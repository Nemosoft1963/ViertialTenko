"""Fail-fast Open WebUI client. The LLM may phrase prompts but never drives state."""

import json
import os

import httpx

MODE_OPENWEBUI = "realtime_openwebui"
BASE_URL = os.getenv("OPENWEBUI_BASE_URL", "http://open-webui:8080").rstrip("/")
API_KEY = os.getenv("OPENWEBUI_API_KEY", "")
MODEL = os.getenv("OPENWEBUI_MODEL", os.getenv("LOCAL_LLM_MODEL", "qwen3:8b"))
TIMEOUT = max(0.3, min(5.0, float(os.getenv("OPENWEBUI_TIMEOUT_SECONDS", "1.8"))))

SYSTEM_PROMPT = """あなたは運送会社の点呼担当者です。入力された原文の意味、質問順、数字、氏名を変えず、短く自然な日本語に整えてください。
新しい質問や情報を追加せず、点呼完了を独自判断しないでください。返答はJSONのみで {\"spoken_reply\":\"...\"} としてください。"""


def status():
    if not API_KEY:
        return {"ready": False, "detail": "OPENWEBUI_API_KEY未設定", "model": MODEL}
    try:
        response = httpx.get(
            f"{BASE_URL}/ollama/api/tags",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        names = {item.get("name") for item in response.json().get("models", [])}
        if MODEL not in names:
            return {"ready": False, "detail": f"モデル未取得: {MODEL}", "model": MODEL}
        return {"ready": True, "detail": "接続済み", "model": MODEL}
    except httpx.HTTPError as exc:
        return {"ready": False, "detail": f"{type(exc).__name__}: {exc}"[:240], "model": MODEL}


def naturalize(text, status="", context=None):
    if not API_KEY or not text:
        return text
    payload = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"status": status, "original": text, "context": context or {}}, ensure_ascii=False)},
        ],
        "format": "json",
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 120},
    }
    try:
        response = httpx.post(
            f"{BASE_URL}/ollama/api/chat",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=payload,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").removeprefix("json").strip()
        reply = json.loads(content).get("spoken_reply", "").strip()
        if not reply or len(reply) > 240:
            return text
        # Never allow the model to lose vehicle-number digits present in the source.
        source_digits = "".join(c for c in text if c.isdigit())
        reply_digits = "".join(c for c in reply if c.isdigit())
        if source_digits and source_digits != reply_digits:
            return text
        return reply
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return text


def naturalize_pending_prompt(db_path, expected_checkin_id=None):
    import sqlite3

    with sqlite3.connect(db_path, timeout=1) as con:
        values = dict(con.execute(
            "SELECT key,value FROM settings WHERE key IN "
            "('operation_mode','realtime_prompt_id','realtime_prompt_text',"
            "'realtime_prompt_status_after','realtime_prompt_checkin_id','realtime_prompt_state')"
        ).fetchall())
    if values.get("operation_mode") != MODE_OPENWEBUI or values.get("realtime_prompt_state") != "queued":
        return False
    checkin_id = values.get("realtime_prompt_checkin_id", "")
    if expected_checkin_id is not None and checkin_id != str(expected_checkin_id):
        return False
    original = values.get("realtime_prompt_text", "")
    reply = naturalize(original, values.get("realtime_prompt_status_after", ""), {"checkin_id": checkin_id})
    if reply == original:
        return False
    with sqlite3.connect(db_path, timeout=1) as con:
        cursor = con.execute(
            "UPDATE settings SET value=? WHERE key='realtime_prompt_text' "
            "AND EXISTS(SELECT 1 FROM settings WHERE key='realtime_prompt_id' AND value=?) "
            "AND EXISTS(SELECT 1 FROM settings WHERE key='realtime_prompt_state' AND value='queued')",
            (reply, values.get("realtime_prompt_id", "")),
        )
    return cursor.rowcount == 1
