import json
import unittest
from unittest.mock import Mock, patch

from app import openwebui_client


class OpenWebUIClientTests(unittest.TestCase):
    def test_without_key_falls_back_without_network(self):
        with patch.object(openwebui_client, "API_KEY", ""), patch("app.openwebui_client.httpx.post") as post:
            self.assertEqual(openwebui_client.naturalize("体調はどうですか"), "体調はどうですか")
            post.assert_not_called()

    def test_valid_json_reply_is_used(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": json.dumps({"spoken_reply": "お体の調子はいかがですか。"}, ensure_ascii=False)}}
        with patch.object(openwebui_client, "API_KEY", "test"), patch("app.openwebui_client.httpx.post", return_value=response):
            self.assertEqual(openwebui_client.naturalize("体調はどうですか"), "お体の調子はいかがですか。")

    def test_changed_digits_are_rejected(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": '{"spoken_reply":"車番は5678ですね"}'}}
        with patch.object(openwebui_client, "API_KEY", "test"), patch("app.openwebui_client.httpx.post", return_value=response):
            self.assertEqual(openwebui_client.naturalize("車番は1234ですね"), "車番は1234ですね")
