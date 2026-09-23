# School Printer

The iMac's CUPS `bw` queue uses the Fujifilm driver and sends jobs to the
campus print cluster at `lpd://10.2.20.170/sjbw`. The intended student workflow
is native printing to `ipp://imac.local:631/printers/bw` from each device's
Print dialog, then logging in at any campus printer to select and release the
job. The legacy iOS app and upload relay remain installed until device and
school network tests pass.

## Native IPP rollout

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

The student printer URL is `ipp://imac.local:631/printers/bw` (or use the
iMac's reserved school IP if `.local` discovery is unavailable). CUPS advertises
the queue with Bonjour on the local subnet. Confirm AirPrint discovery on an
iPhone/iPad before adding any extra Bonjour service for this same queue.
School IT must confirm client reachability on TCP 631 and mDNS discovery on
UDP 5353, including any VLAN boundaries. Use the school's approved student
subnets for the CUPS/firewall access rule.

### School account identity gate

Campus release uses **school accounts, not Mac local accounts**. The Fujifilm
driver has separate User ID, Passcode, and Account ID settings. CUPS currently
accepts IPP print jobs from the local subnet without school authentication.
In a held test, the remote client supplied the job username and CUPS recorded
that name. In a separate local filter probe, the Fujifilm driver wrote the
supplied job username into its print data but left Account ID empty. A device's
OS username must therefore not be assumed
to identify its school account. The two initial PDF jobs were submitted as
`kenny_lhs`; their appearance under a school account is unverified.

School IT needs to confirm how its print system associates a native IPP job
with a student's account, including whether a school User ID, Account ID, or
passcode must be embedded by the driver. Test with two distinct school accounts
and confirm that each sees only their own job at a campus printer. Do not
create Mac local accounts as a substitute for this mapping.

Do not retire the iOS app or relay until a PDF and a Word document from each
target platform appear under the
correct student's campus account, can be released at a chosen printer, and
copies, duplex, A4, and monochrome output have been checked on paper. CUPS
accepting a job only proves delivery to the campus cluster; it does not prove
that a student can see or release it. The Mac's print queue tools handle staff
job management before release.

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
