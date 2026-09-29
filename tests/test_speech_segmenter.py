import unittest

from app.meet_response_worker_v2 import SpeechSegmenter, VAD_BLOCK_BYTES


SPEECH = (1200).to_bytes(2, "little", signed=True) * (VAD_BLOCK_BYTES // 2)
SILENCE = bytes(VAD_BLOCK_BYTES)


class SpeechSegmenterTests(unittest.TestCase):
    def test_vehicle_number_waits_for_700ms_silence(self):
        segmenter = SpeechSegmenter()
        self.assertIsNone(segmenter.feed(SPEECH, "awaiting_id"))
        self.assertIsNone(segmenter.feed(SPEECH, "awaiting_id"))
        for _ in range(6):
            self.assertIsNone(segmenter.feed(SILENCE, "awaiting_id"))
        self.assertIsNotNone(segmenter.feed(SILENCE, "awaiting_id"))

    def test_yes_no_finishes_after_500ms_sample_boundary(self):
        segmenter = SpeechSegmenter()
        segmenter.feed(SPEECH, "in_progress")
        segmenter.feed(SPEECH, "in_progress")
        for _ in range(4):
            self.assertIsNone(segmenter.feed(SILENCE, "in_progress"))
        self.assertIsNotNone(segmenter.feed(SILENCE, "in_progress"))

    def test_silence_without_speech_is_ignored(self):
        segmenter = SpeechSegmenter()
        for _ in range(20):
            self.assertIsNone(segmenter.feed(SILENCE, "awaiting_id"))
        self.assertEqual(segmenter.buffer, bytearray())


if __name__ == "__main__":
    unittest.main()
