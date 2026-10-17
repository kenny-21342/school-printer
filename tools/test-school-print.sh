#!/bin/bash
# Tests tools/school-print against a fake ssh and stubbed remote commands, so
# nothing touches the real iMac:  ./tools/test-school-print.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/cfg"
export FAKE_LOG="$T/log" T PATH="$T/bin:$PATH" XDG_CONFIG_HOME="$T/cfg" SIGN_RETRY_DELAY=0
export SCHOOL_PRINT_REMOTE="$T/remote" SCHOOL_PRINT_HOST=fake@imac FAKE_OS=Linux
: > "$FAKE_LOG"

cat > "$T/bin/ssh" <<'X'
#!/bin/bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1; shift
echo "ssh $host $*" >> "$FAKE_LOG"
[[ -n ${FAKE_UNREACHABLE:-} ]] && exit 255
if [[ $FAKE_OS == Darwin && $* != "uname -s" ]]; then exit 0; fi   # never run the macOS commands locally
exec bash -c "$*"
X
cat > "$T/bin/uname" <<'X'
#!/bin/bash
echo "$FAKE_OS"
X
cat > "$T/remote" <<'X'
#!/bin/bash
echo "remote $*" >> "$FAKE_LOG"
case $1 in
  summary) echo "Service: RUNNING | Open printer: OFF (as s21342) | Pending requests: 2" ;;
  address) echo 10.9.8.7 ;;
  unsigned) printf 'UNSIGNED-%s-%s' "$2" "$3" ;;
  store-shortcut) cat > "$T/stored-$2" ;;
  owner) [[ -z ${2:-} ]] && echo s21342 ;;
  *) echo "ok:$*" ;;
esac
X
cat > "$T/bin/shortcuts" <<'X'
#!/bin/bash
[[ -n ${FAKE_SIGN_FAIL:-} ]] && exit 1
while [[ $# -gt 0 ]]; do case $1 in --input) i=$2;; --output) o=$2;; esac; shift; done
printf AEA1 > "$o"; cat "$i" >> "$o"
X
chmod +x "$T/bin/"* "$T/remote"

fails=0
check() { # description, condition-result
  if [[ $2 == ok ]]; then echo "  ok   $1"; else echo "  FAIL $1"; fails=$((fails + 1)); fi
}
has() { grep -qF -- "$1" "$FAKE_LOG" && echo ok || echo no; }
tool="$HERE/school-print"

echo "linux: status"
out=$("$tool" status)
check "prints the machine's summary line" "$([[ $out == *"Pending requests: 2"* ]] && echo ok)"
check "calls the installed tool, not a shell snippet" "$(has 'remote summary')"

echo "linux: approve signs and stores a Shortcut"
: > "$FAKE_LOG"; out=$("$tool" approve 5); rc=$?
check "approves" "$(has 'remote approve 5')"
check "asks for the address and exports with the right URL" "$(has 'remote unsigned 5 http://10.9.8.7:8000/print')"
check "stores a signed (AEA1) file" "$([[ $(head -c 4 "$T/stored-5") == AEA1 ]] && echo ok)"
check "the stored file is the exported one" "$(grep -q UNSIGNED-5 "$T/stored-5" && echo ok)"
check "reports success" "$([[ $out == *"signed and stored for request 5"* && $rc == 0 ]] && echo ok)"

echo "linux: signing failure still approves, stores nothing, says how to retry"
: > "$FAKE_LOG"; rm -f "$T/stored-6"; out=$(FAKE_SIGN_FAIL=1 "$tool" approve 6); rc=$?
check "approved" "$(has 'remote approve 6')"
check "nothing stored" "$([[ ! -e $T/stored-6 ]] && echo ok)"
check "tells you to retry" "$([[ $out == *"retry: school-print shortcut 6"* && $rc != 0 ]] && echo ok)"

echo "linux: input validation"
"$tool" revoke abc >/dev/null 2>&1; check "revoke rejects a non-numeric id" "$([[ $? == 2 ]] && echo ok)"
"$tool" owner 'a b' >/dev/null 2>&1; check "owner rejects a bad school ID" "$([[ $? == 1 ]] && echo ok)"
: > "$FAKE_LOG"; "$tool" owner s21342 >/dev/null; check "owner sets a good ID" "$(has 'remote owner s21342')"
: > "$FAKE_LOG"; "$tool" revoke 3 >/dev/null; check "revoke passes the id on" "$(has 'remote revoke 3')"
: > "$FAKE_LOG"; "$tool" off >/dev/null; check "off calls the tool and reports status" "$([[ $(has 'remote off') == ok && $(has 'remote summary') == ok ]] && echo ok)"

echo "unreachable machine"
out=$(FAKE_UNREACHABLE=1 "$tool" status); rc=$?
check "says so and fails" "$([[ $out == "iMac unreachable" && $rc == 1 ]] && echo ok)"

echo "macOS machine is handled by the old code path"
: > "$FAKE_LOG"; FAKE_OS=Darwin "$tool" status >/dev/null 2>&1
check "uses launchctl, not the Linux tool" "$([[ $(has 'launchctl print') == ok && $(has 'remote ') == no ]] && echo ok)"

echo "host setting"
unset SCHOOL_PRINT_HOST
"$tool" host kenny@10.1.2.3 >/dev/null
check "is saved" "$([[ $(cat "$T/cfg/school-print/host") == kenny@10.1.2.3 ]] && echo ok)"
: > "$FAKE_LOG"; "$tool" status >/dev/null
check "and used" "$(has 'ssh kenny@10.1.2.3')"
"$tool" host 'bad host;rm' >/dev/null 2>&1; check "rejects a bad host" "$([[ $? == 2 ]] && echo ok)"

echo
[[ $fails == 0 ]] && echo "all passed" || { echo "$fails FAILED"; exit 1; }
