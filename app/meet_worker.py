import asyncio
import os
import sqlite3
from datetime import datetime, timezone

from playwright.async_api import async_playwright

DB_PATH = os.getenv("DATABASE_PATH", "/data/tenko.db")
MODE = os.getenv("APP_MODE", "simulation")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def update_status(status, detail=""):
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        values = {
            "worker_status": status,
            "worker_detail": detail[:500],
            "worker_updated_at": now_iso(),
        }
        for key, value in values.items():
            con.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))


def load_settings():
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        rows = dict(con.execute("SELECT key,value FROM settings").fetchall())
    return {
        "meet_url": rows.get("meet_url") or os.getenv("MEET_URL", ""),
        "scenario_id": int(rows.get("active_scenario_id") or os.getenv("ACTIVE_SCENARIO_ID", "1")),
        "name": rows.get("bot_display_name") or os.getenv("BOT_DISPLAY_NAME", "仮想点呼"),
    }


async def run_meet():
    cfg = load_settings()
    if MODE != "meet":
        update_status("standby", "シミュレーションモードで待機中")
        await asyncio.sleep(10)
        return
    if not cfg["meet_url"]:
        update_status("configuration_required", "Google Meet URLを設定してください")
        await asyncio.sleep(10)
        return

    update_status("connecting", "Google Meetへ接続しています")
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            "/profile",
            headless=True,
            args=[
                "--use-fake-ui-for-media-stream",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
            locale="ja-JP",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(cfg["meet_url"], wait_until="domcontentloaded", timeout=60000)

            textbox = page.get_by_role("textbox")
            if await textbox.count():
                await textbox.first.fill(cfg["name"])

            joined = False
            for label in ["参加をリクエスト", "今すぐ参加", "Ask to join", "Join now"]:
                button = page.get_by_role("button", name=label)
                if await button.count():
                    await button.first.click()
                    joined = True
                    break

            if not joined:
                title = await page.title()
                body_text = (await page.locator("body").inner_text())[:300]
                await page.screenshot(path="/data/meet-debug.png", full_page=True)
                update_status("attention_required", f"参加ボタン未検出: {title} | {body_text}")
                await page.wait_for_timeout(30000)
                return

            update_status("waiting_admission", "入室許可または参加者を待っています")
            for _ in range(60):
                await page.wait_for_timeout(5000)
                update_status("connected", "Meet会議室を監視中")
        finally:
            await context.close()


async def main():
    update_status("starting", "ワーカーを起動しています")
    while True:
        try:
            await run_meet()
        except Exception as exc:
            update_status("error", f"{type(exc).__name__}: {exc}")
            print(f"meet-worker: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
