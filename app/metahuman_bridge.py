"""Host-local MetaHuman transport. Never substitute a simulated face on failure."""
import base64
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path, data=None, content_type="application/octet-stream"):
    url = os.getenv("METAHUMAN_BRIDGE_URL", "").rstrip("/")
    token = os.getenv("METAHUMAN_BRIDGE_TOKEN", "")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            len(token) < 32):
        raise RuntimeError("MetaHuman bridge is not configured (URL and 32+ character token required)")
    req = Request(url + path, data=data, headers={
        "Authorization": "Bearer " + token, "Content-Type": content_type})
    # Do not leak the host-only credential to redirects or environment proxies.
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=3) as response:
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise RuntimeError("MetaHuman response exceeds limit")
        return payload


def status():
    try:
        value = json.loads(request("/status"))
        ready = value.get("ready") is True and value.get("protocol") == 1
        return {"ready": ready, "state": "connected" if ready else "not_ready",
                "detail": "映像・音声ブリッジ接続済み（人物・Live Linkは目視確認が必要）" if ready else "映像または音声デバイスの準備ができていません"}
    except Exception:
        return {"ready": False, "state": "unavailable", "detail": "未設定または未接続。Unreal・仮想カメラ・音声ループバックを確認してください"}


def frame():
    data = request("/frame")
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("MetaHuman bridge returned invalid JPEG")
    return base64.b64encode(data).decode("ascii")


def audio(encoded):
    if len(encoded) > 24_000_000:
        raise ValueError("Audio exceeds limit")
    data = base64.b64decode(encoded, validate=True)
    result = json.loads(request("/audio", data, "audio/wav"))
    if result.get("accepted") is not True:
        raise RuntimeError("MetaHuman audio was not accepted")
    return result
