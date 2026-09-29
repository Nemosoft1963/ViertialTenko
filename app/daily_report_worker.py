import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from app.db import init_db
from app.recordings import RETENTION_MONTHS, cleanup_expired_recordings

from app.report_mailer import JST, save_setting, send_monthly_report, send_report, setting


DB_PATH = Path(os.getenv("DATABASE_PATH", "/data/tenko.db"))
POLL_SECONDS = max(10, int(os.getenv("REPORT_POLL_SECONDS", "30")))


def now_iso():
    return datetime.now(JST).isoformat(timespec="seconds")


def report_date_for(now):
    return now.date() - timedelta(days=1)


def should_send(now, enabled, last_sent_date):
    report_date = report_date_for(now)
    return (
        enabled
        and now.hour == 1
        and last_sent_date != report_date.isoformat()
    )


def previous_month_start(now):
    current_month = now.date().replace(day=1)
    return (current_month - timedelta(days=1)).replace(day=1)


def should_send_monthly(now, enabled, last_sent_month):
    report_month = previous_month_start(now).strftime("%Y-%m")
    return (
        enabled
        and now.day == 1
        and now.hour == 2
        and last_sent_month != report_month
    )


def should_cleanup_recordings(now, last_cleanup_month):
    return (
        now.day == 1
        and now.hour == 3
        and last_cleanup_month != now.strftime("%Y-%m")
    )


def waiting_detail(daily_enabled, monthly_enabled):
    schedules = []
    if daily_enabled:
        schedules.append("毎日1:00 前日分")
    if monthly_enabled:
        schedules.append("毎月1日2:00 前月分")
    return ((" / ".join(schedules) + " CSV送信待機") if schedules else "CSV自動送信は無効") + f" / 録音{RETENTION_MONTHS}か月保持・毎月1日3:00削除"

def update_worker_state(status, detail, **extra):
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as con:
        save_setting(con, "report_worker_status", status)
        save_setting(con, "report_worker_detail", detail)
        save_setting(con, "report_worker_updated_at", now_iso())
        for key, value in extra.items():
            save_setting(con, key, value)
        con.commit()


def run_once(now=None):
    current = now or datetime.now(JST)
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as con:
        daily_enabled = setting(con, "daily_report_enabled", "0") == "1"
        monthly_enabled = setting(con, "monthly_report_enabled", "0") == "1"
        last_sent_date = setting(con, "daily_report_last_sent_date")
        last_sent_month = setting(con, "monthly_report_last_sent_month")
        last_cleanup_month = setting(con, "recording_cleanup_last_month")

    if should_cleanup_recordings(current, last_cleanup_month):
        month_key = current.strftime("%Y-%m")
        update_worker_state("cleaning_recordings", f"録音データの{RETENTION_MONTHS}か月保持処理を実行中")
        try:
            with closing(sqlite3.connect(DB_PATH, timeout=30)) as con:
                result = cleanup_expired_recordings(con, current, RETENTION_MONTHS)
                con.commit()
        except Exception as exc:
            update_worker_state("error", f"録音削除 / {type(exc).__name__}: {exc}"[:500])
            return False
        update_worker_state(
            "recordings_cleaned",
            f"{result['cutoff_month']}以降を保持 / {result['deleted_records']}件削除",
            recording_cleanup_last_month=month_key,
            recording_cleanup_last_at=now_iso(),
            recording_cleanup_last_deleted=str(result["deleted_records"]),
        )
        return True

    if should_send(current, daily_enabled, last_sent_date):
        report_date = report_date_for(current)
        update_worker_state(
            "sending_daily",
            f"{report_date.isoformat()}分の日次CSVを送信中",
        )
        try:
            result = send_report(DB_PATH, report_date)
        except Exception as exc:
            update_worker_state(
                "error",
                f"日次送信 / {type(exc).__name__}: {exc}"[:500],
            )
            return False
        update_worker_state(
            "sent_daily",
            f"{result['report_date']} / {result['checkin_count']}件 / 日次送信完了",
            daily_report_last_sent_date=report_date.isoformat(),
            daily_report_last_sent_at=now_iso(),
        )
        return True

    if should_send_monthly(current, monthly_enabled, last_sent_month):
        report_month = previous_month_start(current)
        update_worker_state(
            "sending_monthly",
            f"{report_month:%Y-%m}分の月次CSVを送信中",
        )
        try:
            result = send_monthly_report(DB_PATH, report_month)
        except Exception as exc:
            update_worker_state(
                "error",
                f"月次送信 / {type(exc).__name__}: {exc}"[:500],
            )
            return False
        month_key = report_month.strftime("%Y-%m")
        update_worker_state(
            "sent_monthly",
            f"{month_key} / {result['checkin_count']}件 / 月次送信完了",
            monthly_report_last_sent_month=month_key,
            monthly_report_last_sent_at=now_iso(),
        )
        return True

    update_worker_state(
        "waiting",
        waiting_detail(daily_enabled, monthly_enabled),
    )
    return False


def main():
    init_db()
    update_worker_state("starting", "日次・月次CSVメールワーカーを起動中")
    while True:
        try:
            run_once()
        except Exception as exc:
            update_worker_state(
                "error",
                f"{type(exc).__name__}: {exc}"[:500],
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
