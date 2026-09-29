import os
import wave
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

RECORDINGS_ROOT = Path(os.getenv("RECORDINGS_PATH", "/data/recordings"))
RETENTION_MONTHS = max(1, int(os.getenv("RECORDING_RETENTION_MONTHS", "14")))


def shift_month_start(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def retention_cutoff(now: datetime | date, retention_months: int = RETENTION_MONTHS) -> date:
    current = date(now.year, now.month, 1)
    return shift_month_start(current, -(retention_months - 1))


def active_checkin_id(con):
    row = con.execute(
        "SELECT id FROM checkins WHERE status IN ('awaiting_id','awaiting_name','in_progress','awaiting_company_message','awaiting_company_message_confirmation') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def prepare_recording(checkin_id, pcm, transcript, sample_rate=16_000, recorded_at=None):
    if not checkin_id or not pcm:
        return None
    moment = recorded_at or datetime.now(timezone.utc)
    month = moment.strftime("%Y-%m")
    directory = RECORDINGS_ROOT / month / f"checkin-{checkin_id}"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{moment.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}.wav"
    path = directory / filename
    temporary = path.with_suffix(".tmp.wav")
    with wave.open(str(temporary), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
    temporary.replace(path)
    duration = len(pcm) / (sample_rate * 2)
    return {
        "checkin_id": checkin_id,
        "file_path": str(path),
        "transcript": transcript,
        "duration_seconds": duration,
        "file_size": path.stat().st_size,
        "recorded_at": moment.strftime("%Y-%m-%d %H:%M:%S"),
    }


def persist_recording(con, draft):
    if not draft:
        return None
    cursor = con.execute(
        "INSERT INTO recordings(checkin_id,file_path,transcript,duration_seconds,file_size,recorded_at) VALUES(?,?,?,?,?,?)",
        (
            draft["checkin_id"], draft["file_path"], draft["transcript"],
            draft["duration_seconds"], draft["file_size"], draft["recorded_at"],
        ),
    )
    return cursor.lastrowid


def save_recording(con, checkin_id, pcm, transcript, sample_rate=16_000, recorded_at=None):
    """Compatibility wrapper; realtime workers should prepare before opening a DB transaction."""
    return persist_recording(con, prepare_recording(checkin_id, pcm, transcript, sample_rate, recorded_at))


def safe_recording_path(value):
    root = RECORDINGS_ROOT.resolve()
    path = Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def cleanup_expired_recordings(con, now=None, retention_months=RETENTION_MONTHS):
    current = now or datetime.now(timezone.utc)
    cutoff = retention_cutoff(current, retention_months)
    cutoff_text = cutoff.strftime("%Y-%m-%d 00:00:00")
    rows = con.execute(
        "SELECT id,file_path FROM recordings WHERE recorded_at < ? ORDER BY id", (cutoff_text,)
    ).fetchall()
    removed_files = 0
    removed_bytes = 0
    ids = []
    for recording_id, file_path in rows:
        ids.append(recording_id)
        path = safe_recording_path(file_path)
        if path and path.is_file():
            removed_bytes += path.stat().st_size
            path.unlink()
            removed_files += 1
    if ids:
        con.executemany("DELETE FROM recordings WHERE id=?", ((value,) for value in ids))
    if RECORDINGS_ROOT.exists():
        directories = sorted((item for item in RECORDINGS_ROOT.rglob("*") if item.is_dir()), reverse=True)
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass
    return {
        "cutoff_month": cutoff.strftime("%Y-%m"),
        "deleted_records": len(ids),
        "deleted_files": removed_files,
        "deleted_bytes": removed_bytes,
    }
