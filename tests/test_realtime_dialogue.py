import json
import sqlite3
import unittest

from app.meet_response_worker_v2 import handle_transcript
from app.realtime_dialogue import is_realtime


SCHEMA = """
CREATE TABLE scenarios (id INTEGER PRIMARY KEY, name TEXT, questions_json TEXT);
CREATE TABLE checkins (id INTEGER PRIMARY KEY AUTOINCREMENT, participant_id TEXT, participant_name TEXT, meet_participant_id TEXT DEFAULT '', meet_url TEXT, scenario_id INTEGER, joined_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT, status TEXT, alert INTEGER DEFAULT 0);
CREATE TABLE answers (id INTEGER PRIMARY KEY AUTOINCREMENT, checkin_id INTEGER, question_key TEXT, question_text TEXT, answer_text TEXT, is_ok INTEGER, answered_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE driver_identities (vehicle_number TEXT PRIMARY KEY, driver_name TEXT, meet_participant_id TEXT DEFAULT '', first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP, last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP, use_count INTEGER DEFAULT 1, verified INTEGER DEFAULT 0);
CREATE TABLE company_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, checkin_id INTEGER UNIQUE, original_text TEXT, summary_text TEXT, category TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


class RealtimeDialogueTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        questions = [
            {"key": "license", "text": "免許証を携帯していますか？", "expected": "yes"},
            {"key": "health", "text": "体調に問題はありませんか？", "expected": "yes"},
        ]
        self.con.execute("INSERT INTO scenarios VALUES(1,'標準',?)", (json.dumps(questions, ensure_ascii=False),))
        self.con.executemany("INSERT INTO settings VALUES(?,?)", (("active_scenario_id", "1"), ("operation_mode", "realtime")))
        self.checkin_id = self.con.execute(
            "INSERT INTO checkins(participant_id,participant_name,scenario_id,status) VALUES('確認待ち','Meet participant',1,'awaiting_id')"
        ).lastrowid

    def tearDown(self):
        self.con.close()

    def test_gpu_mode_uses_realtime_dialogue(self):
        self.con.execute("UPDATE settings SET value='realtime_gpu' WHERE key='operation_mode'")
        self.assertTrue(is_realtime(self.con))
        active = handle_transcript(self.con, "車番は12-34です")
        self.assertEqual(active, self.checkin_id)
        self.assertIn("お名前", self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0])
    def test_advances_only_after_each_recognized_answer(self):
        active = handle_transcript(self.con, "車番は12-34です")
        self.assertEqual(active, self.checkin_id)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_name")
        self.assertIn("お名前", self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0])

        active = handle_transcript(self.con, "山田太郎です", active)
        self.assertEqual(active, self.checkin_id)
        self.assertIn("免許証", self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0])

        active = handle_transcript(self.con, "はい", active)
        self.assertEqual(active, self.checkin_id)
        self.assertIn("体調", self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0])

        active = handle_transcript(self.con, "いいえ", active)
        self.assertIsNone(active)
        status, alert = self.con.execute("SELECT status,alert FROM checkins").fetchone()
        self.assertEqual(status, "completed")
        self.assertEqual(alert, 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 2)

    def test_natural_mode_greets_known_driver_and_records_company_message_immediately(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("INSERT INTO driver_identities(vehicle_number,driver_name,verified) VALUES('12-34','山田太郎',1)")
        active = handle_transcript(self.con, "車番は12-34です")
        self.assertEqual(active, self.checkin_id)
        status, name = self.con.execute("SELECT status,participant_name FROM checkins").fetchone()
        self.assertEqual((status, name), ("in_progress", "山田太郎"))
        prompt = self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0]
        self.assertIn("山田太郎さん、お疲れ様です", prompt)

        active = handle_transcript(self.con, "はい", active)
        active = handle_transcript(self.con, "はい", active)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_company_message")
        self.assertIn("会社へ伝えて", self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0])
        self.assertEqual(self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_fast'").fetchone()[0], "0")

        active = handle_transcript(self.con, "配送先への到着が30分ほど遅れますと伝えてください", active)
        self.assertIsNone(active)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "completed")
        summary, category = self.con.execute("SELECT summary_text,category FROM company_messages").fetchone()
        self.assertIn("30分", summary)
        self.assertEqual(category, "urgent")
        final_prompt = self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0]
        self.assertEqual(final_prompt, "お伝えします。点呼は以上です。回答を記録しました。ありがとうございました。")
        worker_status = self.con.execute("SELECT value FROM settings WHERE key='participant_worker_status'").fetchone()[0]
        self.assertEqual(worker_status, "checkin_completed")

    def test_natural_mode_remembers_new_driver(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        active = handle_transcript(self.con, "車番は56-78です")
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_name")
        handle_transcript(self.con, "佐藤花子です", active)
        self.assertEqual(
            self.con.execute("SELECT driver_name FROM driver_identities WHERE vehicle_number='56-78'").fetchone()[0],
            "佐藤花子",
        )
    def test_natural_mode_uses_meet_participant_id_for_name(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("UPDATE checkins SET meet_participant_id='meet-user-001' WHERE id=?", (self.checkin_id,))
        self.con.execute(
            "INSERT INTO driver_identities(vehicle_number,driver_name,meet_participant_id,verified) "
            "VALUES('12-34','山田太郎','meet-user-001',1)"
        )
        active = handle_transcript(self.con, "車番は12-34です")
        self.assertEqual(active, self.checkin_id)
        self.assertEqual(
            self.con.execute("SELECT participant_name FROM checkins WHERE id=?", (self.checkin_id,)).fetchone()[0],
            "山田太郎",
        )

    def test_natural_mode_rejects_conflicting_vehicle_and_meet_names(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("UPDATE checkins SET meet_participant_id='meet-user-002' WHERE id=?", (self.checkin_id,))
        self.con.execute(
            "INSERT INTO driver_identities(vehicle_number,driver_name,meet_participant_id,verified) VALUES(?,?,?,1)",
            ('12-34','山田太郎','meet-user-001'),
        )
        self.con.execute(
            "INSERT INTO driver_identities(vehicle_number,driver_name,meet_participant_id,verified) VALUES(?,?,?,1)",
            ('56-78','佐藤花子','meet-user-002'),
        )
        handle_transcript(self.con, "車番は12-34です")
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_name")
    def test_company_message_rejects_bare_yes_and_reprompts(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (self.checkin_id,))
        active = handle_transcript(self.con, "はい", self.checkin_id)
        self.assertEqual(active, self.checkin_id)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_company_message")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM company_messages").fetchone()[0], 0)
        self.assertIn("具体的", self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0])

    def test_company_message_corrects_common_vehicle_term(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (self.checkin_id,))
        handle_transcript(self.con, "程度ダンプの不良があります", self.checkin_id)
        summary = self.con.execute("SELECT summary_text FROM company_messages").fetchone()[0]
        self.assertEqual(summary, "テールランプの不良があります")
        self.assertEqual(self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0], "お伝えします。点呼は以上です。回答を記録しました。ありがとうございました。")

    def test_windscreen_crack_is_normalized_and_flagged_urgent(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (self.checkin_id,))
        handle_transcript(self.con, "フロントガラスに日々が入ってしまいました", self.checkin_id)
        summary, category = self.con.execute("SELECT summary_text,category FROM company_messages").fetchone()
        self.assertEqual(summary, "フロントガラスにひびが入ってしまいました")
        self.assertEqual(category, "urgent")
        self.assertEqual(self.con.execute("SELECT alert FROM checkins").fetchone()[0], 1)
    def test_company_message_completes_without_confirmation(self):
        self.con.execute("UPDATE settings SET value='realtime_natural' WHERE key='operation_mode'")
        self.con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (self.checkin_id,))
        active = handle_transcript(self.con, "到着が遅れます", self.checkin_id)
        self.assertIsNone(active)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "completed")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM company_messages").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0], "お伝えします。点呼は以上です。回答を記録しました。ありがとうございました。")
    def test_rejects_long_enumeration_as_vehicle_number(self):
        active = handle_transcript(self.con, "1、2、3、4、5、6、7、8、9、10")
        self.assertIsNone(active)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_id")

    def test_rejects_conversation_as_driver_name(self):
        self.con.execute("UPDATE checkins SET participant_id='15-84',status='awaiting_name' WHERE id=?", (self.checkin_id,))
        active = handle_transcript(self.con, "もう一度お願いします", self.checkin_id)
        self.assertEqual(active, self.checkin_id)
        self.assertEqual(self.con.execute("SELECT status FROM checkins").fetchone()[0], "awaiting_name")
    def test_unrecognized_answer_repeats_without_recording(self):
        self.con.execute("UPDATE checkins SET status='in_progress' WHERE id=?", (self.checkin_id,))
        active = handle_transcript(self.con, "よく分かりません", self.checkin_id)
        self.assertEqual(active, self.checkin_id)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)
        prompt = self.con.execute("SELECT value FROM settings WHERE key='realtime_prompt_text'").fetchone()[0]
        self.assertIn("はい", prompt)


if __name__ == "__main__":
    unittest.main()
