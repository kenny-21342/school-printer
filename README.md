# School Printer Relay

iPhone/iPad → Raspberry Pi → FUJIFILM Xerox "bw" (`lpd://10.2.20.170/sjbw`)

## Architecture

```
┌─────────────┐    HTTPS/multipart    ┌──────────────────────┐    LPD     ┌────────────────┐
│  iOS app    │ ────────────────────> │ Pi 4: FastAPI + JWT  │ ────────> │ FUJIFILM Xerox │
│ SchoolPrinter│   (Bearer token)     │   + SQLite + CUPS bw  │  lpd://   │  (10.2.20.170) │
└─────────────┘                       └──────────────────────┘           └────────────────┘
```

- iOS picks a file (PDF, Office, image, text) → uploads to Pi
- Pi authenticates the user (per-user JWT, SQLite), spools file, calls `lp -d bw`
- CUPS sends raw PDF over LPD to the printer

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

- All auth is JWT (HS256, 7-day expiry). Tokens stored in `UserDefaults`
  (consider Keychain for production distribution).
- Passwords hashed with argon2.
- UFW only allows 22, 3389, Samba, 8000 from LAN.
- systemd service runs with `ProtectSystem=full`, `MemoryMax=1G`, no new privs.
- File uploads limited to 50 MB, MIME-type allowlist.
- Rate limit: 10 logins/min, 30 prints/min per IP.

## Known limitations

- The Pi can't reach the printer from home (different subnet). Printer will be
  testable once the Pi is at school.
- Plain HTTP only. Add TLS before relying on this for real student data.
