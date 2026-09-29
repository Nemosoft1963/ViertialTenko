import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone

from playwright.async_api import async_playwright


DB_PATH = os.getenv("DATABASE_PATH", "/data/tenko.db")
CDP_URL = os.getenv("MEET_CDP_URL", "http://meet-browser:9222")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def setting(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def register_participant(display_name: str) -> int:
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        scenario_id = int(setting(con, "active_scenario_id", "1"))
        meet_url = setting(con, "meet_url")
        cursor = con.execute(
            "INSERT INTO checkins(participant_id,participant_name,meet_url,scenario_id,status) VALUES(?,?,?,?,?)",
            ("確認待ち", display_name, meet_url, scenario_id, "awaiting_id"),
        )
        checkin_id = cursor.lastrowid
        save_setting(con, "participant_worker_status", "checkin_started")
        save_setting(con, "participant_worker_detail", f"{display_name} / checkin #{checkin_id}")
        save_setting(con, "participant_worker_updated_at", now_iso())
        return checkin_id


def extract_name(message: str) -> str | None:
    text = " ".join(message.split())
    patterns = (
        r"^(.+?)(?:さん)?が参加しました",
        r"^(.+?) joined(?: the call| this call| the meeting)?",
        r"^(.+?) has joined",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip(" 、,:：")
            if name and len(name) <= 120:
                return name
    return None


async def install_observer(page) -> None:
    await page.evaluate(
        """
        () => {
          window.__tenkoJoinEvents = window.__tenkoJoinEvents || [];
          if (window.__tenkoObserverInstalled) return;
          window.__tenkoObserverInstalled = true;
          const seen = new Set();
          const collect = (node) => {
            const text = (node && node.textContent || '').trim();
            if (!text || text.length > 500) return;
            if (!/(参加しました|joined|has joined)/i.test(text)) return;
            if (seen.has(text)) return;
            seen.add(text);
            window.__tenkoJoinEvents.push(text);
            setTimeout(() => seen.delete(text), 60000);
          };
          new MutationObserver((mutations) => {
            for (const mutation of mutations) {
              for (const node of mutation.addedNodes) collect(node);
            }
          }).observe(document.body, {childList: true, subtree: true});
        }
        """
    )


async def restart_virtual_microphone(page) -> None:
    selectors = (
        'button[aria-label*="マイク"]',
        'button[aria-label*="microphone" i]',
        'button[data-tooltip*="マイク"]',
    )
    button = None
    for selector in selectors:
        candidate = page.locator(selector).first
        if await candidate.count():
            button = candidate
            break
    if button is None:
        return
    label = (await button.get_attribute("aria-label") or "").lower()
    if "オフ" in label or "turn off" in label or "mute" in label:
        await button.click()
        await page.wait_for_timeout(700)
        await button.click()
    else:
        await button.click()


async def run() -> None:
    async with async_playwright() as playwright:
        while True:
            try:
                browser = await playwright.chromium.connect_over_cdp(CDP_URL)
                while True:
                    pages = [page for context in browser.contexts for page in context.pages]
                    page = next((item for item in pages if "meet.google.com" in item.url), None)
                    if page is None:
                        await asyncio.sleep(2)
                        continue
                    await install_observer(page)
                    events = await page.evaluate("() => (window.__tenkoJoinEvents || []).splice(0)")
                    for event in events:
                        name = extract_name(event)
                        if name:
                            register_participant(name)
                            await restart_virtual_microphone(page)
                    await asyncio.sleep(1)
            except Exception as exc:
                with sqlite3.connect(DB_PATH, timeout=30) as con:
                    save_setting(con, "participant_worker_status", "reconnecting")
                    save_setting(con, "participant_worker_detail", f"{type(exc).__name__}: {exc}"[:500])
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())
