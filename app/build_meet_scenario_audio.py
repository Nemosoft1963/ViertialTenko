import argparse
import asyncio
import json
import sqlite3
import tempfile
import wave
from pathlib import Path

import av
import edge_tts
from app.build_virtual_avatar import DEFAULT_IDLE_VIDEO, build_virtual_camera



RATE = 48_000
SAMPLE_WIDTH = 2


def silence(seconds: float) -> bytes:
    return b"\x00" * int(RATE * SAMPLE_WIDTH * seconds)


def decode_mp3(path: Path) -> bytes:
    result = bytearray()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                result.extend(bytes(converted.planes[0])[: converted.samples * SAMPLE_WIDTH])
        for converted in resampler.resample(None):
            result.extend(bytes(converted.planes[0])[: converted.samples * SAMPLE_WIDTH])
    return bytes(result)


async def synthesize(text: str, output: Path) -> bytes:
    await edge_tts.Communicate(text, "ja-JP-NanamiNeural", rate="-5%").save(str(output))
    return decode_mp3(output)


async def build(
    db_path: Path,
    output: Path,
    scenario_id: int | None,
    wait_seconds: float,
    avatar_source: Path | None = None,
    avatar_output: Path | None = None,
    avatar_width: int = 512,
    avatar_height: int = 288,
    avatar_fps: int = 30,
    avatar_idle_video: Path | None = DEFAULT_IDLE_VIDEO,
) -> None:
    with sqlite3.connect(db_path) as con:
        if scenario_id is None:
            row = con.execute("SELECT value FROM settings WHERE key='active_scenario_id'").fetchone()
            scenario_id = int(row[0]) if row else 1
        scenario = con.execute(
            "SELECT name, questions_json FROM scenarios WHERE id=?", (scenario_id,)
        ).fetchone()
    if scenario is None:
        raise SystemExit(f"scenario {scenario_id} not found")

    questions = json.loads(scenario[1])
    phrases = [
        ("お待たせしました。これから通常の点呼を開始します。", 1.5),
        ("最初に、車番とお名前を確認します。まず、車番を数字ではっきりお答えください。", wait_seconds),
        ("続けて、お名前をフルネームではっきりお答えください。", wait_seconds),
    ]
    phrases.extend(
        (f'{question["text"]}。「はい」または「いいえ」で、はっきりお答えください。', wait_seconds)
        for question in questions
    )
    phrases.append(("点呼は以上です。回答を記録しました。ありがとうございました。", 3.0))

    pcm = bytearray(silence(5.0))
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        for index, (phrase, pause) in enumerate(phrases):
            pcm.extend(await synthesize(phrase, temp / f"phrase-{index}.mp3"))
            pcm.extend(silence(pause))

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    with wave.open(str(temporary), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    temporary.replace(output)
    avatar = None
    if avatar_source is not None and avatar_output is not None:
        avatar = build_virtual_camera(
            avatar_source,
            output,
            avatar_output,
            avatar_width,
            avatar_height,
            avatar_fps,
            avatar_idle_video,
        )
    print(
        json.dumps(
            {"scenario_id": scenario_id, "name": scenario[0], "questions": len(questions), "output": str(output), "avatar": avatar},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/data/tenko.db")
    parser.add_argument("--output", default="/meet-config/virtual-mic.wav")
    parser.add_argument("--scenario-id", type=int)
    parser.add_argument("--wait-seconds", type=float, default=5.0)
    parser.add_argument("--avatar-source", default="/data/virtual-checkin-operator.png")
    parser.add_argument("--avatar-output", default="/meet-config/virtual-camera.y4m")
    parser.add_argument("--avatar-width", type=int, default=512)
    parser.add_argument("--avatar-height", type=int, default=288)
    parser.add_argument("--avatar-fps", type=int, default=30)
    parser.add_argument("--avatar-idle-video", default=str(DEFAULT_IDLE_VIDEO))
    parser.add_argument("--skip-idle-video", action="store_true")
    parser.add_argument("--skip-avatar", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        build(
            Path(args.db),
            Path(args.output),
            args.scenario_id,
            args.wait_seconds,
            None if args.skip_avatar else Path(args.avatar_source),
            None if args.skip_avatar else Path(args.avatar_output),
            args.avatar_width,
            args.avatar_height,
            args.avatar_fps,
            None if args.skip_idle_video else Path(args.avatar_idle_video),
        )
    )
