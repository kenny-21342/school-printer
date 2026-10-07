# School Print

Print from a phone or iPad's normal Share → Print menu to the school's campus
printers, and release the job at the printer with your own school login.

```
iPad ──AirPrint (IPP)──> iMac: School Print service (:8631) ──> CUPS `bw` ──LPD──> campus Follow-Me
                          │  token in the URL decides who you are
                          └─ relay (:8000): registry, /register, legacy app API
```

CUPS on the iMac (macOS 12) cannot publish the `URF` Bonjour key iOS requires, so
a small service, `deploy/airprint-shim/`, fronts it: `ippeveprinter` (ships with
CUPS) behind a Python proxy. The campus system trusts whatever user name arrives
with a job, and iOS always reports `mobile`, so **the proxy decides the name**.

## Per-student printers

1. A student opens `http://<imac>:8000/register` and enters their school ID and name.
2. **They prove the ID is theirs.** The relay prints a one-page code under that ID at
   the campus printer. Only the account's owner can log in there, release the job
   ("School Print verification code") and read the code, which they type on the page.
   The check uses the school's own login, so nothing can be impersonated. Limits: 3
   codes per request, 5 wrong tries per code, 3 requests per ID per hour (each one
   puts a held page in that account's queue).
3. Once verified they are approved automatically (`SP_AIRPRINT_AUTO_APPROVE_VERIFIED=false`
   makes you approve each one with `tools/school-print approve <id>`; `SP_AIRPRINT_VERIFY=false`
   turns the check off and falls back to approving by hand).
4. The student reopens their page and installs the profile (Settings → Profile
   Downloaded). **School Print** now appears in Share → Print, and everything they
   print is released under their school ID. No mDNS is involved: the profile adds
   the printer by address, so it also works where Bonjour discovery is blocked.
5. iPads that cannot install profiles use the Shortcut on the same page (Add Print
   Shortcut), which already contains their key. See `docs/print-shortcut.md`.

`tools/school-print revoke <id>` cuts off one device; its jobs are refused within
about 20 seconds. Students are `users` rows in the relay's database (the school ID
is the username); a token is an HMAC of the request row, so nothing secret is
stored and approving the same request twice returns the same token.

**What the check does and does not prove.** The printed code proves the requester can
log in as that school account at the moment they register. It does not authenticate
later print jobs: the device's token is a bearer secret, so a stolen token prints as
that student until you revoke it, and the campus system itself still trusts whatever
name arrives with a job. Tokens travel over plain HTTP on the school Wi-Fi. Tell
students their jobs count against their own account.

### The open printer

`tools/school-print on|off` registers an additional "School Print" printer over
Bonjour that anyone on the network can see; it prints as the `owner` account
(`tools/school-print owner <id>`). It is off unless you turn it on.
`School Print.app` (Desktop) wraps these commands.

### Service

```bash
./deploy/install-airprint-shim-macos.sh            # on the iMac; --uninstall removes it
tools/school-print status | stop | start
tail -f ~/school-printer/logs/airprint-shim.log     # one line per job: who, what
```

Per-token rate limit: `SHIM_JOBS_PER_HOUR` (default 30). The relay needs
`SP_AIRPRINT_HOST` only if the address students reached `/register` on is not the
one to put in profiles. The iMac needs a stable address (DHCP reservation): the
profile contains it.

## Native IPP rollout (the underlying queue)

> **Sharing is now off.** On 7 Oct a device sent a job to the shared `bw @ iMac`
> queue under the name `lucaslu`, which its owner says he did not print. The
> shared queue has no way to tell who is who, so it was unshared
> (`lpadmin -p bw -o printer-is-shared=false`, `cupsctl --no-share-printers`; the
> prior config is in `~/school-printer-backups/unshare-*`). The relay and shim
> still submit over `localhost:631`. Do not re-run
> `deploy/enable-native-printing-macos.sh` without wanting that route back; it
> turns sharing on again. The notes below describe how the queue was built.

The printer's advertised `/ipp/print` endpoint returns
`client-error-not-found`. Keep the Fujifilm PPD and LPD URI; CUPS exposes that
same queue to clients over IPP. Do not create a second destination.

On the iMac, stage the Fujifilm driver folder and original `bw.ppd` from a Mac
that already prints successfully in `/Users/Shared/school-printer-drivers/`.
Run `deploy/enable-native-printing-macos.sh` **on the iMac**. It takes a backup
of CUPS and relay state, installs the filter as root, creates/shares `bw`, sets
monochrome as the default, and checks the queue's IPP attributes. It asks for
the iMac administrator password locally because CUPS rejects a filter owned by
an ordinary user. Keep the driver files and PPD out of Git.

The printer URL is `ipp://imac.local:631/printers/bw` (or use the iMac's
reserved school IP if `.local` discovery is unavailable). CUPS advertises the
same queue with Bonjour's `_universal` AirPrint subtype on the local subnet;
it does not create a second destination. An iPhone/iPad Print-dialog test is
still needed to confirm discovery and printing.
School IT must confirm client reachability on TCP 631 and mDNS discovery on
UDP 5353, including any VLAN boundaries. Use the school's approved student
subnets for the CUPS/firewall access rule.

### School account identity

Campus release uses **school accounts, not Mac local accounts**. The Fujifilm
driver has separate User ID, Passcode, and Account ID settings. CUPS currently
accepts IPP print jobs from the local subnet without school authentication.
In a held test, the remote client supplied the job username and CUPS recorded
that name. In a separate local filter probe, the Fujifilm driver wrote the
supplied job username into its print data but left Account ID empty. A device's
OS username must therefore not be assumed to identify its school account. The
two initial PDF jobs were submitted as
`kenny_lhs`; their appearance under a school account is unverified.

For the personal pilot, check whether one of those jobs appears after logging
in at a campus printer with your school account. If it does, release it and
check the printed page. If it does not, ask school IT how their print system
associates the job with your account, including whether the Fujifilm driver
must supply a school User ID, Account ID, or passcode. The library iMacs may
work because users log into macOS with school network accounts; this iMac is
currently configured with only local directory search.

For the iPhone/iPad test, join the school student Wi-Fi, open a PDF, choose
**Share → Print**, and select **bw @ iMac**. Submit one page, then log in at a
campus printer with your school account and check whether that job appears.
Report separately whether the queue was discovered, CUPS received the job,
and your school account could release it; each checks a different part of the
route.

For broader student rollout, school IT should confirm the account mapping and
test two distinct school accounts, verifying that each sees only their own
jobs. Keep the iOS app and relay until PDF and Word printing from each target
platform, release at a campus printer, copies, duplex, A4, and monochrome
output all pass. CUPS accepting a job only proves delivery to the campus
cluster; it does not prove that a user can see or release it.

To roll back a failed sharing change on the iMac, run
`lpadmin -p bw -o printer-is-shared=false` and `cupsctl --no-share-printers`.
The script prints its backup directory, which contains the previous CUPS
configuration and relay state.

## Legacy upload relay

### Architecture

```
┌──────────────┐   HTTP/multipart    ┌────────────────────────────────┐   LPD    ┌────────────────┐
│   iOS app    │ ──────────────────> │ iMac: FastAPI + JWT + SQLite   │ ───────> │ campus print   │
│ SchoolPrinter│    (Bearer token)   │  + CUPS + Microsoft Office     │          │ cluster + login│
└──────────────┘                     └────────────────────────────────┘          └────────────────┘
```

One machine does everything:

- iOS picks a file (PDF, JPEG, PNG, text, Word, Excel, PowerPoint) → uploads
- the iMac authenticates the user (per-user JWT, SQLite) and spools the file
- Office files are converted to PDF locally, by driving real Word/Excel/
  PowerPoint through AppleScript (`server/app/office.py`)
- `lp -d bw` hands the PDF to CUPS, which sends it to the printer over IPP

This used to be split across a Raspberry Pi and the iMac, with the Pi shipping
Office files over SMB to a folder-watching converter and polling for the PDF to
come back. **The Pi is retired.** Collapsing both halves onto the iMac turned
that whole relay into one subprocess call — which is what finally made Office
printing work at all.

## Project layout

```
school-printer/
├── server/                 # FastAPI print relay
│   ├── app/
│   │   ├── main.py         # endpoints + the conversion worker
│   │   ├── office.py       # Office → PDF via AppleScript
│   │   ├── filetypes.py    # what prints directly, what needs converting
│   │   ├── scripts/convert-to-pdf.applescript
│   │   ├── auth.py         # argon2 hashing + JWT
│   │   ├── cups.py         # lp wrapper
│   │   ├── db.py           # SQLite schema
│   │   ├── deps.py         # auth deps
│   │   ├── schemas.py      # pydantic models
│   │   └── config.py       # settings (.env-driven)
│   └── pyproject.toml
├── deploy/
│   ├── deploy-macos.sh          # idempotent install/update (rsync + venv + LaunchAgent)
│   ├── com.school.printer.plist # the LaunchAgent
│   └── env.example
└── ios/
    ├── SchoolPrinter/      # Swift sources
    ├── project.yml         # xcodegen spec
    └── SchoolPrinter.xcodeproj
```

## Setting up the iMac

```bash
# Python 3.11+; uv needs no sudo and no Xcode tools, which matters on an
# older macOS where Homebrew has no bottles:
curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13

# Create and share bw with deploy/enable-native-printing-macos.sh instead.
PYTHON="$(uv python find 3.13)" ./deploy/deploy-macos.sh     # run ON the iMac
```

Installs to `~/school-printer` by default — no sudo anywhere, because the
service binds an unprivileged port and keeps its state in one directory. Set
`INSTALL_DIR` to put it elsewhere.

Then four things that are not optional, most of them consequences of one fact — the
service runs as a **LaunchAgent**, because AppleScript can only drive Word,
Excel and PowerPoint inside a logged-in GUI session:

1. **Automatic login** on (System Settings → Users & Groups), or the print queue
   is down after every restart.
2. **No sleep**: Displays → Advanced → "Prevent automatic sleeping on power
   adapter when the display is off".
3. **Office signed in, once per app.** Open Word, Excel and PowerPoint at the
   machine and complete the first-run/activation wizard. This is not optional
   and it fails in the worst way: an unactivated Word does not error, it blocks
   in Office's setup UI (`SetupUI_ShowUIFlowWithEntryPoint` in a `sample` of the
   process), so every job sits in "Converting…" for the full timeout and then
   reports that it took too long. `/health` reports `office.licensed`.
4. **Automation permission**, once per Office app: the first Word job pops a
   "school-printer wants to control Microsoft Word" dialog and fails until
   someone clicks OK. Print one of each while sitting at the machine, or
   pre-approve under Privacy & Security → Automation.

A LaunchDaemon would start without login but has no GUI session, so PDFs would
print and every Office file would fail — the most confusing possible split.
`GET /health` reports `office.gui_session` precisely so that state is visible.

## API

| Method | Path                         | Auth   | Description                       |
|--------|------------------------------|--------|-----------------------------------|
| POST   | `/auth/login`                | -      | Get JWT                           |
| GET    | `/users/me`                  | user   | Current user                      |
| GET    | `/users`                     | admin  | List users                        |
| POST   | `/users`                     | admin  | Create user                       |
| PATCH  | `/users/{id}`                | admin  | Update user (password, etc)       |
| DELETE | `/users/{id}`                | admin  | Delete user                       |
| POST   | `/print`                     | user   | Upload + print (multipart)        |
| GET    | `/jobs`                      | user   | List jobs (`?include_all=true` admin) |
| GET    | `/jobs/{id}`                 | user   | Job detail                        |
| POST   | `/jobs/{id}/refresh`         | user   | Sync status from CUPS             |
| DELETE | `/jobs/{id}`                 | user   | Cancel job                        |
| GET    | `/health`                    | -      | Health check (queue + Office)     |
| GET    | `/docs`                      | -      | Swagger UI                        |

### Job statuses

`converting` → `pending` → `processing` → `done`, or `error` / `cancelled`.

Only Office uploads start at `converting`: `/print` returns as soon as the file
is on disk, and a background worker converts it and hands the PDF to CUPS.
Conversion takes seconds to a minute, far too long to hold an upload request
open on school Wi-Fi. A job in `converting` has no `cups_job_id` yet, and
cancelling one stops it before it ever reaches the printer.

Conversions are serialised across all four uvicorn workers with a file lock —
AppleScript drives a single shared instance of Word, and two at once lose a
document. A worker that dies mid-conversion releases its claim after
`SP_CONVERSION_CLAIM_STALE_S` and the job is retried.

## Operations

```bash
# redeploy after code changes (on the iMac)
./deploy/deploy-macos.sh

# status / logs
launchctl print gui/$(id -u)/com.school.printer
tail -f /usr/local/school-printer/logs/stderr.log

# health, including whether Office printing will work
curl -s localhost:8000/health | python3 -m json.tool

# CUPS web UI
open http://localhost:631/

# create a student user
curl -X POST http://localhost:8000/users \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret123","display_name":"Alice"}'
```

## iOS

Open `ios/SchoolPrinter.xcodeproj` in Xcode (16+). Run on a simulator or device.
Default server is `http://imac.local:8000` — a Bonjour name, so it keeps working
when the iMac's IP changes between networks. Users can override it in Settings
(use the IP if `.local` does not resolve on the school Wi-Fi).

> Note: iOS 14+ blocks plain HTTP by default. `Info.plist` already allows local
> network access; for HTTPS at school, put the iMac behind a reverse proxy
> (nginx + Let's Encrypt or a self-signed cert installed via MDM/profile).

## When you move the iMac to school

1. Put it on the school network (Wi-Fi or Ethernet).
2. Update the CUPS queue if the printer IP changed, retaining its Fujifilm PPD:
   ```bash
   lpadmin -p bw -v lpd://NEW_PRINTER_IP/sjbw
   ```
3. Confirm `imac.local` resolves from an iPad; if not, set the server URL in the
   app's Settings to the iMac's school IP (and ask for a DHCP reservation).
4. Check `/health`: `cups_reachable`, `queue_is_raw: false`, and
   `office.available` (which is false unless Office is both installed *and*
   activated).

## Security notes

- All auth is JWT (HS256, 7-day expiry). Tokens are stored in the **Keychain**.
  Changing a password bumps `token_version`, which revokes every existing token.
- Passwords hashed with argon2.
- Login throttling is **per username** (10 failures → 5-minute lockout), not per
  IP: behind school NAT the whole school shares one address.
- The macOS firewall prompt on first bind to port 8000 must be allowed.
- File uploads limited to 50 MB, MIME-type allowlist.
- Spool files are deleted once a job finishes, and swept hourly
  (`SP_SPOOL_RETENTION_HOURS`). A converted job keeps only the PDF.
- Page previews are rendered on the device with PDFKit and never leave it. The
  server has no preview endpoint to attack: `/pdf-info` and `/preview` were
  removed once the client stopped calling them.
- Office conversion opens uploaded documents in real Word/Excel/PowerPoint. Macro
  execution is off by default in Office, and files arrive from authenticated
  users only, but this is the one place where student input reaches a GUI app —
  worth keeping in mind before opening the service to a wider network.

## Known limitations

- Plain HTTP only. Add TLS before relying on this for real student data.
- The legacy upload app is iOS only. Native IPP is intended to cover Windows,
  Android and ChromeOS once device and network acceptance tests pass.
- Pages, Numbers and Keynote are rejected (415) with a message asking for a PDF
  export — nothing scripts them, and the export is two clicks in the app.
- School network rules and physical output still need testing with student
  devices before the relay can be retired.

## Check the `bw` driver

The known working queue uses `FF Print Driver for Mac OS X v2.3`, not a raw
queue. CUPS reports A4, duplex, copies, and color/monochrome capabilities when
that PPD and filter are installed correctly.

```bash
curl -s http://localhost:8000/health | python3 -m json.tool   # queue_is_raw
lpoptions -p bw -l        # empty output ⇒ raw queue
```

If `lpoptions -p bw -l` is empty or `lpstat -p bw` reports an insecure filter,
install the Fujifilm driver before enabling sharing. The printer's
`/ipp/print` endpoint does not work on this device.

## Tests

```bash
cd server
pip install -e '.[dev]'
pytest tests/ -q          # 43 tests; no printer and no Office required
```

`lp` and `osascript` are both stubbed, so the suite never touches the printer
and never launches Word.
