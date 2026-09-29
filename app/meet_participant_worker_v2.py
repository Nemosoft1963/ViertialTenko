import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone

from playwright.async_api import async_playwright

try:
    from app.realtime_browser import install_media_bridge, play_pending_prompt, sync_media_mode
    from app.realtime_dialogue import INTRO_PROMPT, is_realtime, queue_prompt
    from app.participant_queue import enqueue_participant, ensure_queue_schema, target_intro, waiting_count
except ImportError:
    from realtime_browser import install_media_bridge, play_pending_prompt, sync_media_mode
    from realtime_dialogue import INTRO_PROMPT, is_realtime, queue_prompt
    from participant_queue import enqueue_participant, ensure_queue_schema, target_intro, waiting_count


DB_PATH = os.getenv("DATABASE_PATH", "/data/tenko.db")
CDP_URL = os.getenv("MEET_CDP_URL", "http://127.0.0.1:9222")
DEPARTURE_GRACE_SECONDS = max(
    0.0, float(os.getenv("PARTICIPANT_DEPARTURE_GRACE_SECONDS", "5"))
)
DEPARTURE_QUESTION_KEY = "participant_departure"
DEPARTURE_QUESTION_TEXT = "\u53c2\u52a0\u8005\u9000\u51fa"

REJOIN_RETRY_SECONDS = max(
    1.0, float(os.getenv("BOT_REJOIN_RETRY_SECONDS", "5"))
)
IN_MEETING_MARKERS = ("\u901a\u8a71\u304b\u3089\u9000\u51fa", "Leave call")
ADMISSION_WAIT_MARKERS = (
    "\u53c2\u52a0\u30ea\u30af\u30a8\u30b9\u30c8\u3092\u9001\u4fe1\u3057\u307e\u3057\u305f",
    "\u8ab0\u304b\u304c\u8a31\u53ef\u3059\u308b\u3068\u53c2\u52a0\u3067\u304d\u307e\u3059",
    "You've asked to join",
    "Asking to join",
)
JOIN_LABELS = (
    "\u518d\u53c2\u52a0",
    "\u3082\u3046\u4e00\u5ea6\u53c2\u52a0",
    "\u53c2\u52a0",
    "\u4eca\u3059\u3050\u53c2\u52a0",
    "\u53c2\u52a0\u3092\u30ea\u30af\u30a8\u30b9\u30c8",
    "\u3053\u306e\u30c7\u30d0\u30a4\u30b9\u306b\u5207\u308a\u66ff\u3048\u308b",
    "Rejoin",
    "Join again",
    "Join now",
    "Ask to join",
    "Switch here",
)
_last_rejoin_attempt = 0.0


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(con, key, value):
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def record_participant_departure():
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        ensure_queue_schema(con)
        active = con.execute(
            "SELECT id,participant_name FROM checkins "
            "WHERE status IN ('awaiting_id','awaiting_name','in_progress','awaiting_company_message','awaiting_company_message_confirmation') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not active:
            save_setting(con, "participant_worker_status", "waiting_for_participant")
            save_setting(
                con,
                "participant_worker_detail",
                "\u53c2\u52a0\u8005\u306a\u3057 / \u65b0\u898f\u5165\u5ba4\u3092\u5f85\u6a5f\u4e2d",
            )
            save_setting(con, "participant_worker_updated_at", now_iso())
            save_setting(con, "response_worker_status", "waiting_for_participant")
            save_setting(con, "response_worker_checkin_id", "")
            return None

        checkin_id, participant_name = active
        answer_text = (
            "Google Meet\u304b\u3089\u9000\u51fa\u3057\u305f\u305f\u3081\u3001"
            "\u70b9\u547c\u3092\u4e2d\u65ad\u3057\u307e\u3057\u305f\u3002"
            "\u65b0\u898f\u5165\u5ba4\u3092\u5f85\u6a5f\u3057\u307e\u3059\u3002"
        )
        con.execute(
            "INSERT INTO answers("
            "checkin_id,question_key,question_text,answer_text,is_ok"
            ") VALUES(?,?,?,?,?)",
            (
                checkin_id,
                DEPARTURE_QUESTION_KEY,
                DEPARTURE_QUESTION_TEXT,
                answer_text,
                0,
            ),
        )
        con.execute(
            "UPDATE checkins SET status='cancelled',alert=1,"
            "completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (checkin_id,),
        )
        con.execute(
            "UPDATE participant_queue SET status='completed' WHERE checkin_id=?",
            (checkin_id,),
        )
        if setting(con, "realtime_prompt_checkin_id") == str(checkin_id):
            prompt_id = setting(con, "realtime_prompt_id", "0")
            save_setting(con, "realtime_prompt_played_id", prompt_id)
            save_setting(con, "realtime_prompt_state", "played")
            save_setting(con, "realtime_prompt_playing", "0")
            if setting(con, "gpu_pipeline_prompt_id") == prompt_id:
                save_setting(con, "gpu_pipeline_state", "standby")
        display_name = participant_name or "Meet participant"
        save_setting(con, "participant_worker_status", "waiting_for_participant")
        save_setting(
            con,
            "participant_worker_detail",
            f"{display_name}\u304c\u9000\u51fa / \u70b9\u547c #{checkin_id} \u3092\u4e2d\u65ad / "
            "\u65b0\u898f\u5165\u5ba4\u3092\u5f85\u6a5f\u4e2d",
        )
        save_setting(con, "participant_worker_updated_at", now_iso())
        save_setting(con, "response_worker_status", "waiting_for_participant")
        save_setting(con, "response_worker_checkin_id", "")
        return checkin_id

def has_active_checkin():
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        row = con.execute(
            "SELECT 1 FROM checkins WHERE status IN ('awaiting_id','awaiting_name','in_progress','awaiting_company_message','awaiting_company_message_confirmation') LIMIT 1"
        ).fetchone()
    return bool(row)


def presence_session_started():
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        return setting(con, "participant_worker_presence_started", "0") == "1"


def reset_presence_session():
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        save_setting(con, "participant_worker_presence_started", "0")


def should_register_from_count(count, sequence_started):
    return (
        count is not None
        and count > 1
        and not sequence_started
        and not has_active_checkin()
        and not presence_session_started()
    )

def register_participant(display_name, meet_participant_id=""):
    fallback_id = "" if display_name in ("", "Meet participant") else f"display:{display_name}"
    meet_participant_id = (meet_participant_id or fallback_id).strip()[:200]
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        bot_names = {
            setting(con, "bot_display_name", "").strip(),
            setting(con, "meet_bot_account_name", "").strip(),
        }
        bot_names.discard("")
        if display_name.strip() in bot_names:
            save_setting(con, "participant_worker_status", "bot_join_ignored")
            save_setting(con, "participant_worker_detail", f"BOT自身の参加通知を除外: {display_name}")
            save_setting(con, "participant_worker_updated_at", now_iso())
            return None, False
        active = con.execute(
            "SELECT id FROM checkins WHERE status IN ('awaiting_id','awaiting_name','in_progress','awaiting_company_message','awaiting_company_message_confirmation') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if active:
            queue_id, queued = enqueue_participant(con, display_name, meet_participant_id)
            pending = waiting_count(con)
            save_setting(con, "participant_worker_status", "participant_queued" if queued else "duplicate_join_ignored")
            save_setting(
                con,
                "participant_worker_detail",
                f"{display_name} / queue #{queue_id} / 待機 {pending}人 / active checkin #{active[0]}",
            )
            save_setting(con, "participant_worker_updated_at", now_iso())
            return active[0], False
        scenario_id = int(setting(con, "active_scenario_id", "1"))
        cursor = con.execute(
            "INSERT INTO checkins(participant_id,participant_name,meet_participant_id,meet_url,scenario_id,status) VALUES(?,?,?,?,?,?)",
            ("\u78ba\u8a8d\u5f85\u3061", display_name, meet_participant_id, setting(con, "meet_url"), scenario_id, "awaiting_id"),
        )
        checkin_id = cursor.lastrowid
        save_setting(con, "participant_worker_presence_started", "1")
        save_setting(con, "participant_worker_status", "checkin_started")
        save_setting(con, "participant_worker_detail", f"{display_name} / checkin #{checkin_id}")
        save_setting(con, "participant_worker_updated_at", now_iso())
        if is_realtime(con):
            queue_prompt(
                con,
                target_intro(display_name, waiting_count(con)),
                "awaiting_vehicle_number",
                checkin_id,
            )
        return checkin_id, True


def configured_meet_url():
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        return setting(con, "meet_url").strip()



def is_in_meeting_text(body_text):
    return any(marker in body_text for marker in IN_MEETING_MARKERS)


def is_waiting_for_admission(body_text):
    return any(marker in body_text for marker in ADMISSION_WAIT_MARKERS)


def save_worker_state(status, detail):
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        save_setting(con, "participant_worker_status", status)
        save_setting(con, "participant_worker_detail", detail)
        save_setting(con, "participant_worker_updated_at", now_iso())


async def ensure_meeting_page(page):
    global _last_rejoin_attempt

    target = configured_meet_url()
    if not target.startswith("https://meet.google.com/"):
        return False

    if target not in page.url:
        await page.goto(target, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

    body_text = await page.locator("body").inner_text()
    if is_in_meeting_text(body_text):
        return True

    now = asyncio.get_running_loop().time()
    if now - _last_rejoin_attempt < REJOIN_RETRY_SECONDS:
        return False
    _last_rejoin_attempt = now
    save_worker_state(
        "bot_rejoining",
        "BOT\u304cMeet\u304b\u3089\u9000\u51fa\u307e\u305f\u306f\u5207\u65ad / \u81ea\u52d5\u518d\u5165\u5ba4\u4e2d",
    )

    for attempt in range(2):
        body_text = await page.locator("body").inner_text()
        if is_in_meeting_text(body_text):
            save_worker_state("meeting_ready", f"BOT\u518d\u5165\u5ba4\u5b8c\u4e86 / {target}")
            return True
        if is_waiting_for_admission(body_text):
            save_worker_state(
                "waiting_admission",
                "BOT\u518d\u5165\u5ba4\u3092\u30ea\u30af\u30a8\u30b9\u30c8\u6e08\u307f / \u5165\u5ba4\u8a31\u53ef\u5f85\u3061",
            )
            return False

        for label in JOIN_LABELS:
            button = page.get_by_role("button", name=label, exact=True)
            if not await button.count():
                continue
            await button.first.click()
            await page.wait_for_timeout(5000)
            body_text = await page.locator("body").inner_text()
            if is_in_meeting_text(body_text):
                save_worker_state("meeting_ready", f"BOT\u518d\u5165\u5ba4\u5b8c\u4e86 / {target}")
                return True
            if is_waiting_for_admission(body_text):
                save_worker_state(
                    "waiting_admission",
                    "BOT\u518d\u5165\u5ba4\u3092\u30ea\u30af\u30a8\u30b9\u30c8\u6e08\u307f / \u5165\u5ba4\u8a31\u53ef\u5f85\u3061",
                )
            else:
                save_worker_state(
                    "bot_rejoin_retry",
                    f"BOT\u518d\u5165\u5ba4\u64cd\u4f5c\u5f8c\u306e\u63a5\u7d9a\u5f85\u3061 / {label}",
                )
            return False

        if attempt == 0:
            await page.goto(target, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

    save_worker_state(
        "bot_rejoin_retry",
        f"BOT\u518d\u5165\u5ba4\u30dc\u30bf\u30f3\u5f85\u3061 / {target}",
    )
    return False


def extract_name(message):
    text = " ".join(message.split())
    patterns = (
        r"^(.+?)(?:\u3055\u3093)?\u304c(?:\u30df\u30fc\u30c6\u30a3\u30f3\u30b0\u306b|\u901a\u8a71\u306b)?\u53c2\u52a0\u3057\u307e\u3057\u305f",
        r"^(.+?) joined(?: the call| this call| the meeting)?",
        r"^(.+?) has joined",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip(" ,:")
            if name and len(name) <= 120:
                return name
    return None


async def install_observer(page):
    await page.evaluate(
        r"""
        () => {
          window.__tenkoJoinEvents = window.__tenkoJoinEvents || [];
          if (window.__tenkoObserverInstalledV2) return;
          window.__tenkoObserverInstalledV2 = true;
          const seen = new Set();
          const collect = (node) => {
            const text = (node && node.textContent || '');
            for (const raw of text.split(/\n+/)) {
              const line = raw.trim();
              if (!line || line.length > 200) continue;
              if (!/(\u53c2\u52a0\u3057\u307e\u3057\u305f|joined|has joined)/i.test(line)) continue;
              if (seen.has(line)) continue;
              seen.add(line);
              const host = node && node.nodeType === 1 ? node : node && node.parentElement;
              const holder = host && host.closest && host.closest('[data-participant-id],[data-requested-participant-id]');
              const meetId = holder && (holder.getAttribute('data-participant-id') || holder.getAttribute('data-requested-participant-id')) || '';
              window.__tenkoJoinEvents.push({text: line, meetParticipantId: meetId});
              setTimeout(() => seen.delete(line), 60000);
            }
          };
          new MutationObserver((mutations) => {
            for (const mutation of mutations) {
              for (const node of mutation.addedNodes) collect(node);
            }
          }).observe(document.body, {childList: true, subtree: true});
        }
        """
    )


async def restart_virtual_microphone(page):
    target = configured_meet_url()
    if not target:
        return
    count = await participant_count(page)
    if count is not None:
        with sqlite3.connect(DB_PATH, timeout=30) as con:
            save_setting(con, "participant_worker_last_count", str(count))
    await page.goto(target, wait_until="domcontentloaded")
    await page.wait_for_timeout(1000)
    await ensure_meeting_page(page)
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        save_setting(con, "participant_worker_status", "sequence_started")
        save_setting(con, "participant_worker_detail", target)
        save_setting(con, "participant_worker_updated_at", now_iso())


async def participant_count(page):
    body_text = await page.locator("body").inner_text()
    if "通話から退出" not in body_text:
        return None
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        worker_status = setting(con, "participant_worker_status")
    if worker_status in (
        "bot_rejoining",
        "bot_rejoin_retry",
        "waiting_admission",
    ):
        save_worker_state("meeting_ready", f"BOT\u518d\u5165\u5ba4\u5b8c\u4e86 / {configured_meet_url()}")


    lines = [line.strip() for line in body_text.splitlines()]
    try:
        details_index = lines.index("ミーティングの詳細")
    except ValueError:
        return None

    for line in lines[details_index + 1 : details_index + 4]:
        if line.isdigit():
            count = int(line)
            if 1 <= count <= 1000:
                return count
    return None


async def run():
    async with async_playwright() as playwright:
        while True:
            try:
                browser = await playwright.chromium.connect_over_cdp(CDP_URL)
                for context in browser.contexts:
                    await install_media_bridge(context)
                with sqlite3.connect(DB_PATH, timeout=30) as con:
                    last_participant_count = int(setting(con, "participant_worker_last_count", "1"))
                alone_since = None
                departure_checked = False
                count_candidate = None
                count_candidate_samples = 0
                while True:
                    if os.getenv("CLUSTER_ENABLED", "0") == "1":
                        with sqlite3.connect(DB_PATH, timeout=10) as cluster_con:
                            if setting(cluster_con, "cluster_is_leader", "0") != "1":
                                await asyncio.sleep(2)
                                continue
                    pages = [page for context in browser.contexts for page in context.pages]
                    page = next((item for item in pages if "meet.google.com" in item.url), None)
                    if page is None:
                        await asyncio.sleep(2)
                        continue
                    await ensure_meeting_page(page)
                    if not await sync_media_mode(page):
                        await asyncio.sleep(2)
                        continue
                    await install_observer(page)
                    await play_pending_prompt(page)
                    events = await page.evaluate("() => (window.__tenkoJoinEvents || []).splice(0)")
                    sequence_started = False
                    for event in events:
                        event_text = event.get("text", "") if isinstance(event, dict) else event
                        meet_participant_id = event.get("meetParticipantId", "") if isinstance(event, dict) else ""
                        name = extract_name(event_text)
                        if name:
                            _, created = register_participant(name, meet_participant_id)
                            if created:
                                with sqlite3.connect(DB_PATH, timeout=30) as con:
                                    realtime = is_realtime(con)
                                if not realtime:
                                    await restart_virtual_microphone(page)
                            sequence_started = True
                            break
                    count = await participant_count(page)
                    if count is not None:
                        if count == count_candidate:
                            count_candidate_samples += 1
                        else:
                            count_candidate = count
                            count_candidate_samples = 1
                        # Google Meetの人数表示は画面更新時に一瞬だけ増減することがある。
                        # 通知から氏名を取得できなかった場合だけ、3回連続で同じ人数を
                        # 観測してから匿名参加者として登録する。
                        if (
                            count_candidate_samples >= 3
                            and should_register_from_count(count, sequence_started)
                        ):
                            _, created = register_participant("Meet participant")
                            if created:
                                with sqlite3.connect(DB_PATH, timeout=30) as con:
                                    realtime = is_realtime(con)
                                if not realtime:
                                    await restart_virtual_microphone(page)
                        last_participant_count = count
                        if count <= 1:
                            now = asyncio.get_running_loop().time()
                            if alone_since is None:
                                alone_since = now
                                departure_checked = False
                            elif (
                                not departure_checked
                                and now - alone_since >= DEPARTURE_GRACE_SECONDS
                            ):
                                record_participant_departure()
                                if presence_session_started():
                                    reset_presence_session()
                                departure_checked = True
                        else:
                            alone_since = None
                            departure_checked = False
                        with sqlite3.connect(DB_PATH, timeout=30) as con:
                            save_setting(
                                con, "participant_worker_last_count", str(last_participant_count)
                            )
                    else:
                        alone_since = None
                        departure_checked = False
                    await asyncio.sleep(1)
            except Exception as exc:
                with sqlite3.connect(DB_PATH, timeout=30) as con:
                    save_setting(con, "participant_worker_status", "reconnecting")
                    save_setting(con, "participant_worker_detail", f"{type(exc).__name__}: {exc}"[:500])
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())
