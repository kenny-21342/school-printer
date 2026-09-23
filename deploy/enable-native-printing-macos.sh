#!/bin/bash
# Run on the iMac after copying the Fujifilm driver and original bw PPD from
# the Mac that already prints successfully. This script asks for sudo because
# CUPS refuses to run print filters owned by an ordinary user.
set -euo pipefail

QUEUE=bw
DEVICE_URI=lpd://10.2.20.170/sjbw
STAGING_DIR=/Users/Shared/school-printer-drivers
DRIVER_SOURCE="$STAGING_DIR/FUJIFILM"
PPD_SOURCE="$STAGING_DIR/bw-original.ppd"
DRIVER_TARGET=/Library/Printers/FUJIFILM
BACKUP="$HOME/school-printer-backups/native-ipp-$(date +%Y%m%d-%H%M%S)"
RELAY="$HOME/school-printer"

if [[ $(uname -s) != Darwin ]]; then
  echo 'Run this on the iMac.' >&2
  exit 1
fi
if [[ ! -x "$DRIVER_SOURCE/Filter/FFACMMCFilter" || ! -f "$PPD_SOURCE" ]]; then
  echo "Missing staged Fujifilm driver or PPD in $STAGING_DIR." >&2
  exit 1
fi
if ! /usr/bin/grep -q '^\*cupsFilter: "application/pdf 0 /Library/Printers/FUJIFILM/Filter/FFACMMCFilter"' "$PPD_SOURCE"; then
  echo 'The staged PPD does not point to the original Fujifilm filter.' >&2
  exit 1
fi

# Capture the live CUPS state and the old relay before touching either. Keep
# this outside the deployment directory so redeploys cannot overwrite it.
umask 077
mkdir -p "$BACKUP/cups" "$BACKUP/relay"
sudo -v
sudo cp -p /etc/cups/cupsd.conf /etc/cups/cups-files.conf "$BACKUP/cups/"
if [[ -f /etc/cups/printers.conf ]]; then
  sudo cp -p /etc/cups/printers.conf "$BACKUP/cups/"
fi
if [[ -f /etc/cups/ppd/$QUEUE.ppd ]]; then
  sudo cp -p "/etc/cups/ppd/$QUEUE.ppd" "$BACKUP/cups/"
fi
if [[ -d "$RELAY/data" ]]; then
  cp -Rp "$RELAY/data" "$BACKUP/relay/"
  if [[ -f "$RELAY/data/printers.db" && -x "$RELAY/venv/bin/python" ]]; then
    "$RELAY/venv/bin/python" - "$RELAY/data/printers.db" "$BACKUP/relay/printers-consistent.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
destination = sqlite3.connect(sys.argv[2])
source.backup(destination)
destination.close()
source.close()
PY
  fi
fi
if [[ -f "$RELAY/.env" ]]; then
  cp -p "$RELAY/.env" "$BACKUP/relay/"
fi
if [[ -f "$HOME/Library/LaunchAgents/com.school.printer.plist" ]]; then
  cp -p "$HOME/Library/LaunchAgents/com.school.printer.plist" "$BACKUP/relay/"
fi

# Install into the system driver directory. CUPS checks root ownership of the
# filter and its supporting plug-ins before it will accept a job.
sudo /usr/bin/ditto "$DRIVER_SOURCE" "$DRIVER_TARGET"
sudo /usr/sbin/chown -R root:wheel "$DRIVER_TARGET"
/usr/bin/cupstestppd "$PPD_SOURCE"

# The device's /ipp/print endpoint returns client-error-not-found. Retain the
# working LPD URI and Fujifilm PPD; CUPS exposes this one queue via IPP.
/usr/sbin/lpadmin -p "$QUEUE" -v "$DEVICE_URI" -P "$PPD_SOURCE" -E
/usr/sbin/lpadmin -p "$QUEUE" -o FFColorMode=Black -o printer-is-shared=true
# Advertise the same CUPS queue in the Bonjour subtype used by AirPrint.
# CUPS builds the service's document-format TXT data from the queue itself.
/usr/sbin/cupsctl --share-printers BrowseDNSSDSubTypes=_cups,_universal

/usr/bin/lpstat -t
/usr/bin/lpoptions -p "$QUEUE" -l | /usr/bin/grep -E '^(Duplex|PageSize|FFColorMode)/'
/usr/bin/ipptool -q "ipp://localhost:631/printers/$QUEUE" /usr/share/cups/ipptool/get-printer-attributes.test
echo "Backup: $BACKUP"
echo "Shared printer: ipp://$(/usr/sbin/scutil --get LocalHostName).local:631/printers/$QUEUE"
echo 'School account mapping remains to be verified; CUPS does not authenticate native IPP jobs against school accounts.'
