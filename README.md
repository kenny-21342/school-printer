# School Printer Relay

iPhone/iPad → Raspberry Pi → FUJIFILM Xerox "bw" (`lpd://10.2.20.170/sjbw`)

## Architecture

```
┌─────────────┐    HTTPS/multipart    ┌──────────────────────┐    LPD     ┌────────────────┐
│  iOS app    │ ────────────────────> │ Pi 4: FastAPI + JWT  │ ────────> │ FUJIFILM Xerox │
│ SchoolPrinter│   (Bearer token)     │   + SQLite + CUPS bw  │  lpd://   │  (10.2.20.170) │
└─────────────┘                       └──────────────────────┘           └────────────────┘
```

- iOS picks a file (PDF, JPEG, PNG, text) → uploads to Pi
- Pi authenticates the user (per-user JWT, SQLite), spools file, calls `lp -d bw`
- CUPS sends the PDF over LPD to the printer

> **Office files are not supported yet.** Word/Excel/PowerPoint uploads are
> rejected with a clear message asking the user to export to PDF. The iMac
> conversion service in `imac/` is a design sketch that was never wired up —
> previously these files were accepted, queued forever, and never printed.

## Project layout

```
school-printer/
├── server/                 # FastAPI print relay
│   ├── app/
│   │   ├── main.py         # all endpoints
│   │   ├── auth.py         # argon2 hashing + JWT
│   │   ├── cups.py         # lp wrapper
│   │   ├── db.py           # SQLite schema
│   │   ├── deps.py         # auth deps
│   │   ├── schemas.py      # pydantic models
│   │   └── config.py       # settings (.env-driven)
│   └── pyproject.toml
├── deploy/
│   ├── deploy.sh           # idempotent Pi deploy (rsync + venv + systemd)
│   ├── school-printer.service
│   └── env.example
└── ios/
    ├── SchoolPrinter/      # Swift sources
    ├── project.yml         # xcodegen spec
    └── SchoolPrinter.xcodeproj
```

## Deployed (Pi at 192.168.1.106)

| Component  | Status                          |
|------------|---------------------------------|
| systemd    | `school-printer.service` (active, enabled) |
| Listening  | `0.0.0.0:8000`                  |
| UFW        | 8000/tcp allowed                |
| CUPS queue | `bw` → `lpd://10.2.20.170/sjbw` (raw, mono) |
| Disk       | 3.4 GB free (was 215 MB)        |
| Admin user | `admin` (password: change via API) |

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
| GET    | `/health`                    | -      | Health check                      |
| GET    | `/docs`                      | -      | Swagger UI                        |

## Operations

```bash
# redeploy after code changes
./deploy/deploy.sh

# status / logs
ssh kenny_lhs@192.168.1.106 'sudo systemctl status school-printer'
ssh kenny_lhs@192.168.1.106 'sudo journalctl -u school-printer -f'

# CUPS web UI
open http://192.168.1.106:631/

# create a student user
curl -X POST http://192.168.1.106:8000/users \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"secret123","display_name":"Alice"}'
```

## iOS

Open `ios/SchoolPrinter.xcodeproj` in Xcode (16+). Run on a simulator or device.
Default server is `http://192.168.1.106:8000`; users can override in Settings.

> Note: iOS 14+ blocks plain HTTP by default. `Info.plist` already allows local
> network access; for HTTPS at school, put the Pi behind a reverse proxy (nginx
> + Let's Encrypt or a self-signed cert installed via MDM/profile).

## When you deploy the Pi at school

1. Plug Pi into school network (Wi-Fi or Ethernet).
2. Find its new IP: `ip addr` on the Pi, or check the router/DHCP.
3. Update CUPS queue if the printer IP changed:
   ```bash
   sudo lpadmin -p bw -v lpd://NEW_PRINTER_IP/sjbw
   ```
4. Update the iOS app's server URL in Settings.
5. The service auto-starts on boot; nothing else to do.

## Security notes

- All auth is JWT (HS256, 7-day expiry). Tokens are stored in the **Keychain**.
  Changing a password bumps `token_version`, which revokes every existing token.
- Passwords hashed with argon2.
- Login throttling is **per username** (10 failures → 5-minute lockout), not per
  IP: behind school NAT the whole school shares one address.
- UFW only allows 22, 3389, Samba, 8000 from LAN.
- systemd service runs with `ProtectSystem=full`, `MemoryMax=1G`, no new privs.
- File uploads limited to 50 MB, MIME-type allowlist.
- Spool files are deleted once a job finishes, and swept hourly
  (`SP_SPOOL_RETENTION_HOURS`).
- `deploy.sh` takes the sudo password from the `SUDO_PW` environment variable —
  never hardcode it. If you previously used the committed default, rotate it.

## Known limitations

- The Pi can't reach the printer from home (different subnet). Printer will be
  testable once the Pi is at school.
- Plain HTTP only. Add TLS before relying on this for real student data.
- **iOS only.** Windows, Android and Chromebook users are not served, despite
  that being the original goal. There is also no app-distribution story: an
  unsigned build expires after 7 days and needs a Mac + Xcode per device.
- Office formats need an export to PDF first (see above).

## Open item: is the `bw` queue raw?

If `bw` was created without a PPD/driver, CUPS passes files through untouched
and **silently ignores** page ranges, duplex, paper size, orientation and
scaling — meaning the whole print-options UI would control nothing. This has
not been verified because the Pi has been unreachable.

Check it with:

```bash
curl -s http://<PI>:8000/health | python3 -m json.tool   # queue_is_raw
# or on the Pi directly:
lpstat -l -p bw
lpoptions -p bw -l        # empty output ⇒ raw queue
```

If it is raw, re-point the queue at the printer's IPP endpoint so CUPS applies
its filters (and reports real job status instead of "handed to the printer"):

```bash
sudo lpadmin -p bw -v ipp://10.2.20.170/ipp/print -m everywhere -E
lpoptions -p bw -l        # should now list real options
```

## Running it on a Mac instead of the Pi

The Pi was one of two machines: it ran the API and CUPS, and shipped Office
files over the network to an iMac that had Word installed, then polled for the
PDF to come back (`imac/README.md`). Running the API on the Mac itself deletes
that relay entirely -- conversion becomes a local subprocess call -- and frees
the Pi for something else.

Nothing in the server had to change to make this work, because macOS ships CUPS
as part of the OS (`/usr/bin/lp`, `lpstat`, `lpoptions`, `cancel`) and the
server is plain FastAPI, SQLite and subprocess calls. `pdftoppm` and `pdfinfo`
come from `brew install poppler`.

```bash
brew install poppler
lpadmin -p bw -v ipp://10.2.20.170/ipp/print -m everywhere -E
./deploy/deploy-macos.sh
```

Two requirements the Pi did not have, both consequences of the same fact -- the
service runs as a **LaunchAgent**, because AppleScript can only drive Word,
Excel and PowerPoint inside a logged-in GUI session:

- **Automatic login** must be on, or the queue is down after every restart.
- **The Mac must not sleep**: Displays -> Advanced -> "Prevent automatic
  sleeping on power adapter when the display is off".

A LaunchDaemon would start without login but has no GUI session, so PDFs would
print and every Office file would fail -- the most confusing possible split.

## Tests

```bash
cd server
pip install -e '.[dev]'
pytest tests/ -q          # 38 tests, no printer required (lp is stubbed)
```
