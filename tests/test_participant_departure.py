import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import meet_participant_worker_v2 as participant_worker
from app.meet_response_worker_v2 import checkin_accepts_answers
from app.participant_queue import activate_next_participant, ensure_queue_schema, expire_stale_anonymous_participants


SCHEMA = """
CREATE TABLE scenarios (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    questions_json TEXT NOT NULL
);
CREATE TABLE checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT NOT NULL,
    participant_name TEXT,
    meet_participant_id TEXT NOT NULL DEFAULT '',
    meet_url TEXT,
    scenario_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',
    alert INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    is_ok INTEGER,
    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ParticipantDepartureTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tenko.db"
        with sqlite3.connect(self.db_path) as con:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT INTO scenarios(id,name,questions_json) VALUES(1,'standard','[]')"
            )
            con.execute(
                "INSERT INTO settings(key,value) VALUES('active_scenario_id','1')"
            )
            con.execute(
                "INSERT INTO settings(key,value) VALUES('meet_url','https://meet.google.com/test')"
            )
        self.original_db_path = participant_worker.DB_PATH
        participant_worker.DB_PATH = str(self.db_path)

    def tearDown(self):
        participant_worker.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_departure_closes_checkin_and_allows_next_participant(self):
        checkin_id, created = participant_worker.register_participant("Driver A", "meet-driver-a")
        self.assertTrue(created)

        departed_id = participant_worker.record_participant_departure()
        self.assertEqual(departed_id, checkin_id)

        with sqlite3.connect(self.db_path) as con:
            meet_id = con.execute("SELECT meet_participant_id FROM checkins WHERE id=?", (checkin_id,)).fetchone()[0]
            self.assertEqual(meet_id, "meet-driver-a")
            row = con.execute(
                "SELECT status,alert,completed_at FROM checkins WHERE id=?",
                (checkin_id,),
            ).fetchone()
            self.assertEqual(row[0], "cancelled")
            self.assertEqual(row[1], 1)
            self.assertIsNotNone(row[2])
            event = con.execute(
                "SELECT question_key,question_text,answer_text,is_ok "
                "FROM answers WHERE checkin_id=?",
                (checkin_id,),
            ).fetchone()
            self.assertEqual(event[0], "participant_departure")
            self.assertIn("\u9000\u51fa", event[1])
            self.assertIn("\u65b0\u898f\u5165\u5ba4", event[2])
            self.assertEqual(event[3], 0)
            settings = dict(con.execute("SELECT key,value FROM settings").fetchall())
            self.assertEqual(
                settings["participant_worker_status"], "waiting_for_participant"
            )
            self.assertEqual(
                settings["response_worker_status"], "waiting_for_participant"
            )
            self.assertEqual(settings["response_worker_checkin_id"], "")

        self.assertIsNone(participant_worker.record_participant_departure())
        with sqlite3.connect(self.db_path) as con:
            event_count = con.execute(
                "SELECT COUNT(*) FROM answers WHERE checkin_id=? "
                "AND question_key='participant_departure'",
                (checkin_id,),
            ).fetchone()[0]
        self.assertEqual(event_count, 1)

        next_id, next_created = participant_worker.register_participant("Driver B")
        self.assertTrue(next_created)
        self.assertNotEqual(next_id, checkin_id)

    def test_count_fallback_starts_even_when_previous_count_was_already_two(self):
        self.assertTrue(participant_worker.should_register_from_count(2, False))
        checkin_id, created = participant_worker.register_participant("Meet participant")
        self.assertTrue(created)
        self.assertFalse(participant_worker.should_register_from_count(2, False))
        with sqlite3.connect(self.db_path) as con:
            con.execute("UPDATE checkins SET status='completed' WHERE id=?", (checkin_id,))
        self.assertFalse(participant_worker.should_register_from_count(2, False))
        participant_worker.reset_presence_session()
        self.assertTrue(participant_worker.should_register_from_count(2, False))
        self.assertIsNotNone(checkin_id)
    def test_second_participant_is_queued_and_started_after_first_completes(self):
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "INSERT INTO settings(key,value) VALUES('operation_mode','realtime_natural')"
            )
        first_id, first_created = participant_worker.register_participant(
            "Driver A", "meet-driver-a"
        )
        self.assertTrue(first_created)

        active_id, second_created = participant_worker.register_participant(
            "Driver B", "meet-driver-b"
        )
        self.assertFalse(second_created)
        self.assertEqual(active_id, first_id)

        with sqlite3.connect(self.db_path) as con:
            queued = con.execute(
                "SELECT display_name,meet_participant_id,status "
                "FROM participant_queue ORDER BY id"
            ).fetchone()
            self.assertEqual(queued, ("Driver B", "meet-driver-b", "waiting"))
            con.execute(
                "UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (first_id,),
            )
            next_id, started = activate_next_participant(
                con, "Driver Aさんの点呼は終了しました。"
            )
            self.assertTrue(started)
            self.assertNotEqual(next_id, first_id)
            next_checkin = con.execute(
                "SELECT participant_name,meet_participant_id,status FROM checkins WHERE id=?",
                (next_id,),
            ).fetchone()
            self.assertEqual(
                next_checkin, ("Driver B", "meet-driver-b", "awaiting_id")
            )
            prompt = con.execute(
                "SELECT value FROM settings WHERE key='realtime_prompt_text'"
            ).fetchone()[0]
            self.assertIn("Driver Bさん", prompt)
    def test_multiple_anonymous_participants_can_wait_in_order(self):
        first_id, created = participant_worker.register_participant(
            "Meet participant", "count-fallback:first"
        )
        self.assertTrue(created)
        participant_worker.register_participant(
            "Meet participant", "count-fallback:second"
        )
        participant_worker.register_participant(
            "Meet participant", "count-fallback:third"
        )
        with sqlite3.connect(self.db_path) as con:
            queued = con.execute(
                "SELECT meet_participant_id,status FROM participant_queue ORDER BY id"
            ).fetchall()
        self.assertEqual(
            queued,
            [
                ("count-fallback:second", "waiting"),
                ("count-fallback:third", "waiting"),
            ],
        )
        self.assertIsNotNone(first_id)
    def test_bot_join_notification_is_ignored(self):
        with sqlite3.connect(self.db_path) as con:
            con.execute("INSERT INTO settings(key,value) VALUES('bot_display_name','関東ロジ管理')")
        checkin_id, created = participant_worker.register_participant("関東ロジ管理", "bot-id")
        self.assertIsNone(checkin_id)
        self.assertFalse(created)
        with sqlite3.connect(self.db_path) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM checkins").fetchone()[0], 0)
    def test_cancelled_checkin_rejects_further_answers(self):
        checkin_id, _ = participant_worker.register_participant("Driver A")
        with sqlite3.connect(self.db_path) as con:
            self.assertTrue(checkin_accepts_answers(con, checkin_id))

        participant_worker.record_participant_departure()

        with sqlite3.connect(self.db_path) as con:
            self.assertFalse(checkin_accepts_answers(con, checkin_id))

    def test_departure_releases_prompt_for_cancelled_checkin(self):
        checkin_id, _ = participant_worker.register_participant("Driver A")
        with sqlite3.connect(self.db_path) as con:
            con.execute("INSERT INTO settings(key,value) VALUES('realtime_prompt_id','9')")
            con.execute("INSERT INTO settings(key,value) VALUES('realtime_prompt_checkin_id',?)", (str(checkin_id),))
            con.execute("INSERT INTO settings(key,value) VALUES('realtime_prompt_state','synthesizing')")
            con.execute("INSERT INTO settings(key,value) VALUES('realtime_prompt_playing','1')")
        participant_worker.record_participant_departure()
        with sqlite3.connect(self.db_path) as con:
            values = dict(con.execute("SELECT key,value FROM settings WHERE key IN ('realtime_prompt_played_id','realtime_prompt_state','realtime_prompt_playing')"))
        self.assertEqual(values, {'realtime_prompt_played_id': '9', 'realtime_prompt_state': 'played', 'realtime_prompt_playing': '0'})

    def test_expire_only_old_anonymous_waiting_entries(self):
        with sqlite3.connect(self.db_path) as con:
            ensure_queue_schema(con)
            con.execute("INSERT INTO participant_queue(display_name,meet_participant_id,status,joined_at) VALUES('Meet participant','count-fallback:old','waiting',datetime('now','-20 minutes'))")
            con.execute("INSERT INTO participant_queue(display_name,meet_participant_id,status,joined_at) VALUES('Driver B','meet-driver-b','waiting',datetime('now','-20 minutes'))")
            self.assertEqual(expire_stale_anonymous_participants(con, 15), 1)
            states = dict(con.execute("SELECT meet_participant_id,status FROM participant_queue"))
        self.assertEqual(states['count-fallback:old'], 'expired')
        self.assertEqual(states['meet-driver-b'], 'waiting')


if __name__ == "__main__":
    unittest.main()

