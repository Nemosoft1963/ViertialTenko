import json
import os
import sqlite3
import tempfile
import time
import wave
from pathlib import Path

from faster_whisper import WhisperModel


DB_PATH = Path(os.getenv("DATABASE_PATH", "/data/tenko.db"))
AUDIO_PATH = Path(os.getenv("MEET_OUTPUT_PATH", "/meet-config/meet-output.raw"))
MODEL_DIR = Path(os.getenv("WHISPER_MODEL_DIR", "/data/whisper-models"))
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
RATE = 16_000
CHUNK_SECONDS = 5
CHUNK_BYTES = RATE * 2 * CHUNK_SECONDS


def setting(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def update_status(con: sqlite3.Connection, **values: str) -> None:
    for key, value in values.items():
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def active_scenario(con: sqlite3.Connection):
    scenario_id = int(setting(con, "active_scenario_id", "1"))
    row = con.execute("SELECT questions_json FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
    return scenario_id, json.loads(row[0])


def classify(text: str) -> tuple[bool | None, str]:
    compact = text.replace(" ", "")
    negative = ("いいえ", "持っていません", "問題があります", "悪い", "不調")
    positive = ("はい", "持っています", "問題ない", "問題ありません", "大丈夫", "良好")
    if any(word in compact for word in negative):
        return False, text
    if any(word in compact for word in positive):
        return True, text
    return None, text


def transcribe(model: WhisperModel, pcm: bytes) -> str:
    if not pcm or max(pcm) == min(pcm):
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav") as temporary:
        with wave.open(temporary.name, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(pcm)
        segments, _ = model.transcribe(temporary.name, language="ja", vad_filter=True, beam_size=3)
        return "".join(segment.text for segment in segments).strip()


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
    offset = AUDIO_PATH.stat().st_size if AUDIO_PATH.exists() else 0
    checkin_id = None
    question_index = 0

    while True:
        if not AUDIO_PATH.exists():
            time.sleep(1)
            continue
        size = AUDIO_PATH.stat().st_size
        if size < offset:
            offset, checkin_id, question_index = 0, None, 0
        if size - offset < CHUNK_BYTES:
            time.sleep(1)
            continue
        with AUDIO_PATH.open("rb") as audio:
            audio.seek(offset)
            pcm = audio.read(CHUNK_BYTES)
        offset += len(pcm)
        text = transcribe(model, pcm)
        if not text:
            continue

        with sqlite3.connect(DB_PATH, timeout=30) as con:
            scenario_id, questions = active_scenario(con)
            update_status(con, response_worker_status="listening", response_worker_last_text=text)
            if checkin_id is None:
                pending = con.execute(
                    "SELECT id FROM checkins WHERE status='awaiting_id' ORDER BY id LIMIT 1"
                ).fetchone()
                if pending:
                    checkin_id = pending[0]
                    con.execute(
                        "UPDATE checkins SET participant_id=?,status='in_progress' WHERE id=?",
                        (text, checkin_id),
                    )
                else:
                    cursor = con.execute(
                        "INSERT INTO checkins(participant_id,participant_name,meet_url,scenario_id) VALUES(?,?,?,?)",
                        (text, "", setting(con, "meet_url"), scenario_id),
                    )
                    checkin_id = cursor.lastrowid
                question_index = 0
                update_status(con, response_worker_checkin_id=str(checkin_id))
                continue
            if question_index >= len(questions):
                continue
            question = questions[question_index]
            is_ok, answer_text = classify(text)
            con.execute(
                "INSERT INTO answers(checkin_id,question_key,question_text,answer_text,is_ok) VALUES(?,?,?,?,?)",
                (checkin_id, question["key"], question["text"], answer_text, is_ok),
            )
            if is_ok is not True:
                con.execute("UPDATE checkins SET alert=1 WHERE id=?", (checkin_id,))
            question_index += 1
            if question_index == len(questions):
                con.execute(
                    "UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (checkin_id,),
                )
                checkin_id, question_index = None, 0


if __name__ == "__main__":
    main()

