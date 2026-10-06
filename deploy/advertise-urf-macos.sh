#!/bin/bash
# Run ON the iMac after enable-native-printing-macos.sh. iOS only lists a
# printer in Share -> Print when its Bonjour record carries a URF key, and the
# Fujifilm PPD gives CUPS nothing to build one from. This adds a
# *cupsUrfSupported line to a copy of the original PPD and reinstalls it on
# the existing bw queue (no second destination). The staged original is never
# modified. Needs no root: the iMac account is in _lpadmin. If the ipptool check does not show urf-supported afterwards, the
# previous PPD is restored.
set -euo pipefail

QUEUE=bw
STAGING_DIR=/Users/Shared/school-printer-drivers
PPD_SOURCE="$STAGING_DIR/bw-original.ppd"
PPD_PATCHED="$STAGING_DIR/bw-urf.ppd"
# W8 = 8-bit grey, RS600 = 600 dpi, DM1 = long-edge duplex; no SRGB24, since
# the queue is black-and-white (FFColorMode=Black).
URF='V1.4,CP1,W8,RS600,DM1'
BACKUP="$HOME/school-printer-backups/urf-$(date +%Y%m%d-%H%M%S)"

if [[ $(uname -s) != Darwin ]]; then
  echo 'Run this on the iMac.' >&2
  exit 1
fi
if [[ ! -f "$PPD_SOURCE" ]]; then
  echo "Missing $PPD_SOURCE; run enable-native-printing-macos.sh first." >&2
  exit 1
fi

umask 077
mkdir -p "$BACKUP"
cp -p "/etc/cups/ppd/$QUEUE.ppd" "$BACKUP/$QUEUE.ppd"

# Insert before the first *cupsFilter line; drop any earlier URF line so
# reruns are idempotent.
/usr/bin/awk -v urf="$URF" '
  /^\*cupsUrfSupported:/ { next }
  /^\*cupsFilter:/ && !done { print "*cupsUrfSupported: \"" urf "\""; done = 1 }
  { print }
  END { if (!done) exit 1 }
' "$PPD_SOURCE" > "$PPD_PATCHED"
/usr/bin/cupstestppd "$PPD_PATCHED"

/usr/sbin/lpadmin -p "$QUEUE" -P "$PPD_PATCHED"
# lpadmin -P resets per-queue options; reassert the ones the pilot relies on.
/usr/sbin/lpadmin -p "$QUEUE" -o FFColorMode=Black -o printer-is-shared=true -E
sleep 3

if /usr/bin/ipptool -tv "ipp://localhost:631/printers/$QUEUE" \
    /usr/share/cups/ipptool/get-printer-attributes.test | /usr/bin/grep -q 'urf-supported'; then
  echo 'urf-supported is now published.'
  echo 'Bonjour record:'
  /usr/bin/dns-sd -L "$QUEUE @ $(/usr/sbin/scutil --get ComputerName)" _ipp._tcp local &
  pid=$!
  sleep 4
  kill "$pid" 2>/dev/null || true
  echo "Backup: $BACKUP"
else
  echo 'urf-supported did not appear; restoring the previous PPD.' >&2
  /usr/sbin/lpadmin -p "$QUEUE" -P "$BACKUP/$QUEUE.ppd"
  /usr/sbin/lpadmin -p "$QUEUE" -o FFColorMode=Black -o printer-is-shared=true -E
  echo 'This CUPS build ignores *cupsUrfSupported; fall back to an IPP shim.' >&2
  exit 1
fi
