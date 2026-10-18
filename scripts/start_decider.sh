#!/usr/bin/env bash
# Start the local Strands Decider 2B server (Linux / WSL2 with an NVIDIA GPU).
# One-time setup:  uv venv -p 3.12 .venv-decider && uv pip install -p .venv-decider/bin/python \
#                    strands-decider flash-linear-attention
set -euo pipefail
cd "$(dirname "$0")/.."
CKPT="${DECIDER_CHECKPOINT:-StrandsAgents/strands-decider-2B-hobson-v19}"
DEVICE="${DECIDER_DEVICE:-cuda}"
PORT="${DECIDER_PORT:-8099}"
exec .venv-decider/bin/strands-decider serve "$CKPT" --device "$DEVICE" --port "$PORT"
