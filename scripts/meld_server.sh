#!/usr/bin/env bash
# Linux server entry point. No training without the explicit train/run stage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
stage="${1:-check}"
variant="${2:-full}"
seed="${3:-42}"

if [[ "$stage" == "setup" ]]; then
    "${PYTHON_BIN:-python3}" -m venv .venv-meld-server
    source .venv-meld-server/bin/activate
    python -m pip install --upgrade pip
    # Override TORCH_INDEX_URL for the server's driver. Does not install/modify drivers.
    python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url "${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu121}"
    python -m pip install -r requirements-meld.txt
    python -m pip install -e .
    exit 0
fi
if [[ -f .venv-meld-server/bin/activate ]]; then
    source .venv-meld-server/bin/activate
fi
if [[ "$stage" == "prepare" || "$stage" == "run" ]]; then
    python scripts/prepare_meld.py download --media
    python scripts/prepare_meld.py extract
    python scripts/prepare_meld.py prepare --require-media
    python scripts/meld_features.py download-models
fi
if [[ "$stage" == "features" || "$stage" == "run" ]]; then
    python scripts/meld_features.py extract --device cuda
fi
if [[ "$stage" == "check" || "$stage" == "run" || "$stage" == "train" ]]; then
    python scripts/train_meld.py preflight --forward --output outputs/meld/preflight.json
fi
if [[ "$stage" == "train" || "$stage" == "run" ]]; then
    python scripts/train_meld.py train --variant "$variant" --seed "$seed" --output "outputs/meld/${variant}-seed${seed}"
fi
if [[ "$stage" == "evaluate" ]]; then
    python scripts/train_meld.py evaluate --device cuda --checkpoint "outputs/meld/${variant}-seed${seed}/best.pt" --output "outputs/meld/${variant}-seed${seed}/test"
fi
case "$stage" in
    setup|prepare|features|check|train|run|evaluate) ;;
    *) echo "Usage: bash scripts/meld_server.sh {setup|prepare|features|check|train|run|evaluate} [variant] [seed]" >&2; exit 2 ;;
esac
