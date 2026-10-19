#!/usr/bin/env bash
# Run on Arch after installing dependencies and configuring a Linux bw queue.
# sudo ./deploy/deploy-linux.sh
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/school-printer}"
PYTHON="${PYTHON:-python3}"
[[ $(uname -s) == Linux ]] || { echo 'Run this installer on Linux.' >&2; exit 1; }
[[ $EUID == 0 ]] || { echo 'Run with sudo: sudo ./deploy/deploy-linux.sh' >&2; exit 1; }
[[ $INSTALL_DIR =~ ^/[A-Za-z0-9/_.-]+$ && $INSTALL_DIR != / && $INSTALL_DIR != /home/* && $INSTALL_DIR != /root/* ]] || {
  echo 'INSTALL_DIR must be an absolute system path without spaces (default /opt/school-printer).' >&2; exit 1;
}
INSTALL_DIR="$(realpath -m "$INSTALL_DIR")"
[[ $INSTALL_DIR != / && $INSTALL_DIR != /home/* && $INSTALL_DIR != /root/* ]] || {
  echo 'Choose a system directory outside /home and /root.' >&2; exit 1;
}
[[ $INSTALL_DIR != "$ROOT" && $ROOT != "$INSTALL_DIR/"* && $INSTALL_DIR != "$ROOT/"* ]] || {
  echo 'INSTALL_DIR must be separate from this checkout.' >&2; exit 1;
}
for tool in "$PYTHON" rsync systemctl lpstat lpoptions lpadmin cupsctl ippeveprinter avahi-publish-service libreoffice curl runuser; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. See docs/linux.md for Arch packages." >&2; exit 1; }
done
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
systemctl is-active --quiet cups.service || { echo 'Start cups.service first.' >&2; exit 1; }
QUEUE="$("$PYTHON" - "$INSTALL_DIR/.env" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
queue = 'bw'
if path.exists():
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition('=')
        if sep and key == 'SP_CUPS_QUEUE':
            queue = value.strip().strip('"\x27')
print(queue)
PY
)"
[[ $QUEUE =~ ^[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid SP_CUPS_QUEUE.' >&2; exit 1; }
PRINT_BACKEND="${PRINT_BACKEND:-cups}"
[[ $PRINT_BACKEND == cups || $PRINT_BACKEND == lpd ]] || { echo 'PRINT_BACKEND must be cups or lpd.' >&2; exit 1; }
if [[ $PRINT_BACKEND == lpd ]]; then
  command -v pdftops >/dev/null || command -v gs >/dev/null || { echo 'lpd needs poppler (pdftops) or ghostscript.' >&2; exit 1; }
fi
[[ $PRINT_BACKEND == lpd ]] || lpstat -h localhost:631 -p "$QUEUE" >/dev/null || { echo "Configure Linux queue '$QUEUE' first; see docs/linux.md." >&2; exit 1; }
[[ $PRINT_BACKEND == lpd || -n $(lpoptions -h localhost:631 -p "$QUEUE" -l) ]] || { echo "'$QUEUE' needs a Linux driver; a raw queue is not sufficient." >&2; exit 1; }
getent group school-printer >/dev/null || groupadd --system school-printer
id school-printer >/dev/null 2>&1 || useradd --system --gid school-printer --home-dir "$INSTALL_DIR/data" --shell /usr/bin/nologin school-printer
install -d -m 755 "$INSTALL_DIR"
for dir in data spool logs data/airprint; do
  install -d -m 700 -o school-printer -g school-printer "$INSTALL_DIR/$dir"
done

# Stop before replacing code; state and .env are never rsync'd or deleted.
systemctl stop school-airprint.service school-printer.service 2>/dev/null || true
rsync -a --delete --exclude '__pycache__' --exclude '.venv' --exclude '.pytest_cache' --exclude '*.egg-info' "$ROOT/server/" "$INSTALL_DIR/server/"
install -d -m 755 "$INSTALL_DIR/airprint-shim"
for file in shim.py ipp.py print-job.sh; do
  install -m 755 "$ROOT/deploy/airprint-shim/$file" "$INSTALL_DIR/airprint-shim/$file"
done
# A macOS venv cannot be reused on Linux. Always create this one on the target.
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
  "$PYTHON" -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet -e "$INSTALL_DIR/server"
chmod -R a+rX "$INSTALL_DIR/server" "$INSTALL_DIR/venv"
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  "$PYTHON" - "$ROOT/deploy/linux/env.example" "$INSTALL_DIR/.env" <<'PY'
import pathlib, secrets, sys
text = pathlib.Path(sys.argv[1]).read_text()
pathlib.Path(sys.argv[2]).write_text(text.replace('SP_BOOTSTRAP_ADMIN_PASS=\n', f'SP_BOOTSTRAP_ADMIN_PASS={secrets.token_urlsafe(24)}\n'))
PY
fi
if [[ $PRINT_BACKEND == lpd ]] && ! grep -q '^SP_PRINT_BACKEND=' "$INSTALL_DIR/.env"; then
  printf 'SP_PRINT_BACKEND=lpd\n' >> "$INSTALL_DIR/.env"
fi
chown school-printer:school-printer "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env"

# Keep the underlying unauthenticated route closed. Only the proxy is public.
[[ $PRINT_BACKEND == lpd ]] || lpadmin -p "$QUEUE" -o printer-is-shared=false
cupsctl --no-share-printers
for unit in school-printer school-airprint; do
  sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" "$ROOT/deploy/linux/$unit.service" > "/etc/systemd/system/$unit.service"
  chmod 644 "/etc/systemd/system/$unit.service"
done
install -m 755 "$ROOT/tools/school-print-linux" /usr/local/bin/school-print
printf 'INSTALL_DIR=%s\n' "$INSTALL_DIR" > /etc/school-printer.conf
chmod 644 /etc/school-printer.conf
chown -R school-printer:school-printer "$INSTALL_DIR/data" "$INSTALL_DIR/spool" "$INSTALL_DIR/logs"
systemctl daemon-reload
systemctl enable --now cups.service avahi-daemon.service
systemctl enable school-printer.service school-airprint.service
systemctl restart school-printer.service school-airprint.service
ready=0
for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then ready=1; break; fi
  sleep 1
done
[[ $ready == 1 ]] || { echo 'Relay failed to start. Check journalctl -u school-printer.' >&2; exit 1; }
systemctl is-active --quiet school-airprint.service || { echo 'AirPrint failed to start. Check journalctl -u school-airprint.' >&2; exit 1; }
echo "Installed at $INSTALL_DIR. Services start at boot without logging into Hyprland."
echo "Registration: http://<reserved-ip>:8000/register; AirPrint: TCP 8631."
echo "Admin password is in $INSTALL_DIR/.env (sudo cat)."
echo 'Check: school-print status; journalctl -u school-printer -u school-airprint -f'
