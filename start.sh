#!/usr/bin/env bash
# MFP Print & Scan Server - Linux launcher: ./start.sh
# First run creates .venv and installs dependencies; later they're
# reinstalled only when requirements.txt changes.
# ./start.sh --setup-only: just prepare .venv and exit (e.g. before
# installing the systemd service from deploy/).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "[MFP] Creating virtual environment .venv ..."
    python3 -m venv .venv
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
    echo "[MFP] Installing dependencies ..."
    .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
    cp requirements.txt .venv/requirements.installed
fi
if [ "${1:-}" = "--setup-only" ]; then
    exit 0
fi

# Port/scheme as the server will see them: config.ini, overridden by MFP_*.
read -r port scheme <<< "$(.venv/bin/python -c '
from app.config import settings
print(settings.port, "https" if settings.ssl_certfile and settings.ssl_keyfile else "http")
')"
echo
echo "[MFP] Web UI:         $scheme://localhost:$port"
for ip in $(hostname -I 2>/dev/null); do
    echo "[MFP] On the network: $scheme://$ip:$port"
done
echo "[MFP] Stop the server: Ctrl+C"
echo

exec .venv/bin/python run.py
