import unittest

from app.logistics_speech import hotwords_for_status, normalize_logistics_terms


class LogisticsSpeechTests(unittest.TestCase):
    def test_company_message_uses_logistics_vocabulary(self):
        words = hotwords_for_status("awaiting_company_message")
        self.assertIn("テールランプ", words)
        self.assertIn("運行管理者", words)
        self.assertIn("フロントガラス", words)
        self.assertIn("ひび", words)

    def test_dynamic_terms_are_limited_and_included(self):
        words = hotwords_for_status("awaiting_name", ["田中一郎", "品川1234"])
        self.assertIn("田中一郎", words)
        self.assertLessEqual(len(words), 1200)

    def test_normalization_is_conservative_and_auditable(self):
        text, changes = normalize_logistics_terms("テールナンプが切れています")
        self.assertEqual(text, "テールランプが切れています")
        self.assertEqual(changes, [{"from": "テールナンプ", "to": "テールランプ"}])

    def test_windscreen_homophone_is_corrected_only_in_vehicle_context(self):
        text, changes = normalize_logistics_terms("フロントガラスに日々が入ってしまいました")
        self.assertEqual(text, "フロントガラスにひびが入ってしまいました")
        self.assertEqual(changes, [{"from": "フロントガラスの日々", "to": "フロントガラスのひび"}])

        unrelated, changes = normalize_logistics_terms("日々安全運転をしています")
        self.assertEqual(unrelated, "日々安全運転をしています")
        self.assertEqual(changes, [])
