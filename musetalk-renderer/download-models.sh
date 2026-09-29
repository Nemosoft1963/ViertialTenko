#!/usr/bin/env bash
set -euo pipefail

cd /workspace/MuseTalk
mkdir -p models/musetalkV15 models/syncnet models/dwpose \
  models/face-parse-bisent models/sd-vae models/whisper

if [ ! -s models/musetalkV15/unet.pth ]; then
  huggingface-cli download TMElyralab/MuseTalk \
    --local-dir models \
    --include "musetalkV15/musetalk.json" "musetalkV15/unet.pth"
fi
if [ ! -s models/sd-vae/diffusion_pytorch_model.bin ]; then
  huggingface-cli download stabilityai/sd-vae-ft-mse \
    --local-dir models/sd-vae \
    --include "config.json" "diffusion_pytorch_model.bin"
fi
if [ ! -s models/whisper/pytorch_model.bin ]; then
  huggingface-cli download openai/whisper-tiny \
    --local-dir models/whisper \
    --include "config.json" "pytorch_model.bin" "preprocessor_config.json"
fi
if [ ! -s models/dwpose/dw-ll_ucoco_384.pth ]; then
  huggingface-cli download yzd-v/DWPose \
    --local-dir models/dwpose \
    --include "dw-ll_ucoco_384.pth"
fi
if [ ! -s models/syncnet/latentsync_syncnet.pt ]; then
  huggingface-cli download ByteDance/LatentSync \
    --local-dir models/syncnet \
    --include "latentsync_syncnet.pt"
fi
if [ ! -s models/face-parse-bisent/79999_iter.pth ]; then
  gdown 154JgKpzCPW82qINcVieuPH3fZ2e0P812 \
    -O models/face-parse-bisent/79999_iter.pth
fi
if [ ! -s models/face-parse-bisent/resnet18-5c106cde.pth ]; then
  curl -fL https://download.pytorch.org/models/resnet18-5c106cde.pth \
    -o models/face-parse-bisent/resnet18-5c106cde.pth
fi

find models -type f -printf '%p %s bytes\n' | sort
