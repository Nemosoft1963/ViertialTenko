import sqlite3
import tempfile
from contextlib import closing
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app.daily_report_worker import (
    previous_month_start,
    should_send,
    should_send_monthly,
)
from app.report_mailer import (
    JST,
    MailSettings,
    build_csv_bytes,
    build_message,
    send_report,
    send_monthly_report,
    validate_email,
)


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
    meet_url TEXT,
    scenario_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    alert INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    is_ok INTEGER,
    answered_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ReportMailerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tenko.db"
        with closing(sqlite3.connect(self.db_path)) as con:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT INTO scenarios(id,name,questions_json) "
                "VALUES(1,'標準点呼','[]')"
            )
            cursor = con.execute(
                "INSERT INTO checkins("
                "participant_id,participant_name,meet_url,scenario_id,"
                "joined_at,completed_at,status,alert"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    "12-34",
                    "山田太郎",
                    "https://meet.google.com/test",
                    1,
                    "2026-08-19 15:30:00",
                    "2026-08-19 15:35:00",
                    "completed",
                    0,
                ),
            )
            con.execute(
                "INSERT INTO answers("
                "checkin_id,question_key,question_text,answer_text,is_ok,answered_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    cursor.lastrowid,
                    "license",
                    "運転免許証を携帯していますか？",
                    "はい",
                    1,
                    "2026-08-19 15:34:00",
                ),
            )
            con.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builds_previous_day_csv_in_japan_time(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            data, count = build_csv_bytes(con, date(2026, 8, 20))
        text = data.decode("utf-8-sig")
        self.assertEqual(count, 1)
        self.assertIn("車番,氏名", text)
        self.assertIn("12-34,山田太郎", text)
        self.assertIn("2026-08-20 00:30:00", text)
        self.assertIn("運転免許証を携帯していますか？,はい,正常", text)


    def test_builds_full_month_csv(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            data, count = build_csv_bytes(
                con,
                date(2026, 8, 1),
                date(2026, 9, 1),
            )
        self.assertEqual(count, 1)
        self.assertIn("12-34,山田太郎", data.decode("utf-8-sig"))
    def test_empty_day_still_has_csv_header(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            data, count = build_csv_bytes(con, date(2026, 8, 19))
        self.assertEqual(count, 0)
        self.assertTrue(data.decode("utf-8-sig").startswith("点呼ID,開始日時"))

    def test_rejects_consecutive_dot_address(self):
        self.assertFalse(validate_email("tenko@example..com"))
        self.assertTrue(validate_email("tenko@example.com"))

    def test_message_has_csv_attachment(self):
        settings = MailSettings(
            "smtp.example.com",
            465,
            "user@example.com",
            "secret",
            "user@example.com",
            "recipient@example.com",
        )
        message = build_message(settings, date(2026, 8, 20), b"csv", 1)
        self.assertEqual(message["To"], "recipient@example.com")
        attachment = next(message.iter_attachments())
        self.assertEqual(
            attachment.get_filename(),
            "tenko-report-2026-08-20.csv",
        )

    def test_send_report_uses_smtp_ssl(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            values = {
                "smtp_host": "smtp.example.com",
                "smtp_port": "465",
                "smtp_user": "user@example.com",
                "smtp_password": "secret",
                "smtp_sender": "user@example.com",
                "report_recipient": "recipient@example.com",
            }
            con.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                values.items(),
            )
            con.commit()
        with patch("app.report_mailer.smtplib.SMTP_SSL") as smtp_ssl:
            smtp = smtp_ssl.return_value.__enter__.return_value
            result = send_report(self.db_path, date(2026, 8, 20))
        smtp.login.assert_called_once_with("user@example.com", "secret")
        smtp.send_message.assert_called_once()
        self.assertEqual(result["checkin_count"], 1)

    def test_scheduler_runs_once_during_one_oclock_hour(self):
        now = datetime(2026, 8, 21, 1, 0, tzinfo=JST)
        self.assertTrue(should_send(now, True, ""))
        self.assertFalse(should_send(now, True, "2026-08-20"))
        self.assertFalse(should_send(now.replace(hour=2), True, ""))
        self.assertFalse(should_send(now, False, ""))


    def test_monthly_scheduler_runs_first_day_at_two(self):
        now = datetime(2026, 8, 1, 2, 0, tzinfo=JST)
        self.assertEqual(previous_month_start(now), date(2026, 7, 1))
        self.assertTrue(should_send_monthly(now, True, ""))
        self.assertFalse(should_send_monthly(now, True, "2026-07"))
        self.assertFalse(should_send_monthly(now.replace(day=2), True, ""))
        self.assertFalse(should_send_monthly(now.replace(hour=1), True, ""))
        january = datetime(2026, 1, 1, 2, 0, tzinfo=JST)
        self.assertEqual(previous_month_start(january), date(2025, 12, 1))

    def test_send_monthly_report_uses_month_filename(self):
        with closing(sqlite3.connect(self.db_path)) as con:
            values = {
                "smtp_host": "smtp.example.com",
                "smtp_port": "465",
                "smtp_user": "user@example.com",
                "smtp_password": "secret",
                "smtp_sender": "user@example.com",
                "report_recipient": "recipient@example.com",
            }
            con.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                values.items(),
            )
            con.commit()
        with patch("app.report_mailer.smtplib.SMTP_SSL") as smtp_ssl:
            smtp = smtp_ssl.return_value.__enter__.return_value
            result = send_monthly_report(self.db_path, date(2026, 8, 1))
        message = smtp.send_message.call_args.args[0]
        attachment = next(message.iter_attachments())
        self.assertEqual(result["report_month"], "2026-08")
        self.assertEqual(result["checkin_count"], 1)
        self.assertEqual(attachment.get_filename(), "tenko-report-2026-08.csv")
        self.assertIn("月次点呼記録", str(message["Subject"]))


if __name__ == "__main__":
    unittest.main()
