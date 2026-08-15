#!/usr/bin/env bash
# Deploy the school-printer API to the Pi.
# Idempotent: safe to re-run for updates.
#
# Usage:
#   ./deploy/deploy.sh
#
# Requires: ssh access (key-based) to kenny_lhs@192.168.1.106

set -euo pipefail

PI_HOST="${PI_HOST:-kenny_lhs@192.168.1.106}"
REMOTE_DIR="/opt/school-printer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDO_PW="${SUDO_PW:-Ro3t6a50}"

# Helper: run a remote command, with sudo password ready if needed
remote_sudo() { ssh "$PI_HOST" "echo '$SUDO_PW' | sudo -S bash -c '$1'"; }

echo "==> Target: $PI_HOST:$REMOTE_DIR"

# 1. Sync the server/ dir
echo "==> rsync app code"
remote_sudo "mkdir -p $REMOTE_DIR/server $REMOTE_DIR/data $REMOTE_DIR/spool && chown -R kenny_lhs:kenny_lhs $REMOTE_DIR"
rsync -az --delete --exclude '__pycache__' --exclude '.venv' --exclude '*.pyc' \
  "$SCRIPT_DIR/server/" "$PI_HOST:$REMOTE_DIR/server/"

# 2. Ensure .env exists (don't overwrite if present)
echo "==> ensure .env"
scp -q "$SCRIPT_DIR/deploy/env.example" "$PI_HOST:/tmp/sp_env.example"
ssh "$PI_HOST" "test -f $REMOTE_DIR/server/.env || cp /tmp/sp_env.example $REMOTE_DIR/server/.env"

# 3. Create/refresh venv
echo "==> venv (python3)"
ssh "$PI_HOST" "bash -s" <<EOF
set -e
if [ ! -d $REMOTE_DIR/venv ]; then
  python3 -m venv $REMOTE_DIR/venv
fi
$REMOTE_DIR/venv/bin/pip install --upgrade pip --quiet
$REMOTE_DIR/venv/bin/pip install --quiet -e $REMOTE_DIR/server
EOF

# 4. Install systemd unit
echo "==> systemd unit"
scp -q "$SCRIPT_DIR/deploy/school-printer.service" "$PI_HOST:/tmp/school-printer.service"
remote_sudo "install -m 644 /tmp/school-printer.service /etc/systemd/system/school-printer.service && systemctl daemon-reload && systemctl enable school-printer.service"

echo
echo "==> Deploy complete."
echo "    First-time setup:  edit $REMOTE_DIR/server/.env to set SP_BOOTSTRAP_ADMIN_PASS"
echo "    Start:             ssh $PI_HOST 'sudo systemctl start school-printer'"
echo "    Status:            ssh $PI_HOST 'sudo systemctl status school-printer'"
echo "    Docs:              http://192.168.1.106:8000/docs"
