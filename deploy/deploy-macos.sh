#!/usr/bin/env bash
# Install or update the school-printer API on a Mac.
#
# The Pi was one of two machines: it ran the API and CUPS, and shipped Office
# files over the network to an iMac that had Word installed, then polled for the
# PDF to come back. Running the API on the Mac itself deletes that whole relay
# -- the conversion becomes a local subprocess call -- and frees the Pi.
#
# What made this possible without a rewrite: macOS ships CUPS as part of the OS
# (/usr/bin/lp, lpstat, lpoptions, cancel are all present), and the server is
# plain FastAPI, SQLite and subprocess calls with nothing Linux-specific in it.
#
# Usage, run ON the Mac:
#   ./deploy/deploy-macos.sh
#
# Prerequisites, checked below and not installed for you:
#   brew install poppler          # pdftoppm and pdfinfo, for previews
#   a CUPS queue named "bw"       # lpadmin -p bw -v ipp://<printer>/ipp/print -m everywhere -E
#
# Idempotent: safe to re-run for updates. The live config at
# /usr/local/school-printer/.env sits outside the copied tree, so an update can
# never clobber it -- same rule as the Pi deploy.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/usr/local/school-printer}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.school.printer"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "Checking prerequisites"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS. Use deploy/deploy.sh for the Pi." >&2
  exit 1
fi

for tool in lp lpstat lpoptions; do
  command -v "$tool" >/dev/null || { echo "missing $tool — is CUPS enabled?" >&2; exit 1; }
done

for tool in pdftoppm pdfinfo; do
  command -v "$tool" >/dev/null || {
    echo "missing $tool — run: brew install poppler" >&2
    exit 1
  }
done

QUEUE="${SP_CUPS_QUEUE:-bw}"
if ! lpstat -p "$QUEUE" >/dev/null 2>&1; then
  echo "No CUPS queue named '$QUEUE'. Add the printer first:" >&2
  echo "  lpadmin -p $QUEUE -v ipp://<printer-ip>/ipp/print -m everywhere -E" >&2
  exit 1
fi
echo "queue '$QUEUE' is present"

# A raw queue silently ignores every print option — duplex, scaling, copies.
if [ -z "$(lpoptions -p "$QUEUE" -l 2>/dev/null)" ]; then
  echo "WARNING: '$QUEUE' is a raw queue with no driver. Print options will be" >&2
  echo "         ignored. /health reports this as queue_is_raw." >&2
fi

say "Installing to $INSTALL_DIR"

sudo mkdir -p "$INSTALL_DIR"/{data,spool,logs}
sudo chown -R "$(id -u):$(id -g)" "$INSTALL_DIR"

# Copy the server tree only. Anything outside server/ — including .env — is left
# alone on purpose.
rsync -a --delete \
  --exclude '__pycache__' --exclude '.venv' --exclude '.pytest_cache' \
  "$SCRIPT_DIR/server/" "$INSTALL_DIR/server/"

say "Python environment"

if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
  python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -e "$INSTALL_DIR/server"

if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$SCRIPT_DIR/deploy/env.example" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
  say "Created $INSTALL_DIR/.env from the example — edit it before first use."
fi

say "LaunchAgent"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SCRIPT_DIR/deploy/$LABEL.plist" "$AGENT"

# bootout first so a re-run picks up a changed plist. Ignore the error when it
# was not loaded to begin with.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

say "Done"
cat <<NOTE
The API is on http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this-mac>'):8000

Two things this Mac needs that the Pi did not:

  1. Automatic login. A LaunchAgent only runs in a logged-in GUI session --
     required, because Office -> PDF conversion drives Word through AppleScript
     and GUI apps cannot launch without one. Without automatic login the print
     queue is down after every restart.

  2. No sleep. System Settings -> Displays -> Advanced -> "Prevent automatic
     sleeping on power adapter when the display is off".

Check it:  curl -s localhost:8000/health | python3 -m json.tool
Logs:      tail -f $INSTALL_DIR/logs/stderr.log
NOTE
