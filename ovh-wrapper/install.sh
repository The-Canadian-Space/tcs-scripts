#!/usr/bin/env bash
# install.sh — one-time bootstrap for /opt/tcs/ovh-wrapper on the VPS.
#
# Idempotent: safe to re-run (skips venv creation if present, keeps
# existing .env). Run as ubuntu.
#
# Docs: README.md in this directory + tcs-docs/docs/infrastructure/ovh-api.md

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d venv ]; then
  echo "[install] creating Python venv..."
  python3 -m venv venv
fi

echo "[install] installing/upgrading dependencies..."
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "[install] .env not present — copying from .env.example"
  cp .env.example .env
  chmod 600 .env
  echo "[install]"
  echo "[install] NEXT: edit .env with the OVH triplet from 1Password entry OVH_API_TCS"
  echo "[install]   nano $DIR/.env"
  echo "[install]"
  echo "[install] Then verify auth:"
  echo "[install]   source $DIR/venv/bin/activate && python3 wrapper.py me"
else
  echo "[install] .env already present — leaving it alone (chmod fix if needed)"
  chmod 600 .env
fi

# Ensure ubuntu owns everything (in case install ran as root)
if [ "$(id -u)" = "0" ]; then
  chown -R ubuntu:ubuntu "$DIR"
fi

echo "[install] done. Wrapper: $DIR/wrapper.py"
./venv/bin/python3 wrapper.py --help || true
