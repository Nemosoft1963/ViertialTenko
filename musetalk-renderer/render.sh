#!/usr/bin/env bash
set -euo pipefail

cd /workspace/MuseTalk
INPUT_IMAGE=/data/virtual-checkin-operator.png
INPUT_VIDEO=/data/idle-base.mp4
INPUT_AUDIO=${GPU_INPUT_AUDIO:-/meet-config/virtual-mic.wav}
SOURCE_IMAGE=/tmp/musetalk-source.png
SOURCE_VIDEO=/tmp/musetalk-source.mp4
CONFIG=/tmp/tenko-musetalk.yaml
OUTPUT_MP4=${GPU_OUTPUT_MP4:-/meet-config/musetalk-tenko.mp4}
OUTPUT_Y4M=/meet-config/virtual-camera.y4m
TEMP_Y4M=/meet-config/virtual-camera.musetalk.tmp.y4m
WORK_DIR=${GPU_WORK_DIR:-/meet-config/musetalk-work}

test -s "$INPUT_AUDIO"
mkdir -p "$WORK_DIR/results"

if [ -s "$INPUT_VIDEO" ]; then
  ffmpeg -y -v warning -i "$INPUT_VIDEO" -an -r 25 \
    -vf "scale=512:288:force_original_aspect_ratio=decrease,pad=512:288:(ow-iw)/2:(oh-ih)/2" \
    -c:v libx264 -pix_fmt yuv420p -crf 18 "$SOURCE_VIDEO"
  SOURCE="$SOURCE_VIDEO"
  echo "Using idle video: $INPUT_VIDEO"
else
  test -s "$INPUT_IMAGE"
  ffmpeg -y -v warning -i "$INPUT_IMAGE" -frames:v 1 \
    -vf "scale=512:288:force_original_aspect_ratio=decrease,pad=512:288:(ow-iw)/2:(oh-ih)/2" \
    "$SOURCE_IMAGE"
  SOURCE="$SOURCE_IMAGE"
  echo "Using fallback still image: $INPUT_IMAGE"
fi

cat > "$CONFIG" <<EOF
tenko:
  video_path: "$SOURCE"
  audio_path: "$INPUT_AUDIO"
  result_name: "$OUTPUT_MP4"
EOF

rm -rf "$WORK_DIR/results/v15"
python -m scripts.inference \
  --inference_config "$CONFIG" \
  --result_dir "$WORK_DIR/results" \
  --unet_model_path models/musetalkV15/unet.pth \
  --unet_config models/musetalkV15/musetalk.json \
  --whisper_dir models/whisper \
  --version v15 \
  --fps 25 \
  --batch_size 8 \
  --extra_margin 10 \
  --parsing_mode jaw \
  --use_float16 \
  --output_vid_name "$OUTPUT_MP4"

test -s "$OUTPUT_MP4"
if [ "${PUBLISH_Y4M:-1}" = "1" ]; then
  rm -f "$TEMP_Y4M"
  ffmpeg -y -v warning -i "$OUTPUT_MP4" -an \
    -vf "scale=512:288:force_original_aspect_ratio=decrease,pad=512:288:(ow-iw)/2:(oh-ih)/2,fps=15" \
    -pix_fmt yuv420p -f yuv4mpegpipe "$TEMP_Y4M"
  mv -f "$TEMP_Y4M" "$OUTPUT_Y4M"
fi

ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate \
  -show_entries format=duration -of json "$OUTPUT_MP4"
ls -lh "$OUTPUT_MP4" "$OUTPUT_Y4M"
