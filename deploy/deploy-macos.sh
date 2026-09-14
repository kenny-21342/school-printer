#!/usr/bin/env bash
# Install or update the school-printer relay on the iMac. This is the only
# deploy script: the iMac is the only machine in the pipeline.
#
# It used to be two. The Pi ran the API and CUPS and shipped Office files over
# the network to the iMac, which had Word installed, then polled a shared folder
# for the PDF to come back. Running everything on the iMac deletes that relay --
# conversion is now a local subprocess call (server/app/office.py) -- and frees
# the Pi entirely.
#
# What made the move possible without a rewrite: macOS ships CUPS as part of the
# OS (/usr/bin/lp, lpstat, lpoptions, cancel are all present), and the server is
# plain FastAPI, SQLite and subprocess calls with nothing Linux-specific in it.
#
# Usage, run ON the iMac:
#   ./deploy/deploy-macos.sh
#
# Prerequisites, checked below and not installed for you:
#   python3 >= 3.11               # set PYTHON=/path/to/python3 if it is not on PATH
#   Microsoft Word/Excel/PowerPoint, opened and signed in once at the machine
#     (an unactivated Word hangs in its setup wizard instead of converting)
#   a CUPS queue named "bw"       # lpadmin -p bw -v ipp://<printer>/ipp/print -m everywhere -E
#
# INSTALL_DIR defaults to ~/school-printer, which needs no sudo -- the service
# binds an unprivileged port and keeps all its state in one directory. Point it
# at /usr/local/school-printer (or anywhere else) if you prefer; the script uses
# sudo only when the target is not already writable.
#
# Idempotent: safe to re-run for updates. The live config at
# $INSTALL_DIR/.env sits outside the copied tree, so an update can never
# clobber it.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/school-printer}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.school.printer"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="${PYTHON:-python3}"

# The install directory holds a copy of server/, so it must not BE the repo.
if [ "$INSTALL_DIR" = "$SCRIPT_DIR" ]; then
  echo "INSTALL_DIR must not be the repository itself ($SCRIPT_DIR)." >&2
  echo "The deploy copies server/ into it and would overwrite your checkout." >&2
  exit 1
fi

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "Checking prerequisites"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS; the relay runs on the iMac." >&2
  exit 1
fi

for tool in lp lpstat lpoptions; do
  command -v "$tool" >/dev/null || { echo "missing $tool — is CUPS enabled?" >&2; exit 1; }
done

# The server needs 3.11+. A bare macOS has no usable python3 at all (/usr/bin/python3
# is a stub that only prompts to install the Command Line Tools), so check the
# version rather than mere presence.
command -v "$PYTHON" >/dev/null || {
  echo "no python3 found. Install one, or set PYTHON=/path/to/python3." >&2
  echo "  uv works well and needs no sudo:" >&2
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13" >&2
  exit 1
}
PYVER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
case "$PYVER" in
  3.1[1-9]|3.[2-9][0-9]) echo "python $PYVER at $(command -v "$PYTHON")" ;;
  *) echo "python 3.11+ required, found ${PYVER}. Set PYTHON=/path/to/python3." >&2; exit 1 ;;
esac

# Office -> PDF conversion drives these three through AppleScript. A missing app
# is not fatal -- PDFs and images still print -- but the matching file type will
# fail, so say which one now rather than one job at a time later.
MISSING_OFFICE=()
for app in "Microsoft Word" "Microsoft Excel" "Microsoft PowerPoint"; do
  [ -d "/Applications/$app.app" ] || MISSING_OFFICE+=("$app")
done
if [ ${#MISSING_OFFICE[@]} -eq 0 ]; then
  echo "Office apps present: Word, Excel, PowerPoint"
  # Installed is not the same as usable. Without activation Word blocks in its
  # first-run wizard and every conversion times out with nothing to point at.
  if [ ! -e /Library/Preferences/com.microsoft.office.licensingV2.plist ] \
     && [ ! -e "$HOME/Library/Group Containers/UBF8T346G9.Office/Licenses" ] \
     && [ ! -e "$HOME/Library/Group Containers/UBF8T346G9.Office/com.microsoft.Office365.plist" ]; then
    echo "WARNING: Office does not look activated. Open Word, Excel and" >&2
    echo "         PowerPoint once at this machine and sign in, or every" >&2
    echo "         Office job will hang until it times out." >&2
  fi
else
  echo "WARNING: not installed: ${MISSING_OFFICE[*]}" >&2
  echo "         Those file types will be accepted and then fail to convert." >&2
fi

QUEUE="${SP_CUPS_QUEUE:-bw}"
HAVE_QUEUE=1
if ! lpstat -p "$QUEUE" >/dev/null 2>&1; then
  # Not fatal. `lpadmin -m everywhere` has to talk to the printer to fetch its
  # capabilities, so the queue cannot be created until this machine is on the
  # same network as it. Everything up to the final lp call still works, and
  # /health reports the queue state.
  HAVE_QUEUE=0
  echo "WARNING: no CUPS queue named '$QUEUE' — print jobs will fail until it exists." >&2
  echo "         Once the printer is reachable:" >&2
  echo "           lpadmin -p $QUEUE -v ipp://<printer-ip>/ipp/print -m everywhere -E" >&2
else
  echo "queue '$QUEUE' is present"
fi

# A raw queue silently ignores every print option — duplex, scaling, copies.
if [ "$HAVE_QUEUE" = 1 ] && [ -z "$(lpoptions -p "$QUEUE" -l 2>/dev/null)" ]; then
  echo "WARNING: '$QUEUE' is a raw queue with no driver. Print options will be" >&2
  echo "         ignored. /health reports this as queue_is_raw." >&2
fi

say "Installing to $INSTALL_DIR"

# sudo only when the target is somewhere this user cannot write. A home-directory
# install -- the default -- never asks for a password, which is what lets the
# whole deploy run over ssh.
if mkdir -p "$INSTALL_DIR"/{data,spool,logs} 2>/dev/null; then
  echo "created $INSTALL_DIR (no sudo needed)"
else
  echo "$INSTALL_DIR is not writable; using sudo"
  sudo mkdir -p "$INSTALL_DIR"/{data,spool,logs}
  sudo chown -R "$(id -u):$(id -g)" "$INSTALL_DIR"
fi

# Copy the server tree only. Anything outside server/ — including .env — is left
# alone on purpose. The AppleScript converter lives inside server/app/scripts/
# precisely so that this one rsync carries it.
rsync -a --delete \
  --exclude '__pycache__' --exclude '.venv' --exclude '.pytest_cache' \
  "$SCRIPT_DIR/server/" "$INSTALL_DIR/server/"

say "Python environment"

if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
  "$PYTHON" -m venv "$INSTALL_DIR/venv"
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
# The plist in deploy/ is a template: substitute the install directory chosen
# above rather than hardcoding one path for every machine.
sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" "$SCRIPT_DIR/deploy/$LABEL.plist" > "$AGENT"

# Bootout first so a re-run picks up a changed plist. Ignore the error when it
# was not loaded to begin with.
#
# bootout returns before launchd has finished tearing the service down, and
# bootstrapping into that gap fails with "Bootstrap failed: 5: Input/output
# error" -- which leaves the queue DOWN after what looks like a routine
# redeploy. So wait for the old service to actually disappear, then load.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for _ in $(seq 1 50); do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.2
done

for attempt in 1 2 3; do
  if launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null; then
    break
  fi
  if [ "$attempt" = 3 ]; then
    echo "launchctl bootstrap failed three times. The service is NOT running." >&2
    echo "  launchctl print gui/$(id -u)/$LABEL" >&2
    exit 1
  fi
  sleep 1
done
launchctl kickstart -k "gui/$(id -u)/$LABEL"

# Do not report success until it actually answers.
for _ in $(seq 1 30); do
  curl -fsS -m 2 "http://127.0.0.1:${SP_PORT:-8000}/health" >/dev/null 2>&1 && break
  sleep 1
done

say "Done"
cat <<NOTE
The API is on http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this-mac>'):8000

Four things this Mac needs:

  1. Automatic login. A LaunchAgent only runs in a logged-in GUI session --
     required, because Office -> PDF conversion drives Word through AppleScript
     and GUI apps cannot launch without one. Without automatic login the print
     queue is down after every restart.

  2. No sleep. System Settings -> Displays -> Advanced -> "Prevent automatic
     sleeping on power adapter when the display is off".

  3. Office signed in, once per app. Open Word, Excel and PowerPoint here and
     finish the first-run/activation wizard. An unactivated Word does not fail,
     it blocks in Office's setup UI, so jobs sit in "Converting..." until they
     time out. /health reports office.licensed.

  4. Automation permission, once per Office app. The first Word, Excel or
     PowerPoint job pops a "school-printer wants to control ..." dialog and the
     conversion fails until someone clicks OK. Print one of each while sitting
     at the machine, or pre-approve under System Settings -> Privacy & Security
     -> Automation. /health reports office.available and office.gui_session.

Check it:  curl -s localhost:8000/health | python3 -m json.tool
Logs:      tail -f $INSTALL_DIR/logs/stderr.log
NOTE
