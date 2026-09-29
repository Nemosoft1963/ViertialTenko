import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import health_monitor as monitor


class HealthMonitorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "recovery-state.json"
        self.snapshot = {
            "issues": [{"code": "participant_worker_stale", "severity": "warning"}],
            "components": {"participant_worker": {"detail": ""}, "gpu_pipeline": {"error": ""}},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_maps_database_locks_to_the_affected_workers(self):
        snapshot = {
            "issues": [],
            "components": {
                "participant_worker": {"detail": "OperationalError: database is locked"},
                "gpu_pipeline": {"state": "error", "error": "OperationalError: database is locked"},
            },
        }
        self.assertEqual(
            monitor.recovery_targets(snapshot),
            {"meet-bot": "SQLiteロック", "gpu-avatar-worker": "SQLiteロック"},
        )

    def test_ignores_stale_gpu_error_when_pipeline_is_not_failed(self):
        snapshot = {
            "issues": [],
            "components": {
                "participant_worker": {"detail": ""},
                "gpu_pipeline": {"state": "standby", "error": "OperationalError: database is locked"},
            },
        }
        self.assertEqual(monitor.recovery_targets(snapshot), {})

    def test_restarts_only_after_threshold_and_honors_cooldown(self):
        restarted = []
        published = []
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        with patch.object(monitor, "RECOVERY_STATE", self.state), patch.object(
            monitor, "FAILURE_THRESHOLD", 3
        ), patch.object(monitor, "RECOVERY_COOLDOWN", 600), patch.object(
            monitor, "publish_recovery", published.append
        ):
            for offset in range(2):
                self.assertEqual(monitor.auto_recover(self.snapshot, now + timedelta(seconds=offset), restarted.append), [])
            actions = monitor.auto_recover(self.snapshot, now + timedelta(seconds=2), restarted.append)
            self.assertEqual(restarted, ["meet-browser"])
            self.assertEqual(actions[0]["result"], "success")
            for offset in range(3, 7):
                monitor.auto_recover(self.snapshot, now + timedelta(seconds=offset), restarted.append)
            self.assertEqual(restarted, ["meet-browser"])
            self.assertEqual(len(published), 1)


if __name__ == "__main__":
    unittest.main()
