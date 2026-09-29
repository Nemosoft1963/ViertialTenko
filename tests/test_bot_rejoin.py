import unittest

from app.meet_participant_worker_v2 import (
    JOIN_LABELS,
    is_in_meeting_text,
    is_waiting_for_admission,
)


class BotRejoinTests(unittest.TestCase):
    def test_detects_japanese_and_english_in_meeting_markers(self):
        self.assertTrue(is_in_meeting_text("\u901a\u8a71\u304b\u3089\u9000\u51fa"))
        self.assertTrue(is_in_meeting_text("Leave call"))
        self.assertFalse(is_in_meeting_text("\u518d\u53c2\u52a0"))

    def test_detects_admission_wait(self):
        self.assertTrue(
            is_waiting_for_admission(
                "\u53c2\u52a0\u30ea\u30af\u30a8\u30b9\u30c8\u3092\u9001\u4fe1\u3057\u307e\u3057\u305f"
            )
        )
        self.assertTrue(is_waiting_for_admission("You've asked to join"))

    def test_rejoin_labels_cover_post_call_and_prejoin_pages(self):
        self.assertIn("\u518d\u53c2\u52a0", JOIN_LABELS)
        self.assertIn("Rejoin", JOIN_LABELS)
        self.assertIn("\u4eca\u3059\u3050\u53c2\u52a0", JOIN_LABELS)


if __name__ == "__main__":
    unittest.main()
