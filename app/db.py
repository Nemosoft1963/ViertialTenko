import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DATABASE_PATH", "/data/tenko.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, questions_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS checkins (
 id INTEGER PRIMARY KEY AUTOINCREMENT, participant_id TEXT NOT NULL, participant_name TEXT,
 meet_participant_id TEXT NOT NULL DEFAULT '',
 meet_url TEXT, scenario_id INTEGER NOT NULL, joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT, status TEXT NOT NULL DEFAULT 'in_progress', alert INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(scenario_id) REFERENCES scenarios(id)
);
CREATE TABLE IF NOT EXISTS answers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, checkin_id INTEGER NOT NULL, question_key TEXT NOT NULL,
 question_text TEXT NOT NULL, answer_text TEXT NOT NULL, is_ok INTEGER,
 answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(checkin_id) REFERENCES checkins(id)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS driver_identities (
 vehicle_number TEXT PRIMARY KEY, driver_name TEXT NOT NULL,
 meet_participant_id TEXT NOT NULL DEFAULT '',
 first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 use_count INTEGER NOT NULL DEFAULT 1,
 verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS identity_tombstones (
 vehicle_number TEXT PRIMARY KEY, deleted_at TEXT NOT NULL,
 origin_node TEXT NOT NULL DEFAULT ''
);CREATE TABLE IF NOT EXISTS company_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, checkin_id INTEGER NOT NULL UNIQUE,
 original_text TEXT NOT NULL, summary_text TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT 'normal',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(checkin_id) REFERENCES checkins(id)
);
CREATE INDEX IF NOT EXISTS idx_company_messages_category ON company_messages(category, created_at);
CREATE TABLE IF NOT EXISTS recordings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, checkin_id INTEGER NOT NULL,
 file_path TEXT NOT NULL UNIQUE, transcript TEXT NOT NULL DEFAULT '',
 duration_seconds REAL NOT NULL, file_size INTEGER NOT NULL,
 recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(checkin_id) REFERENCES checkins(id)
);
CREATE INDEX IF NOT EXISTS idx_recordings_checkin ON recordings(checkin_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_recordings_recorded_at ON recordings(recorded_at);
CREATE TABLE IF NOT EXISTS participant_queue (
 id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL,
 meet_participant_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'waiting',
 joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, checkin_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_participant_queue_status ON participant_queue(status,id);
"""

@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
        checkin_columns = {row[1] for row in con.execute("PRAGMA table_info(checkins)")}
        if "meet_participant_id" not in checkin_columns:
            con.execute("ALTER TABLE checkins ADD COLUMN meet_participant_id TEXT NOT NULL DEFAULT ''")
        identity_columns = {row[1] for row in con.execute("PRAGMA table_info(driver_identities)")}
        if "meet_participant_id" not in identity_columns:
            con.execute("ALTER TABLE driver_identities ADD COLUMN meet_participant_id TEXT NOT NULL DEFAULT ''")
        if "verified" not in identity_columns:
            con.execute("ALTER TABLE driver_identities ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
        history = con.execute(
            "SELECT participant_id,participant_name,MAX(joined_at),COUNT(*) FROM checkins "
            "WHERE participant_id NOT IN ('確認待ち','in_progress') AND status='completed' "
            "AND participant_name IS NOT NULL AND TRIM(participant_name) NOT IN ('','Meet participant') "
            "AND participant_name NOT LIKE '%お願い%' AND participant_name NOT LIKE '%数字%' "
            "AND participant_name NOT LIKE '%書き起こ%' AND participant_name NOT LIKE '%もう一度%' "
            "AND participant_name NOT LIKE '%関東ロジ管理%' "
            "GROUP BY participant_id,participant_name HAVING COUNT(*) >= 2"
        ).fetchall()
        for vehicle_number, driver_name, last_seen_at, use_count in history:
            con.execute(
                "INSERT INTO driver_identities(vehicle_number,driver_name,last_seen_at,use_count,verified) VALUES(?,?,?,?,1) "
                "ON CONFLICT(vehicle_number) DO UPDATE SET "
                "driver_name=excluded.driver_name,last_seen_at=excluded.last_seen_at," 
                "use_count=MAX(driver_identities.use_count,excluded.use_count),verified=1",
                (vehicle_number, driver_name, last_seen_at, use_count),
            )
        count = con.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0]
        if not count:
            questions = [
                {"key": "license", "text": "運転免許証を携帯していますか？", "expected": "yes"},
                {"key": "health", "text": "体調に問題はありませんか？", "expected": "yes"},
            ]
            con.execute("INSERT INTO scenarios(name, questions_json) VALUES (?, ?)", ("標準点呼", json.dumps(questions, ensure_ascii=False)))
