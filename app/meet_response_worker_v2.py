import json
import audioop
import logging
import os
import re
import sqlite3
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from faster_whisper import WhisperModel

from app.logistics_speech import hotwords_for_status, normalize_logistics_terms
from app.openwebui_client import naturalize_pending_prompt
from app.recordings import active_checkin_id, persist_recording, prepare_recording
from app.participant_queue import activate_next_participant, finish_queue_entry
from app.realtime_dialogue import (
    COMPLETE_PROMPT,
    COMPANY_MESSAGE_PROMPT,
    NAME_PROMPT,
    RETRY_ANSWER_PROMPT,
    RETRY_ID_PROMPT,
    RETRY_NAME_PROMPT,
    greeting_prompt,
    is_natural,
    is_realtime,
    next_unanswered_question,
    question_prompt,
    queue_prompt,
)

DB_PATH = Path(os.getenv("DATABASE_PATH", "/data/tenko.db"))
AUDIO_PATH = Path(os.getenv("MEET_OUTPUT_PATH", "/meet-config/meet-output.raw"))
MODEL_DIR = Path(os.getenv("WHISPER_MODEL_DIR", "/data/whisper-models"))
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
MODEL_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
MODEL_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
RATE = 16_000
VAD_BLOCK_MS = max(20, int(os.getenv("VAD_BLOCK_MS", "100")))
VAD_BLOCK_BYTES = int(RATE * 2 * VAD_BLOCK_MS / 1000)
VAD_SPEECH_RMS = max(50, int(os.getenv("VAD_SPEECH_RMS", "350")))
VAD_MAX_SPEECH_MS = max(2000, int(os.getenv("VAD_MAX_SPEECH_MS", "8000")))
INPUT_RESPONSE_TIMEOUT_SECONDS = max(5, int(os.getenv("INPUT_RESPONSE_TIMEOUT_SECONDS", "12")))
PROMPT_ECHO_GUARD_SECONDS = max(0.5, float(os.getenv("PROMPT_ECHO_GUARD_SECONDS", "1.5")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def silence_limit_ms(dialogue_status):
    return {
        "awaiting_id": 700,
        "awaiting_name": 650,
        "awaiting_company_message": 1000,
        "awaiting_company_message_confirmation": 450,
        "in_progress": 450,
    }.get(dialogue_status, 600)


class SpeechSegmenter:
    def __init__(self):
        self.buffer = bytearray()
        self.speech_ms = 0
        self.silence_ms = 0

    def reset(self):
        self.buffer.clear()
        self.speech_ms = 0
        self.silence_ms = 0

    def feed(self, pcm, dialogue_status):
        speaking = audioop.rms(pcm, 2) >= VAD_SPEECH_RMS
        if not self.buffer and not speaking:
            return None
        self.buffer.extend(pcm)
        if speaking:
            self.speech_ms += VAD_BLOCK_MS
            self.silence_ms = 0
        else:
            self.silence_ms += VAD_BLOCK_MS
        total_ms = len(self.buffer) * 1000 // (RATE * 2)
        finished = self.silence_ms >= silence_limit_ms(dialogue_status)
        if (finished and self.speech_ms >= 200) or total_ms >= VAD_MAX_SPEECH_MS:
            segment = bytes(self.buffer)
            self.reset()
            return segment
        return None


def complete_or_start_next(con, checkin_id, completion_text=COMPLETE_PROMPT):
    finish_queue_entry(con, checkin_id)
    next_id, started = activate_next_participant(con, completion_text)
    if started:
        return next_id
    update_status(
        con,
        response_worker_status="waiting_for_participant",
        response_worker_checkin_id="",
        participant_worker_status="checkin_completed",
        participant_worker_detail=f"点呼 #{checkin_id} 完了 / 参加者の退出待ち",
        participant_worker_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    if is_realtime(con):
        queue_prompt(con, completion_text, "waiting_for_participant", checkin_id)
    return None


def setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def update_status(con, **values):
    for key, value in values.items():
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def checkin_accepts_answers(con, checkin_id):
    row = con.execute("SELECT status FROM checkins WHERE id=?", (checkin_id,)).fetchone()
    return bool(row and row[0] in ("awaiting_id", "awaiting_name", "in_progress", "awaiting_company_message", "awaiting_company_message_confirmation"))


def active_scenario(con):
    scenario_id = int(setting(con, "active_scenario_id", "1"))
    row = con.execute("SELECT questions_json FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
    return scenario_id, json.loads(row[0])


def classify(text):
    compact = text.replace(" ", "")
    specific_negative = ("いいえ", "持っていません", "問題があります", "悪い", "不調")
    positive = ("はい", "持っています", "問題ない", "問題ありません", "大丈夫", "良好")
    if any(word in compact for word in specific_negative):
        return False
    if any(word in compact for word in positive):
        return True
    if "ありません" in compact:
        return False
    return None


DIGIT_WORDS = {
    "ゼロ": "0", "れい": "0", "零": "0", "〇": "0",
    "いち": "1", "一": "1", "に": "2", "二": "2", "さん": "3", "三": "3",
    "よん": "4", "し": "4", "四": "4", "ご": "5", "五": "5",
    "ろく": "6", "六": "6", "なな": "7", "しち": "7", "七": "7",
    "はち": "8", "八": "8", "きゅう": "9", "く": "9", "九": "9",
}


def normalize_digits(text):
    normalized = text.translate(str.maketrans("０１２３４５６７８９ー―−", "0123456789---"))
    normalized = re.sub(r"(?:ハイフン|ダッシュ|の)", "-", normalized, flags=re.IGNORECASE)
    for word in sorted(DIGIT_WORDS, key=len, reverse=True):
        normalized = normalized.replace(word, DIGIT_WORDS[word])
    return normalized


def looks_like_vehicle_number(text):
    compact = normalize_digits(text).replace(" ", "")
    digits = "".join(char for char in compact if char.isdigit())
    return 1 <= len(digits) <= 8


def normalize_vehicle_number(text):
    compact = re.sub(r"[\s、。,.]", "", normalize_digits(text))
    match = re.search(r"\d+(?:-\d+)*", compact)
    if match:
        return match.group(0).strip("-")
    groups = re.findall(r"\d+", compact)
    return "-".join(groups) if groups else compact


def looks_like_driver_name(text):
    compact = re.sub(r"[\s、。,.!?！？]", "", text)
    compact = re.sub(r"^(?:私|名前|氏名)(?:は|が)?", "", compact)
    compact = re.sub(r"(?:です|と申します|といいます)$", "", compact)
    rejected = ("もう一度", "お願いします", "わかりません", "どうした", "ご視聴", "書き起こ", "数字")
    return 2 <= len(compact) <= 40 and not compact.isdigit() and not any(word in compact for word in rejected)


def normalize_driver_name(text):
    name = " ".join(text.split()).strip(" 、。,.：:")
    name = re.sub(r"^(?:私は|名前は|氏名は|名前|氏名)\s*", "", name)
    name = re.sub(r"\s*(?:です|と申します|といいます)[。.]?$", "", name)
    return name.strip(" 、。,.：:")


def lookup_driver(con, vehicle_number, meet_participant_id=""):
    vehicle = con.execute(
        "SELECT driver_name,meet_participant_id FROM driver_identities WHERE vehicle_number=? AND verified=1",
        (vehicle_number,),
    ).fetchone()
    meet = None
    if meet_participant_id:
        meet = con.execute(
            "SELECT driver_name FROM driver_identities WHERE meet_participant_id=? AND verified=1 ORDER BY last_seen_at DESC LIMIT 1",
            (meet_participant_id,),
        ).fetchone()
    if vehicle and meet and vehicle[0] != meet[0]:
        return ""
    if vehicle:
        return vehicle[0]
    return meet[0] if meet else ""


def remember_driver(con, vehicle_number, driver_name, meet_participant_id=""):
    con.execute(
        "INSERT INTO driver_identities(vehicle_number,driver_name,meet_participant_id,verified) VALUES(?,?,?,1) "
        "ON CONFLICT(vehicle_number) DO UPDATE SET driver_name=excluded.driver_name,"
        "meet_participant_id=CASE WHEN excluded.meet_participant_id<>'' THEN excluded.meet_participant_id ELSE driver_identities.meet_participant_id END,"
        "last_seen_at=CURRENT_TIMESTAMP,use_count=driver_identities.use_count+1,verified=1",
        (vehicle_number, driver_name, meet_participant_id),
    )

def looks_like_company_message(text):
    compact = re.sub(r"[\s、。,.!?！？]", "", text)
    if not compact:
        return False
    if compact in ("はい", "そうです", "了解", "わかりました"):
        return False
    return len(compact) >= 2

def summarize_company_message(text):
    text, _ = normalize_logistics_terms(text)
    compact = re.sub(r"[\s、。,.]", "", text)
    no_message = ("特にありません", "特にない", "ありません", "ないです", "大丈夫です", "なし", "いいえ")
    if any(word in compact for word in no_message):
        return "特になし", "none"
    summary = " ".join(text.split()).strip(" 、。,.：:")
    domain_corrections = {
        "程度ダンプ": "テールランプ",
        "テールダンプ": "テールランプ",
        "ブレイキ": "ブレーキ",
        "社領": "車両",
    }
    for wrong, correct in domain_corrections.items():
        summary = summary.replace(wrong, correct)
    summary = re.sub(r"^(?:会社には|会社へは|伝言は|伝えたいことは)\s*", "", summary)
    summary = re.sub(r"\s*(?:です|と伝えてください|をお願いします)[。.]?$", "", summary)
    summary = summary[:180] or "内容を要確認"
    urgent_words = (
        "事故", "故障", "遅延", "遅れ", "危険", "至急", "緊急", "体調", "けが",
        "フロントガラス", "ひび", "亀裂", "破損", "パンク", "ブレーキ", "警告灯",
    )
    category = "urgent" if any(word in summary for word in urgent_words) else "normal"
    return summary, category


def record_company_message(con, checkin_id, text, mark_alert=True):
    summary, category = summarize_company_message(text)
    con.execute(
        "INSERT INTO company_messages(checkin_id,original_text,summary_text,category) VALUES(?,?,?,?) "
        "ON CONFLICT(checkin_id) DO UPDATE SET original_text=excluded.original_text," 
        "summary_text=excluded.summary_text,category=excluded.category",
        (checkin_id, text, summary, category),
    )
    if mark_alert and category == "urgent":
        con.execute("UPDATE checkins SET alert=1 WHERE id=?", (checkin_id,))
    return summary, category


def transcribe(model, pcm, dialogue_status="awaiting_id"):
    if not pcm or max(pcm) == min(pcm):
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav") as temporary:
        with wave.open(temporary.name, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE); wav.writeframes(pcm)
        precise = dialogue_status in ("awaiting_id", "awaiting_name", "awaiting_company_message")
        segments, _ = model.transcribe(
            temporary.name,
            language="ja",
            task="transcribe",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 250},
            beam_size=3 if precise else 1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            hotwords=hotwords_for_status(dialogue_status),
            initial_prompt={
                "awaiting_id": "車番を数字で答えています。例: 12-34、15-84。数字を正確に短く書き起こしてください。",
                "awaiting_name": "日本人の氏名をフルネームで答えています。氏名だけを正確に書き起こしてください。",
                "in_progress": "点呼の質問に、はい、または、いいえで短く答えています。",
                "awaiting_company_message": "運送会社への伝言です。車両、テールランプ、タイヤ、ブレーキ、故障、遅延などの内容を省略せず正確に書き起こしてください。",
                "awaiting_company_message_confirmation": "復唱した会社伝言への確認に、はい、または、いいえで答えています。",
            }.get(dialogue_status, "日本語の会話を正確に書き起こしてください。"),
        )
        raw_text = "".join(segment.text for segment in segments).strip()
        normalized, corrections = normalize_logistics_terms(raw_text)
        if corrections:
            logging.info("logistics terms normalized corrections=%s raw=%r", corrections, raw_text)
        return normalized


def handle_transcript(con, text, checkin_id=None):
    _, questions = active_scenario(con)
    realtime = is_realtime(con)
    update_status(con, response_worker_status="listening", response_worker_last_text=text)
    if checkin_id is None:
        pending = con.execute(
            "SELECT id,status FROM checkins WHERE status IN ('awaiting_id','awaiting_name','in_progress','awaiting_company_message','awaiting_company_message_confirmation') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not pending:
            update_status(con, response_worker_status="waiting_for_participant")
            return None
        checkin_id = pending[0]
    status_row = con.execute("SELECT status FROM checkins WHERE id=?", (checkin_id,)).fetchone()
    if not status_row:
        return None
    status = status_row[0]
    if status == "awaiting_id":
        if not looks_like_vehicle_number(text):
            update_status(con, response_worker_status="awaiting_vehicle_number")
            if realtime: queue_prompt(con, RETRY_ID_PROMPT, "awaiting_vehicle_number", checkin_id)
            return None
        vehicle_number = normalize_vehicle_number(text)
        meet_row = con.execute("SELECT meet_participant_id FROM checkins WHERE id=?", (checkin_id,)).fetchone()
        meet_participant_id = meet_row[0] if meet_row else ""
        known_name = lookup_driver(con, vehicle_number, meet_participant_id) if is_natural(con) else ""
        if known_name:
            con.execute(
                "UPDATE checkins SET participant_id=?,participant_name=?,status='in_progress' WHERE id=?",
                (vehicle_number, known_name, checkin_id),
            )
            remember_driver(con, vehicle_number, known_name, meet_participant_id)
            index, question = next_unanswered_question(con, checkin_id, questions)
            if question is None:
                con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (checkin_id,))
                prompt = greeting_prompt(known_name, COMPANY_MESSAGE_PROMPT)
                update_status(con, response_worker_status="awaiting_company_message")
                queue_prompt(con, prompt, "awaiting_company_message", checkin_id)
            else:
                prompt = greeting_prompt(known_name, question_prompt(question))
                update_status(con, response_worker_status=f"awaiting_answer_{index + 1}", response_worker_checkin_id=str(checkin_id))
                queue_prompt(con, prompt, f"awaiting_answer_{index + 1}", checkin_id)
            return checkin_id
        con.execute("UPDATE checkins SET participant_id=?,status='awaiting_name' WHERE id=?", (vehicle_number, checkin_id))
        update_status(con, response_worker_status="awaiting_driver_name", response_worker_checkin_id=str(checkin_id))
        if realtime: queue_prompt(con, NAME_PROMPT, "awaiting_driver_name", checkin_id)
        return checkin_id
    if status == "awaiting_name":
        if not looks_like_driver_name(text):
            update_status(con, response_worker_status="awaiting_driver_name")
            if realtime: queue_prompt(con, RETRY_NAME_PROMPT, "awaiting_driver_name", checkin_id)
            return checkin_id
        driver_name = normalize_driver_name(text)
        con.execute("UPDATE checkins SET participant_name=?,status='in_progress' WHERE id=?", (driver_name, checkin_id))
        if is_natural(con):
            identity = con.execute("SELECT participant_id,meet_participant_id FROM checkins WHERE id=?", (checkin_id,)).fetchone()
            remember_driver(con, identity[0], driver_name, identity[1])
        index, question = next_unanswered_question(con, checkin_id, questions)
        update_status(con, response_worker_status=f"awaiting_answer_{index + 1}", response_worker_checkin_id=str(checkin_id))
        if question is None:
            if is_natural(con):
                con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (checkin_id,))
                update_status(con, response_worker_status="awaiting_company_message")
                queue_prompt(con, greeting_prompt(driver_name, COMPANY_MESSAGE_PROMPT), "awaiting_company_message", checkin_id)
                return checkin_id
            con.execute("UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?", (checkin_id,))
            return complete_or_start_next(con, checkin_id)
        if realtime:
            prompt = greeting_prompt(driver_name, question_prompt(question)) if is_natural(con) else question_prompt(question)
            queue_prompt(con, prompt, f"awaiting_answer_{index + 1}", checkin_id)
        return checkin_id
    if status == "awaiting_company_message":
        if not looks_like_company_message(text):
            update_status(con, response_worker_status="awaiting_company_message")
            if realtime:
                queue_prompt(
                    con,
                    "会社への伝言内容を具体的にお話しください。ない場合は、特にありません、とお答えください。",
                    "awaiting_company_message",
                    checkin_id,
                )
            return checkin_id
        record_company_message(con, checkin_id, text, mark_alert=True)
        con.execute("UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?", (checkin_id,))
        return complete_or_start_next(con, checkin_id, f"お伝えします。{COMPLETE_PROMPT}")
    if status == "awaiting_company_message_confirmation":
        confirmed = classify(text)
        if confirmed is None:
            row = con.execute("SELECT summary_text FROM company_messages WHERE checkin_id=?", (checkin_id,)).fetchone()
            summary = row[0] if row else "内容"
            update_status(con, response_worker_status="awaiting_company_message_confirmation")
            if realtime:
                queue_prompt(
                    con,
                    f"会社への伝言は「{summary}」でよろしいですか。はい、または、いいえでお答えください。",
                    "awaiting_company_message_confirmation",
                    checkin_id,
                )
            return checkin_id
        if confirmed is False:
            con.execute("DELETE FROM company_messages WHERE checkin_id=?", (checkin_id,))
            con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (checkin_id,))
            update_status(con, response_worker_status="awaiting_company_message")
            if realtime:
                queue_prompt(
                    con,
                    "承知しました。会社への伝言を、最初からもう一度お話しください。",
                    "awaiting_company_message",
                    checkin_id,
                )
            return checkin_id
        row = con.execute(
            "SELECT summary_text,category FROM company_messages WHERE checkin_id=?", (checkin_id,)
        ).fetchone()
        summary, category = row if row else ("内容を要確認", "normal")
        if category == "urgent":
            con.execute("UPDATE checkins SET alert=1 WHERE id=?", (checkin_id,))
        con.execute("UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?", (checkin_id,))
        priority = "要確認として、" if category == "urgent" else ""
        completion_text = f"承知しました。会社へは、{priority}『{summary}』と伝えます。点呼は以上です。お疲れ様でした。"
        return complete_or_start_next(con, checkin_id, completion_text)
    if status != "in_progress":
        return None
    index, question = next_unanswered_question(con, checkin_id, questions)
    if question is None:
        return None
    is_ok = classify(text)
    if is_ok is None:
        update_status(con, response_worker_status=f"awaiting_answer_{index + 1}")
        if realtime: queue_prompt(con, RETRY_ANSWER_PROMPT, f"awaiting_answer_{index + 1}", checkin_id)
        return checkin_id
    con.execute(
        "INSERT INTO answers(checkin_id,question_key,question_text,answer_text,is_ok) VALUES(?,?,?,?,?)",
        (checkin_id, question["key"], question["text"], text, is_ok),
    )
    if is_ok is not True:
        con.execute("UPDATE checkins SET alert=1 WHERE id=?", (checkin_id,))
    next_index, next_question = next_unanswered_question(con, checkin_id, questions)
    if next_question is None:
        if is_natural(con):
            con.execute("UPDATE checkins SET status='awaiting_company_message' WHERE id=?", (checkin_id,))
            update_status(con, response_worker_status="awaiting_company_message", response_worker_checkin_id=str(checkin_id))
            queue_prompt(con, COMPANY_MESSAGE_PROMPT, "awaiting_company_message", checkin_id)
            return checkin_id
        con.execute("UPDATE checkins SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?", (checkin_id,))
        return complete_or_start_next(con, checkin_id)
    update_status(con, response_worker_status=f"awaiting_answer_{next_index + 1}")
    if realtime: queue_prompt(con, question_prompt(next_question), f"awaiting_answer_{next_index + 1}", checkin_id)
    return checkin_id


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        MODEL_SIZE,
        device=MODEL_DEVICE,
        compute_type=MODEL_COMPUTE_TYPE,
        download_root=str(MODEL_DIR),
        local_files_only=True,
        num_workers=1,
    )
    logging.info(
        "Whisper resident model=%s device=%s compute_type=%s language=ja",
        MODEL_SIZE, MODEL_DEVICE, MODEL_COMPUTE_TYPE,
    )
    offset = AUDIO_PATH.stat().st_size if AUDIO_PATH.exists() else 0
    checkin_id = None
    segmenter = SpeechSegmenter()
    waiting_prompt_id = ""
    waiting_since = time.monotonic()
    ignore_audio_until = 0.0
    silent_reprompts = 0
    while True:
        if os.getenv("CLUSTER_ENABLED", "0") == "1":
            with sqlite3.connect(DB_PATH, timeout=10) as cluster_con:
                if setting(cluster_con, "cluster_is_leader", "0") != "1":
                    time.sleep(2)
                    continue
        if not AUDIO_PATH.exists(): time.sleep(1); continue
        size = AUDIO_PATH.stat().st_size
        if size < offset: offset, checkin_id = 0, None; segmenter.reset()
        if size - offset < VAD_BLOCK_BYTES: time.sleep(0.05); continue
        with AUDIO_PATH.open("rb") as audio:
            audio.seek(offset); block = audio.read(VAD_BLOCK_BYTES)
        offset += len(block)
        with sqlite3.connect(DB_PATH, timeout=30) as con:
            prompt_state = setting(con, "realtime_prompt_state", "played")
            prompt_id = setting(con, "realtime_prompt_id", "")
            prompt_busy = setting(con, "realtime_prompt_playing", "0") == "1" or prompt_state in (
                "queued", "synthesizing", "generating", "rendering", "ready"
            )
            if checkin_id is not None and not checkin_accepts_answers(con, checkin_id):
                checkin_id = None
            recording_checkin_id = checkin_id or active_checkin_id(con)
            dialogue_status = "awaiting_id"
            if recording_checkin_id:
                status_row = con.execute("SELECT status FROM checkins WHERE id=?", (recording_checkin_id,)).fetchone()
                if status_row:
                    dialogue_status = status_row[0]
            if prompt_state == "played" and prompt_id != waiting_prompt_id:
                waiting_prompt_id = prompt_id
                waiting_since = time.monotonic()
                ignore_audio_until = waiting_since + PROMPT_ECHO_GUARD_SECONDS
                segmenter.reset()
            if (
                prompt_state == "played"
                and not prompt_busy
                and recording_checkin_id
                and time.monotonic() - waiting_since >= INPUT_RESPONSE_TIMEOUT_SECONDS
            ):
                prompt_text = setting(con, "realtime_prompt_text", "")
                prompt_status = setting(con, "realtime_prompt_status_after", dialogue_status)
                prompt_checkin_id = setting(con, "realtime_prompt_checkin_id", recording_checkin_id)
                if prompt_text:
                    new_prompt_id = queue_prompt(con, prompt_text, prompt_status, prompt_checkin_id)
                    waiting_prompt_id = ""
                    waiting_since = time.monotonic()
                    silent_reprompts += 1
                    logging.warning(
                        "no input after prompt; replaying prompt checkin=%s count=%s",
                        recording_checkin_id,
                        silent_reprompts,
                    )
                    segmenter.reset()
                    continue
        if not recording_checkin_id:
            segmenter.reset()
            continue
        # The recorder reads the PulseAudio output monitor, which contains the
        # bot's own prompt as well as the remote participant. Never buffer bot
        # audio for later transcription, and discard the short monitor tail
        # after playback completes.
        if prompt_busy or time.monotonic() < ignore_audio_until:
            segmenter.reset()
            continue
        pcm = segmenter.feed(block, dialogue_status)
        if pcm is None:
            continue
        waiting_since = time.monotonic()
        silent_reprompts = 0
        text = transcribe(model, pcm, dialogue_status)
        if not text:
            continue
        # WAV creation and filesystem metadata happen before the SQLite write transaction.
        recording_draft = prepare_recording(recording_checkin_id, pcm, text, RATE)
        saved = False
        for attempt in range(5):
            try:
                with sqlite3.connect(DB_PATH, timeout=10) as con:
                    checkin_id = handle_transcript(con, text, checkin_id)
                    if recording_draft:
                        persist_recording(con, recording_draft)
                saved = True
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    logging.exception("failed to save recognized response")
                    break
                delay = 0.25 * (attempt + 1)
                logging.warning("database busy; retrying recognized response attempt=%s", attempt + 2)
                time.sleep(delay)
        if saved:
            # This network call is deliberately outside the SQLite transaction.
            naturalize_pending_prompt(DB_PATH, checkin_id)


if __name__ == "__main__":
    main()
