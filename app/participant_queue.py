import sqlite3

try:
    from app.realtime_dialogue import INTRO_PROMPT, is_realtime, queue_prompt, setting
except ImportError:
    from realtime_dialogue import INTRO_PROMPT, is_realtime, queue_prompt, setting

ACTIVE_STATUSES = (
    "awaiting_id",
    "awaiting_name",
    "in_progress",
    "awaiting_company_message",
    "awaiting_company_message_confirmation",
)
ANONYMOUS_QUEUE_TTL_MINUTES = 15


def ensure_queue_schema(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS participant_queue ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "display_name TEXT NOT NULL,"
        "meet_participant_id TEXT NOT NULL DEFAULT '',"
        "status TEXT NOT NULL DEFAULT 'waiting',"
        "joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "started_at TEXT,checkin_id INTEGER)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_participant_queue_status "
        "ON participant_queue(status,id)"
    )


def expire_stale_anonymous_participants(con, ttl_minutes=ANONYMOUS_QUEUE_TTL_MINUTES):
    ensure_queue_schema(con)
    return con.execute(
        "UPDATE participant_queue SET status='expired' "
        "WHERE status='waiting' AND meet_participant_id LIKE 'count-fallback:%' "
        "AND joined_at < datetime('now', ?)",
        (f"-{int(ttl_minutes)} minutes",),
    ).rowcount


def target_intro(display_name, waiting_count=0):
    name = (display_name or "").strip()
    if name and name != "Meet participant":
        prefix = f"{name}さん、点呼を始めます。"
    else:
        prefix = "次の参加者の方、点呼を始めます。"
    if waiting_count:
        prefix += "ほかの方は、順番にお呼びしますので、そのままお待ちください。"
    return f"{prefix} {INTRO_PROMPT}".strip()


def enqueue_participant(con, display_name, meet_participant_id=""):
    ensure_queue_schema(con)
    expire_stale_anonymous_participants(con)
    name = (display_name or "Meet participant").strip()[:120]
    meet_id = (meet_participant_id or "").strip()[:200]
    if meet_id:
        duplicate = con.execute(
            "SELECT id FROM participant_queue WHERE meet_participant_id=? "
            "AND status IN ('waiting','active') ORDER BY id DESC LIMIT 1",
            (meet_id,),
        ).fetchone()
        if duplicate:
            return duplicate[0], False
    elif name != "Meet participant":
        duplicate = con.execute(
            "SELECT id FROM participant_queue WHERE display_name=? "
            "AND status='waiting' ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        if duplicate:
            return duplicate[0], False
    cursor = con.execute(
        "INSERT INTO participant_queue(display_name,meet_participant_id,status) "
        "VALUES(?,?,'waiting')",
        (name, meet_id),
    )
    return cursor.lastrowid, True


def waiting_count(con):
    ensure_queue_schema(con)
    expire_stale_anonymous_participants(con)
    return con.execute(
        "SELECT COUNT(*) FROM participant_queue WHERE status='waiting'"
    ).fetchone()[0]


def activate_next_participant(con, completion_text=""):
    ensure_queue_schema(con)
    expire_stale_anonymous_participants(con)
    active = con.execute(
        "SELECT id FROM checkins WHERE status IN (?,?,?,?,?) LIMIT 1",
        ACTIVE_STATUSES,
    ).fetchone()
    if active:
        return active[0], False
    queued = con.execute(
        "SELECT id,display_name,meet_participant_id FROM participant_queue "
        "WHERE status='waiting' ORDER BY id LIMIT 1"
    ).fetchone()
    if not queued:
        return None, False
    queue_id, display_name, meet_id = queued
    scenario_id = int(setting(con, "active_scenario_id", "1"))
    cursor = con.execute(
        "INSERT INTO checkins(participant_id,participant_name,meet_participant_id,meet_url,scenario_id,status) "
        "VALUES(?,?,?,?,?,'awaiting_id')",
        ("確認待ち", display_name, meet_id, setting(con, "meet_url"), scenario_id),
    )
    checkin_id = cursor.lastrowid
    con.execute(
        "UPDATE participant_queue SET status='active',started_at=CURRENT_TIMESTAMP,checkin_id=? WHERE id=?",
        (checkin_id, queue_id),
    )
    con.execute(
        "UPDATE participant_queue SET status='completed' WHERE status='active' AND id<>?",
        (queue_id,),
    )
    remaining = waiting_count(con)
    prompt = target_intro(display_name, remaining)
    if completion_text:
        prompt = f"{completion_text} {prompt}".strip()
    if is_realtime(con):
        queue_prompt(con, prompt, "awaiting_vehicle_number", checkin_id)
    for key, value in {
        "participant_worker_presence_started": "1",
        "participant_worker_status": "checkin_started",
        "participant_worker_detail": f"{display_name} / checkin #{checkin_id} / 待機 {remaining}人",
        "response_worker_status": "awaiting_vehicle_number",
        "response_worker_checkin_id": str(checkin_id),
    }.items():
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    return checkin_id, True


def finish_queue_entry(con, checkin_id):
    ensure_queue_schema(con)
    con.execute(
        "UPDATE participant_queue SET status='completed' WHERE checkin_id=?",
        (checkin_id,),
    )
