#!/bin/bash
set -euo pipefail
if [ -n "${1:-}" ]; then
  BATCH_SIZE="$1"
else
  BATCH_SIZE="${BATCH_SIZE:-32}"
fi
cd /root/piper_workspace/piper/src/python
source venv/bin/activate
export CUDA_VISIBLE_DEVICES=0,1
export TMPDIR=/root/tmp
nohup python -m piper_train \
  --dataset-dir /root/piper_workspace/datasets/ava_rechunk \
  --batch-size "$BATCH_SIZE" \
  --validation-split 0.05 \
  --num-test-examples 5 \
  --max_epochs 10000 \
  --checkpoint-epochs 50 \
  --quality high \
  --accelerator gpu \
  --devices 2 \
  --strategy ddp \
  --resume_from_checkpoint /root/piper_workspace/datasets/ava_rechunk/lightning_logs/version_0/checkpoints/epoch=2824-step=841206.ckpt \
  >> /root/training_ava.log 2>&1 &
echo $! > /root/training_ava.pid
