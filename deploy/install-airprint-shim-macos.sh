#!/bin/bash
# Run ON the iMac (or via ssh). Installs the "School Print" AirPrint front end
# as a LaunchAgent. No sudo. Rollback: remove with --uninstall.
set -euo pipefail

LABEL=com.school.airprint
INSTALL_DIR=${INSTALL_DIR:-$HOME/school-printer}
SRC="$(cd "$(dirname "$0")" && pwd)/airprint-shim"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ $(uname -s) != Darwin ]]; then echo 'Run this on the iMac.' >&2; exit 1; fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for _ in $(seq 1 50); do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.2
done
if [[ ${1:-} == --uninstall ]]; then
  rm -f "$AGENT"
  echo 'School Print removed; bw is unchanged.'
  exit 0
fi

mkdir -p "$INSTALL_DIR/airprint-shim/spool" "$INSTALL_DIR/logs" "$HOME/Library/LaunchAgents"
install -m 755 "$SRC/print-job.sh" "$SRC/shim.py" "$SRC/ipp.py" "$INSTALL_DIR/airprint-shim/"
sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" "$SRC/com.school.airprint.plist" > "$AGENT"

launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true   # an earlier `school-print stop` leaves it disabled
for attempt in 1 2 3; do
  launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null && break
  [[ $attempt == 3 ]] && { echo 'launchctl bootstrap failed.' >&2; exit 1; }
  sleep 1
done
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 3
/usr/bin/ipptool -q -tv ipp://localhost:8631/ipp/print /usr/share/cups/ipptool/get-printer-attributes.test \
  | /usr/bin/grep -E 'urf-supported|document-format-supported|PASS|FAIL'
echo "School Print service running on port 8631 (profile printers). The open Bonjour printer stays off until: tools/school-print on"
