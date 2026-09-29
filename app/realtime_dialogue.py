from datetime import datetime, timezone

MODE_LEGACY = "legacy"
MODE_REALTIME = "realtime"
MODE_REALTIME_GPU = "realtime_gpu"
MODE_REALTIME_NATURAL = "realtime_natural"
MODE_REALTIME_3D = "realtime_3d"
MODE_METAHUMAN = "realtime_metahuman"
MODE_OPENWEBUI = "realtime_openwebui"

INTRO_PROMPT = "お待たせしました。これからリアルタイム点呼を開始します。まず、車番を数字ではっきりお答えください。"
NAME_PROMPT = "続けて、お名前をフルネームではっきりお答えください。"
RETRY_ID_PROMPT = "車番を確認できませんでした。数字ではっきり、もう一度お答えください。"
RETRY_NAME_PROMPT = "お名前を確認できませんでした。フルネームで、もう一度お答えください。"
RETRY_ANSWER_PROMPT = "回答を確認できませんでした。はい、または、いいえでお答えください。"
COMPANY_MESSAGE_PROMPT = "最後に、会社へ伝えておきたいことはありますか。なければ、特にありません、とお答えください。"
COMPLETE_PROMPT = "点呼は以上です。回答を記録しました。ありがとうございました。"


def setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def is_realtime(con):
    return setting(con, "operation_mode", MODE_LEGACY) in (MODE_REALTIME, MODE_REALTIME_GPU, MODE_REALTIME_NATURAL, MODE_REALTIME_3D, MODE_METAHUMAN, MODE_OPENWEBUI)


def is_natural(con):
    return setting(con, "operation_mode", MODE_LEGACY) in (MODE_REALTIME_NATURAL, MODE_REALTIME_3D, MODE_METAHUMAN, MODE_OPENWEBUI)


def uses_gpu(con):
    return setting(con, "operation_mode", MODE_LEGACY) in (MODE_REALTIME_GPU, MODE_REALTIME_NATURAL)


def greeting_prompt(driver_name, next_question=""):
    greeting = f"{driver_name}さん、お疲れ様です。点呼を始めます。"
    return f"{greeting} {next_question}".strip()


def question_prompt(question):
    return f'{question["text"]}。「はい」または「いいえ」で、はっきりお答えください。'


def queue_prompt(con, text, status, checkin_id=""):
    prompt_id = int(setting(con, "realtime_prompt_id", "0")) + 1
    values = {
        "realtime_prompt_id": str(prompt_id),
        "realtime_prompt_text": text,
        "realtime_prompt_status_after": status,
        "realtime_prompt_checkin_id": str(checkin_id),
        "realtime_prompt_state": "queued",
        "realtime_prompt_queued_at": datetime.now(timezone.utc).isoformat(),
        "realtime_prompt_fast": "1" if status == "awaiting_company_message_confirmation" else "0",
    }
    for key, value in values.items():
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    return prompt_id


def next_unanswered_question(con, checkin_id, questions):
    answered = {
        row[0]
        for row in con.execute(
            "SELECT question_key FROM answers WHERE checkin_id=?", (checkin_id,)
        ).fetchall()
    }
    for index, question in enumerate(questions):
        if question["key"] not in answered:
            return index, question
    return len(questions), None
