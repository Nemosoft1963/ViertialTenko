from pathlib import Path

import av


SOURCE = Path("/data/virtual-checkin-operator.png")
OUTPUT = Path("/data/virtual-camera.y4m")
WIDTH = 640
HEIGHT = 360
FPS = 5
SECONDS = 10


with av.open(SOURCE) as container:
    source_frame = next(container.decode(video=0))

frame = source_frame.reformat(width=WIDTH, height=HEIGHT, format="yuv420p")
planes = []
for plane, row_width, rows in (
    (frame.planes[0], WIDTH, HEIGHT),
    (frame.planes[1], WIDTH // 2, HEIGHT // 2),
    (frame.planes[2], WIDTH // 2, HEIGHT // 2),
):
    raw = bytes(plane)
    planes.append(b"".join(
        raw[row * plane.line_size:row * plane.line_size + row_width]
        for row in range(rows)
    ))

with OUTPUT.open("wb") as output:
    output.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F{FPS}:1 Ip A1:1 C420jpeg\n".encode())
    payload = b"FRAME\n" + b"".join(planes)
    for _ in range(FPS * SECONDS):
        output.write(payload)

print(f"created {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
