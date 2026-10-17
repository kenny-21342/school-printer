#!/usr/bin/env bash
# Create the private campus queue using a Linux PPD/driver, never a Mac filter.
# sudo env DEVICE_URI=lpd://<host>/sjbw PPD_SOURCE=/path/to/linux.ppd ./deploy/configure-queue-linux.sh
# Alternatively MODEL=<exact identifier from lpinfo -m> instead of PPD_SOURCE.
set -euo pipefail
umask 077
[[ $(uname -s) == Linux && $EUID == 0 ]] || { echo 'Run on Linux with sudo.' >&2; exit 1; }
QUEUE="${QUEUE:-bw}"
[[ $QUEUE =~ ^[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid queue name.' >&2; exit 1; }
: "${DEVICE_URI:?Set DEVICE_URI to the school-approved campus LPD/IPP URI}"
[[ $DEVICE_URI == lpd://* || $DEVICE_URI == ipp://* || $DEVICE_URI == ipps://* || $DEVICE_URI == socket://* ]] || {
  echo 'Expected an lpd://, ipp://, ipps:// or socket:// device URI.' >&2; exit 1;
}
if [[ -n ${PPD_SOURCE:-} && -z ${MODEL:-} ]]; then
  [[ -f $PPD_SOURCE ]] || { echo 'PPD_SOURCE does not exist.' >&2; exit 1; }
  if grep -Eq '/Library/Printers|/Applications|Mac OS X|FFACMMCFilter' "$PPD_SOURCE"; then
    echo 'This appears to be the macOS driver. Obtain a Linux PPD and its Linux filter first.' >&2; exit 1;
  fi
  cupstestppd "$PPD_SOURCE"
  driver=(-P "$PPD_SOURCE")
elif [[ -n ${MODEL:-} && -z ${PPD_SOURCE:-} && $MODEL != raw ]]; then
  driver=(-m "$MODEL")
else
  echo 'Set exactly one of PPD_SOURCE (Linux PPD) or MODEL (lpinfo -m identifier); raw is not supported.' >&2; exit 1
fi
BACKUP="/var/backups/school-printer/cups-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
cp -a /etc/cups "$BACKUP/"
lpadmin -p "$QUEUE" -v "$DEVICE_URI" "${driver[@]}" -E
lpadmin -p "$QUEUE" -o printer-is-shared=false -o print-color-mode-default=monochrome
cupsctl --no-share-printers
lpoptions -p "$QUEUE" -l
echo "CUPS backup: $BACKUP"
echo "Private queue '$QUEUE' configured. Verify school account release with a one-page PDF before rollout."
