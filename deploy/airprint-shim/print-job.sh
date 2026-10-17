#!/bin/bash
# Called by ippeveprinter once per job: $1 is the spooled document. Hands it to
# the real bw queue, which keeps the Fujifilm filter and the LPD route. This
# shim exists only because CUPS on macOS 12 cannot publish the URF key iOS needs.
QUEUE=${SHIM_QUEUE:-${SP_CUPS_QUEUE:-bw}}
LOG="${SHIM_LOG:-$HOME/school-printer/logs/airprint-shim.log}"
user=${IPP_JOB_ORIGINATING_USER_NAME:-unknown}
# The proxy (shim.py) has already replaced the name iOS sent ("mobile") with the
# school ID it resolved, and the backend only listens on loopback, so this is
# the identity to release under. CUPS honours -U only over a TCP connection (a
# local-socket lp records the macOS account instead), hence -h. Refuse anything
# that is not a plausible school ID rather than print under the iMac's own name.
if [[ ! $user =~ ^[A-Za-z0-9._-]{2,64}$ ]]; then
  echo "refused: bad owner '$user'" >> "$LOG"
  echo "ATTR: job-state-message=No school account for this job" >&2
  exit 1
fi
owner_args=(-h "${SHIM_CUPS_SERVER:-${SP_CUPS_SERVER:-localhost:631}}" -U "$user")

# Only PDF is passed through. iOS sends PDF when the printer lists it; a raster
# URF job would need converting first, so refuse it loudly instead of printing junk.
if [[ ${CONTENT_TYPE:-application/pdf} != application/pdf ]]; then
  echo "refused: $CONTENT_TYPE is not application/pdf" >> "$LOG"
  echo "ATTR: job-state-message=Only PDF is supported" >&2
  exit 1
fi

args=("${owner_args[@]}" -d "$QUEUE" -t "${IPP_JOB_NAME:-AirPrint}" -n "${IPP_COPIES:-1}")
case ${IPP_SIDES:-} in
  one-sided|two-sided-long-edge|two-sided-short-edge) args+=(-o "sides=$IPP_SIDES") ;;
esac
case ${IPP_MEDIA:-} in
  iso_a4*) args+=(-o media=A4) ;;
  na_letter*) args+=(-o media=Letter) ;;
esac
[[ -n ${SHIM_HOLD:-} ]] && args+=(-o job-hold-until=indefinite)

out=$("${SHIM_LP_BINARY:-/usr/bin/lp}" "${args[@]}" "$1" 2>&1); rc=$?
echo "lp rc=$rc: $out" >> "$LOG"
exit $rc
