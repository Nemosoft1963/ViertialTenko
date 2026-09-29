import csv
import io
import os
import smtplib
import sqlite3
import ssl
from dataclasses import dataclass
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path


DB_PATH = Path(os.getenv("DATABASE_PATH", "/data/tenko.db"))
JST = timezone(timedelta(hours=9), name="JST")
STATUS_NAMES = {
    "awaiting_id": "車番確認待ち",
    "awaiting_name": "氏名確認待ち",
    "in_progress": "点呼中",
    "awaiting_company_message": "会社への伝言待ち",
    "awaiting_company_message_confirmation": "会社への伝言確認待ち",
    "completed": "完了",
    "cancelled": "退出（点呼中断）",
}
CSV_HEADERS = (
    "点呼ID",
    "開始日時",
    "完了日時",
    "車番",
    "氏名",
    "Meet URL",
    "シナリオ",
    "状態",
    "要確認",
    "質問・イベント",
    "回答・内容",
    "回答判定",
    "回答日時",
)


@dataclass(frozen=True)
class MailSettings:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_sender: str
    report_recipient: str


def setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(con, key, value):
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def validate_email(address):
    address = address.strip()
    if any(char in address for char in "\r\n"):
        return False
    if address.count("@") != 1:
        return False
    local, domain = address.rsplit("@", 1)
    if not local or not domain or ".." in local or ".." in domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")
    return set(domain) <= allowed


def load_mail_settings(con):
    raw_port = setting(con, "smtp_port", "465")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("SMTPポート番号が不正です。") from exc
    values = MailSettings(
        smtp_host=setting(con, "smtp_host").strip(),
        smtp_port=port,
        smtp_user=setting(con, "smtp_user").strip(),
        smtp_password=setting(con, "smtp_password"),
        smtp_sender=setting(con, "smtp_sender").strip(),
        report_recipient=setting(con, "report_recipient").strip(),
    )
    if not values.smtp_host:
        raise ValueError("SMTPサーバを設定してください。")
    if not 1 <= values.smtp_port <= 65535:
        raise ValueError("SMTPポート番号が不正です。")
    if not values.smtp_user:
        raise ValueError("SMTPログインユーザーを設定してください。")
    if not values.smtp_password:
        raise ValueError("SMTPパスワードを設定してください。")
    if not validate_email(values.smtp_sender):
        raise ValueError("送信元メールアドレスが不正です。")
    if not validate_email(values.report_recipient):
        raise ValueError("送信先メールアドレスが不正です。")
    return values


def report_day_bounds(report_date):
    start_jst = datetime.combine(report_date, time.min, tzinfo=JST)
    end_jst = datetime.combine(report_date + timedelta(days=1), time.min, tzinfo=JST)
    start_utc = start_jst.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_jst.astimezone(timezone.utc).replace(tzinfo=None)
    return (
        start_utc.strftime("%Y-%m-%d %H:%M:%S"),
        end_utc.strftime("%Y-%m-%d %H:%M:%S"),
    )


def format_jst(value):
    if not value:
        return ""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


def report_rows(con, report_date, end_date=None):
    start_utc, day_end_utc = report_day_bounds(report_date)
    end_utc = report_day_bounds(end_date)[0] if end_date else day_end_utc
    return con.execute(
        """
        SELECT
          c.id, c.joined_at, c.completed_at, c.participant_id,
          c.participant_name, c.meet_url, s.name AS scenario_name,
          c.status, c.alert,
          a.question_text, a.answer_text, a.is_ok, a.answered_at
        FROM checkins c
        JOIN scenarios s ON s.id=c.scenario_id
        LEFT JOIN answers a ON a.checkin_id=c.id
        WHERE c.joined_at >= ? AND c.joined_at < ?
        ORDER BY c.joined_at, c.id, a.id
        """,
        (start_utc, end_utc),
    ).fetchall()


def build_csv_bytes(con, report_date, end_date=None):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    rows = report_rows(con, report_date, end_date)
    checkin_ids = set()
    for row in rows:
        checkin_ids.add(row[0])
        if row[11] is None:
            answer_result = ""
        else:
            answer_result = "正常" if row[11] else "要確認"
        writer.writerow(
            (
                row[0],
                format_jst(row[1]),
                format_jst(row[2]),
                row[3],
                row[4] or "",
                row[5] or "",
                row[6],
                STATUS_NAMES.get(row[7], row[7]),
                "あり" if row[8] else "なし",
                row[9] or "",
                row[10] or "",
                answer_result,
                format_jst(row[12]),
            )
        )
    return output.getvalue().encode("utf-8-sig"), len(checkin_ids)


def build_message(
    settings,
    report_date,
    csv_data,
    checkin_count,
    period_label=None,
    subject_label="前日点呼記録",
    filename=None,
):
    period_label = period_label or report_date.isoformat()
    subject = f"【仮想点呼】{subject_label} {period_label}"
    message = EmailMessage()
    message["From"] = settings.smtp_sender
    message["To"] = settings.report_recipient
    message["Subject"] = subject
    message.set_content(
        f"{period_label}の点呼記録を送付します。\n"
        f"点呼件数: {checkin_count}件\n\n"
        "このメールは仮想点呼システムから自動送信されています。"
    )
    filename = filename or f"tenko-report-{report_date.isoformat()}.csv"
    message.add_attachment(
        csv_data,
        maintype="text",
        subtype="csv",
        filename=filename,
    )
    return message

def deliver_message(settings, message):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        settings.smtp_host,
        settings.smtp_port,
        timeout=30,
        context=context,
    ) as smtp:
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


def next_month(month_start):
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)




def send_report(db_path, report_date):
    with closing(sqlite3.connect(db_path, timeout=30)) as con:
        settings = load_mail_settings(con)
        csv_data, checkin_count = build_csv_bytes(con, report_date)
    message = build_message(settings, report_date, csv_data, checkin_count)
    deliver_message(settings, message)
    return {
        "report_date": report_date.isoformat(),
        "checkin_count": checkin_count,
        "recipient": settings.report_recipient,
    }


def send_monthly_report(db_path, month_start):
    month_start = date(month_start.year, month_start.month, 1)
    end_date = next_month(month_start)
    period_label = f"{month_start.year}年{month_start.month}月"
    with closing(sqlite3.connect(db_path, timeout=30)) as con:
        settings = load_mail_settings(con)
        csv_data, checkin_count = build_csv_bytes(
            con,
            month_start,
            end_date,
        )
    message = build_message(
        settings,
        month_start,
        csv_data,
        checkin_count,
        period_label=period_label,
        subject_label="月次点呼記録",
        filename=f"tenko-report-{month_start:%Y-%m}.csv",
    )
    deliver_message(settings, message)
    return {
        "report_month": month_start.strftime("%Y-%m"),
        "checkin_count": checkin_count,
        "recipient": settings.report_recipient,
    }
