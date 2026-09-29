import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import recordings
from app.daily_report_worker import should_cleanup_recordings


SCHEMA = """
CREATE TABLE checkins (id INTEGER PRIMARY KEY, status TEXT);
CREATE TABLE recordings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 checkin_id INTEGER NOT NULL,
 file_path TEXT NOT NULL UNIQUE,
 transcript TEXT NOT NULL DEFAULT '',
 duration_seconds REAL NOT NULL,
 file_size INTEGER NOT NULL,
 recorded_at TEXT NOT NULL
);
"""


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_root = recordings.RECORDINGS_ROOT
        recordings.RECORDINGS_ROOT = Path(self.temp_dir.name) / "recordings"
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT INTO checkins VALUES(1,'in_progress')")

    def tearDown(self):
        recordings.RECORDINGS_ROOT = self.original_root
        self.con.close()
        self.temp_dir.cleanup()

    def test_saves_wav_and_metadata_for_checkin(self):
        pcm = b"\x01\x00" * 16_000
        recording_id = recordings.save_recording(
            self.con,
            1,
            pcm,
            "はい",
            recorded_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        row = self.con.execute(
            "SELECT file_path,transcript,duration_seconds,file_size FROM recordings WHERE id=?",
            (recording_id,),
        ).fetchone()
        self.assertEqual(row[1], "はい")
        self.assertAlmostEqual(row[2], 1.0)
        path = Path(row[0])
        self.assertTrue(path.is_file())
        self.assertGreater(row[3], len(pcm))
        self.assertEqual(path.parts[-3], "2026-08")

    def test_monthly_cleanup_keeps_exactly_fourteen_calendar_months(self):
        old_path = recordings.RECORDINGS_ROOT / "2025-06" / "checkin-1" / "old.wav"
        keep_path = recordings.RECORDINGS_ROOT / "2025-07" / "checkin-1" / "keep.wav"
        for path in (old_path, keep_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"wav")
        self.con.executemany(
            "INSERT INTO recordings(checkin_id,file_path,transcript,duration_seconds,file_size,recorded_at) VALUES(1,?,'',1,3,?)",
            ((str(old_path), "2025-06-30 23:59:59"), (str(keep_path), "2025-07-01 00:00:00")),
        )
        result = recordings.cleanup_expired_recordings(
            self.con,
            datetime(2026, 8, 1, 3, 0),
            14,
        )
        self.assertEqual(result["cutoff_month"], "2025-07")
        self.assertEqual(result["deleted_records"], 1)
        self.assertFalse(old_path.exists())
        self.assertTrue(keep_path.exists())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recordings").fetchone()[0], 1)

    def test_cleanup_runs_once_on_first_day_at_three(self):
        now = datetime(2026, 8, 1, 3, 0)
        self.assertTrue(should_cleanup_recordings(now, "2026-07"))
        self.assertFalse(should_cleanup_recordings(now, "2026-08"))
        self.assertFalse(should_cleanup_recordings(now.replace(day=2), ""))
        self.assertFalse(should_cleanup_recordings(now.replace(hour=2), ""))


if __name__ == "__main__":
    unittest.main()
