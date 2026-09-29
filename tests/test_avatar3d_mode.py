import sqlite3
import unittest
from app import realtime_dialogue as dialogue

class Avatar3DModeTests(unittest.TestCase):
    def test_3d_uses_natural_dialogue_without_video_generation(self):
        with sqlite3.connect(":memory:") as con:
            con.execute("create table settings(key text,value text)")
            con.execute("insert into settings values('operation_mode','realtime_3d')")
            self.assertTrue(dialogue.is_realtime(con))
            self.assertTrue(dialogue.is_natural(con))
            self.assertFalse(dialogue.uses_gpu(con))
