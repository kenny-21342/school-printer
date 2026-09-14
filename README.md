# School Printer Relay

iPhone/iPad → iMac → FUJIFILM Xerox "bw" (`ipp://10.2.20.170/ipp/print`)

## Architecture

```
┌──────────────┐   HTTPS/multipart   ┌────────────────────────────────┐   IPP    ┌────────────────┐
│   iOS app    │ ──────────────────> │ iMac: FastAPI + JWT + SQLite   │ ───────> │ FUJIFILM Xerox │
│ SchoolPrinter│    (Bearer token)   │  + CUPS + Microsoft Office     │          │  (10.2.20.170) │
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

lpadmin -p bw -v ipp://10.2.20.170/ipp/print -m everywhere -E
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
2. Update the CUPS queue if the printer IP changed:
   ```bash
   sudo lpadmin -p bw -v ipp://NEW_PRINTER_IP/ipp/print -m everywhere -E
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
- **iOS only.** Windows, Android and Chromebook users are not served, despite
  that being the original goal. There is also no app-distribution story: an
  unsigned build expires after 7 days and needs a Mac + Xcode per device.
- Pages, Numbers and Keynote are rejected (415) with a message asking for a PDF
  export — nothing scripts them, and the export is two clicks in the app.
- The printer is only reachable from the school subnet, so end-to-end printing
  is testable only once the iMac is at school.

## Open item: is the `bw` queue raw?

If `bw` was created without a PPD/driver, CUPS passes files through untouched
and **silently ignores** page ranges, duplex, paper size, orientation and
scaling — meaning the whole print-options UI would control nothing.

```bash
curl -s http://localhost:8000/health | python3 -m json.tool   # queue_is_raw
lpoptions -p bw -l        # empty output ⇒ raw queue
```

If it is raw, re-point the queue at the printer's IPP endpoint so CUPS applies
its filters (and reports real job status instead of "handed to the printer"):

```bash
sudo lpadmin -p bw -v ipp://10.2.20.170/ipp/print -m everywhere -E
lpoptions -p bw -l        # should now list real options
```

## Tests

```bash
cd server
pip install -e '.[dev]'
pytest tests/ -q          # 43 tests; no printer and no Office required
```

`lp` and `osascript` are both stubbed, so the suite never touches the printer
and never launches Word.
