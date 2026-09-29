import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import cluster_coordinator as cluster

SCHEMA = """
CREATE TABLE checkins(
 id INTEGER PRIMARY KEY,participant_id TEXT,participant_name TEXT,meet_participant_id TEXT,
 meet_url TEXT,scenario_id INTEGER,joined_at TEXT,completed_at TEXT,status TEXT,alert INTEGER,
 cluster_origin_node TEXT,cluster_origin_id TEXT);
CREATE TABLE answers(id INTEGER PRIMARY KEY,checkin_id INTEGER,question_key TEXT,question_text TEXT,answer_text TEXT,is_ok INTEGER,answered_at TEXT);
CREATE TABLE company_messages(id INTEGER PRIMARY KEY,checkin_id INTEGER,original_text TEXT,summary_text TEXT,category TEXT,created_at TEXT);
"""

class ClusterSyncAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.shared = Path(self.tmp.name)
        (self.shared / "results" / "primary-pc").mkdir(parents=True)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT INTO checkins VALUES(1,'12-34','山田','meet-1','https://meet',1,'2026-09-01','2026-09-01','completed',0,'primary-pc','1')")
        self.con.execute("INSERT INTO answers VALUES(1,1,'health','体調','はい',1,'2026-09-01')")
        self.con.execute("INSERT INTO company_messages VALUES(1,1,'なし','なし','normal','2026-09-01')")
        self.payload = {
            "schema": 1, "origin_node": "primary-pc", "origin_id": "1",
            "checkin": {"participant_id":"12-34","participant_name":"山田","meet_participant_id":"meet-1","meet_url":"https://meet","scenario_id":1,"joined_at":"2026-09-01","completed_at":"2026-09-01","status":"completed","alert":0},
            "answers": [{"question_key":"health","question_text":"体調","answer_text":"はい","is_ok":1,"answered_at":"2026-09-01"}],
            "company_message": {"original_text":"なし","summary_text":"なし","category":"normal","created_at":"2026-09-01"},
        }
        (self.shared / "results" / "primary-pc" / "1.json").write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def audit(self):
        with patch.object(cluster, "SHARED", self.shared):
            return cluster.audit_results(self.con)

    def test_complete_match(self):
        result = self.audit()
        self.assertEqual((result["source_total"], result["verified"], result["missing"], result["mismatch"], result["corrupt"]), (1,1,0,0,0))

    def test_missing_local_result(self):
        self.con.execute("DELETE FROM company_messages")
        self.con.execute("DELETE FROM answers")
        self.con.execute("DELETE FROM checkins")
        result = self.audit()
        self.assertEqual((result["verified"], result["missing"]), (0,1))

    def test_mismatch_is_detected(self):
        self.con.execute("UPDATE answers SET answer_text='いいえ'")
        result = self.audit()
        self.assertEqual((result["verified"], result["mismatch"]), (0,1))

    def test_corrupt_json_is_detected(self):
        (self.shared / "results" / "primary-pc" / "bad.json").write_text("{broken", encoding="utf-8")
        result = self.audit()
        self.assertEqual((result["source_total"], result["verified"], result["corrupt"]), (2,1,1))

class IdentitySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.shared = Path(self.tmp.name)
        (self.shared / "identities").mkdir()
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript("""
        CREATE TABLE driver_identities(vehicle_number TEXT PRIMARY KEY,driver_name TEXT NOT NULL,meet_participant_id TEXT NOT NULL DEFAULT '',first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,use_count INTEGER NOT NULL,verified INTEGER NOT NULL);
        CREATE TABLE identity_tombstones(vehicle_number TEXT PRIMARY KEY,deleted_at TEXT NOT NULL,origin_node TEXT NOT NULL DEFAULT '');
        """)
    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()
    def write_snapshot(self, identities=None, tombstones=None):
        payload={"schema":1,"node_id":"primary-pc","timestamp":"2026-09-02T00:00:00+00:00","identities":identities or [],"tombstones":tombstones or []}
        (self.shared/"identities"/"primary-pc.json").write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
    def sync(self):
        with patch.object(cluster,"SHARED",self.shared):
            return cluster.sync_identities(self.con)
    def test_identity_is_imported_and_verified(self):
        self.write_snapshot([{"vehicle_number":"12-34","driver_name":"山田","meet_participant_id":"meet-1","first_seen_at":"2026-09-01","last_seen_at":"2026-09-02","use_count":3,"verified":1}])
        changed,audit=self.sync()
        self.assertEqual(changed,1)
        self.assertEqual(self.con.execute("SELECT driver_name FROM driver_identities WHERE vehicle_number='12-34'").fetchone()[0],"山田")
        self.assertEqual((audit["expected"],audit["verified"],audit["missing"],audit["mismatch"]),(1,1,0,0))
    def test_newer_verified_identity_wins(self):
        self.con.execute("INSERT INTO driver_identities VALUES('12-34','旧名','','2026-08-01','2026-08-01',1,0)")
        self.write_snapshot([{"vehicle_number":"12-34","driver_name":"新名","meet_participant_id":"meet-1","first_seen_at":"2026-08-01","last_seen_at":"2026-09-02","use_count":4,"verified":1}])
        self.sync()
        self.assertEqual(tuple(self.con.execute("SELECT driver_name,verified FROM driver_identities WHERE vehicle_number='12-34'").fetchone()),("新名",1))
    def test_tombstone_deletes_old_identity(self):
        self.con.execute("INSERT INTO driver_identities VALUES('12-34','山田','','2026-08-01','2026-09-01',1,1)")
        self.write_snapshot([], [{"vehicle_number":"12-34","deleted_at":"2026-09-02","origin_node":"primary-pc"}])
        changed,audit=self.sync()
        self.assertEqual(changed,1)
        self.assertIsNone(self.con.execute("SELECT 1 FROM driver_identities WHERE vehicle_number='12-34'").fetchone())
        self.assertEqual((audit["expected"],audit["extra"],audit["deleted"]),(0,0,1))
class ConfigurationSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.shared = Path(self.tmp.name)
        (self.shared / "configuration").mkdir()
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript("""
        CREATE TABLE scenarios(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,questions_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO scenarios(id,name,questions_json,created_at,updated_at) VALUES(99,'旧シナリオ','[]','2026-01-01','2026-01-01');
        INSERT INTO settings VALUES('smtp_password','local-secret');
        """)
    def tearDown(self):
        self.con.close();self.tmp.cleanup()
    def write_snapshot(self, questions='[{"key":"health","text":"体調は？"}]'):
        payload={"schema":1,"node_id":"primary-pc","timestamp":"2026-09-02T00:00:00+00:00","scenarios":[{"name":"標準点呼","questions_json":questions,"created_at":"2026-09-01","updated_at":"2026-09-02"}],"settings":{"meet_url":"https://meet.example/test","bot_display_name":"点呼BOT","meet_bot_account_name":"bot@example.test","operation_mode":"realtime"},"active_scenario_name":"標準点呼","smtp_password":"remote-secret"}
        (self.shared/"configuration"/"primary-pc.json").write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
    def sync(self):
        with patch.object(cluster,"SHARED",self.shared):return cluster.sync_configuration(self.con,"primary-pc")
    def test_imports_scenario_settings_and_maps_active_name(self):
        self.write_snapshot();changed,audit=self.sync()
        row=self.con.execute("SELECT id,questions_json FROM scenarios WHERE name='標準点呼'").fetchone()
        self.assertNotEqual(row[0],99)
        self.assertEqual(self.con.execute("SELECT value FROM settings WHERE key='active_scenario_id'").fetchone()[0],str(row[0]))
        self.assertEqual((audit["expected"],audit["verified"],audit["missing"],audit["mismatch"]),(1,1,0,0))
        self.assertEqual(audit["settings_verified"],5)
    def test_changed_questions_are_overwritten_without_changing_id(self):
        self.con.execute("INSERT INTO scenarios VALUES(7,'標準点呼','[]','2026-01-01','2026-01-01')")
        self.write_snapshot('[{"key":"vehicle","text":"車番は？"}]');self.sync()
        row=self.con.execute("SELECT id,questions_json FROM scenarios WHERE name='標準点呼'").fetchone()
        self.assertEqual(row[0],7);self.assertIn('vehicle',row[1])
    def test_secrets_are_not_synchronized(self):
        self.write_snapshot();self.sync()
        self.assertEqual(self.con.execute("SELECT value FROM settings WHERE key='smtp_password'").fetchone()[0],'local-secret')
        self.assertIsNone(self.con.execute("SELECT value FROM settings WHERE key='remote-secret'").fetchone())
    def test_missing_leader_snapshot_is_reported(self):
        with patch.object(cluster,"SHARED",self.shared):
            changed,audit=cluster.sync_configuration(self.con,"backup-pc")
        self.assertEqual(changed,0);self.assertEqual(audit["state"],"awaiting_leader_snapshot")
if __name__ == "__main__":
    unittest.main()
