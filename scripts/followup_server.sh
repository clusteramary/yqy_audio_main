#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
case "${1:-help}" in
  prepare)
    "$PYTHON" scripts/prepare_followup.py --download
    "$PYTHON" scripts/download_followup_model.py
    "$PYTHON" scripts/prepare_followup_v2.py
    ;;
  features)
    "$PYTHON" scripts/followup.py cache --input data/processed/followup/fq-bank/{train,dev,test}.jsonl --encoder models/pretrained/meld/distilbert-base-uncased --output data/features/followup/fq-bank --device "${DEVICE:-cuda}"
    "$PYTHON" scripts/followup.py cache --input data/processed/followup-v2-mixed/kdconv/{train,dev,test}.jsonl --encoder models/pretrained/followup/bert-base-chinese --output data/features/followup-v2-mixed/kdconv --device "${DEVICE:-cuda}"
    ;;
  train)
    for seed in 42 2026 3407; do
      "$PYTHON" scripts/followup.py train --config configs/followup_fqbank.json --output "outputs/followup/fq-bank/history-$seed" --seed "$seed" --device "${DEVICE:-cuda}"
      for variant in history fixed gated coverage; do
        "$PYTHON" scripts/followup.py train --config configs/followup_kdconv.json --variant "$variant" --output "outputs/followup/kdconv/$variant-$seed" --seed "$seed" --device "${DEVICE:-cuda}"
      done
    done
    ;;
  test)
    # Run only AFTER freezing all choices based on dev results and label audits.
    for seed in 42 2026 3407; do
      "$PYTHON" scripts/followup.py evaluate --checkpoint "outputs/followup/fq-bank/history-$seed/best.pt" --input data/processed/followup/fq-bank/test.jsonl --cache data/features/followup/fq-bank --output "outputs/followup/fq-bank/history-$seed/test.json" --device "${DEVICE:-cuda}"
      for variant in history fixed gated coverage; do
        for condition in original missing shuffled; do
          "$PYTHON" scripts/followup.py evaluate --checkpoint "outputs/followup/kdconv/$variant-$seed/best.pt" --input data/processed/followup-v2-mixed/kdconv/test.jsonl --cache data/features/followup-v2-mixed/kdconv --knowledge "$condition" --output "outputs/followup/kdconv/$variant-$seed/test-$condition.json" --device "${DEVICE:-cuda}"
        done
      done
    done
    ;;
  *)
    echo "Usage: bash scripts/followup_server.sh prepare|features|train|test"
    echo "Default does nothing. Train and test are separate explicit commands."
    ;;
esac
