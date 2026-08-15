#!/usr/bin/env bash
# Deploy the school-printer API to the Pi.
# Idempotent: safe to re-run for updates.
#
# Usage:
#   PI_HOST=kenny_lhs@192.168.1.106 SUDO_PW=... ./deploy/deploy.sh
#
# Environment:
#   PI_HOST  ssh target (user@host). Required if the Pi is not at the default.
#   SUDO_PW  sudo password on the Pi. If unset, passwordless sudo (NOPASSWD)
#            is assumed. Never hardcode this in the repo.
#
# Note: the Pi's live config lives at /opt/school-printer/.env — OUTSIDE the
# rsync'd tree — so deploys can never clobber it. See C1 in the audit plan.

set -euo pipefail

PI_HOST="${PI_HOST:-kenny_lhs@192.168.1.106}"
REMOTE_DIR="/opt/school-printer"
ENV_FILE="$REMOTE_DIR/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Run a command on the Pi as root. The command is read from stdin, so quotes and
# special characters in it are safe. When SUDO_PW is set it is sent as the first
# line of stdin for `sudo -S` to consume, which keeps it out of the remote
# process list (unlike passing it on the command line).
remote_sudo() {
  if [ -n "${SUDO_PW:-}" ]; then
    { printf '%s\n' "$SUDO_PW"; cat; } | ssh "$PI_HOST" "sudo -S -p '' bash -s"
  else
    ssh "$PI_HOST" "sudo -n bash -s"
  fi
}

echo "==> Target: $PI_HOST:$REMOTE_DIR"

# 0. Fail fast if the Pi isn't reachable, rather than half-deploying.
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_HOST" true 2>/dev/null; then
  echo "ERROR: cannot ssh to $PI_HOST" >&2
  echo "       Set PI_HOST to the Pi's current address, e.g.:" >&2
  echo "         PI_HOST=kenny_lhs@192.168.1.123 ./deploy/deploy.sh" >&2
  exit 1
fi

# 1. Ensure the directory layout exists and is owned by the service user.
echo "==> directories"
REMOTE_USER="${PI_HOST%@*}"
remote_sudo <<EOF
set -e
mkdir -p "$REMOTE_DIR/server" "$REMOTE_DIR/data" "$REMOTE_DIR/spool"
chown -R "$REMOTE_USER:$REMOTE_USER" "$REMOTE_DIR"
EOF

# 2. Ensure .env exists BEFORE syncing, and never overwrite it.
#    It lives outside $REMOTE_DIR/server so --delete cannot touch it.
echo "==> ensure .env ($ENV_FILE)"
scp -q "$SCRIPT_DIR/deploy/env.example" "$PI_HOST:/tmp/sp_env.example"
ssh "$PI_HOST" "test -f '$ENV_FILE' || { cp /tmp/sp_env.example '$ENV_FILE'; chmod 600 '$ENV_FILE'; echo '    (created from env.example — set SP_BOOTSTRAP_ADMIN_PASS)'; }"

# 3. Sync the server/ dir. --delete keeps the remote tree clean; .env is
#    excluded belt-and-braces even though it now lives one level up.
echo "==> rsync app code"
rsync -az --delete \
  --exclude '__pycache__' --exclude '.venv' --exclude '*.pyc' \
  --exclude '.env' \
  "$SCRIPT_DIR/server/" "$PI_HOST:$REMOTE_DIR/server/"

# 4. Create/refresh venv
echo "==> venv (python3)"
ssh "$PI_HOST" "bash -s" <<EOF
set -e
if [ ! -d "$REMOTE_DIR/venv" ]; then
  python3 -m venv "$REMOTE_DIR/venv"
fi
"$REMOTE_DIR/venv/bin/pip" install --upgrade pip --quiet
"$REMOTE_DIR/venv/bin/pip" install --quiet -e "$REMOTE_DIR/server"
EOF

# 5. Install systemd unit and restart so the new code actually takes effect.
echo "==> systemd unit"
scp -q "$SCRIPT_DIR/deploy/school-printer.service" "$PI_HOST:/tmp/school-printer.service"
remote_sudo <<'EOF'
set -e
install -m 644 /tmp/school-printer.service /etc/systemd/system/school-printer.service
systemctl daemon-reload
systemctl enable school-printer.service
systemctl restart school-printer.service
EOF

# 6. Report health.
echo "==> post-deploy status"
ssh "$PI_HOST" "systemctl is-active school-printer && systemctl show -p ActiveEnterTimestamp --value school-printer"

echo
echo "==> Deploy complete."
echo "    Config:  $ENV_FILE  (preserved across deploys)"
echo "    Status:  ssh $PI_HOST 'sudo systemctl status school-printer'"
echo "    Logs:    ssh $PI_HOST 'sudo journalctl -u school-printer -f'"
