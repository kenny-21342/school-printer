# iMac Office → PDF conversion service

When the idle iMac becomes available (in ~2 months), this service converts
Office files (Word/Excel/PowerPoint) to PDF using real Microsoft Office,
so the Pi can print them with full fidelity.

## Architecture

```
Pi (pending-conversion job)
  │  scp / smb put original file
  ▼
iMac:/Users/Shared/SchoolPrinter/inbox/<file>
  │  launchd runs convert-to-pdf.scpt continuously
  │  AppleScript opens in Microsoft Word/Excel/PowerPoint, exports PDF
  ▼
iMac:/Users/Shared/SchoolPrinter/outbox/<file>.pdf
  │  Pi polls (scp / smb get)
  ▼
Pi: marks job as converted, submits PDF to CUPS bw
```

## Setup on the iMac (when it arrives)

1. **Install Microsoft Office** (Word, Excel, PowerPoint). Sign in once to
   activate; the apps need to be licensed for AppleScript export to work.

2. **Enable GUI automation permissions** (one-time, per app):
   - System Settings → Privacy & Security → **Automation**
   - Allow `osascript` / `Script Editor` to control Word, Excel, PowerPoint.
   - The first time each app is driven, macOS will prompt — click **OK**.

3. **Create the folders and copy the script:**
   ```bash
   sudo mkdir -p /Users/Shared/SchoolPrinter/{inbox,outbox,done,errors}
   sudo chown -R "$USER:staff" /Users/Shared/SchoolPrinter
   cp convert-to-pdf.scpt /Users/Shared/SchoolPrinter/
   chmod +x /Users/Shared/SchoolPrinter/convert-to-pdf.scpt
   ```

4. **Install the launchd job** (auto-starts on login, restarts if it crashes):
   ```bash
   cp com.school.printer-converter.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.school.printer-converter.plist
   ```

5. **Share the inbox + outbox over SMB** so the Pi can push/pull without SSH:
   - System Settings → General → Sharing → **File Sharing** → On
   - Add `/Users/Shared/SchoolPrinter` as a shared folder
   - Give read+write to your user
   - Note the iMac's IP (`ipconfig getifaddr en0`)
   - From the Pi: `mount -t cifs //IMAC_IP/SchoolPrinter /mnt/imac -o user=...,password=...`

6. **Test manually** before wiring the Pi side:
   ```bash
   cp some-document.docx /Users/Shared/SchoolPrinter/inbox/
   # wait ~10 seconds
   ls /Users/Shared/SchoolPrinter/outbox/   # should contain some-document.pdf
   tail /Users/Shared/SchoolPrinter/converter.log
   ```

## Wiring the Pi side (when the iMac is up)

The Pi needs a small loop that:

1. Finds jobs in `status='pending-conversion'`
2. Pushes each `spool_path` to the iMac inbox (scp or smb)
3. Polls the outbox for `<basename>.pdf`
4. Pulls it back, submits to CUPS, marks the job `pending`

A skeleton of that loop is sketched in `pi_conversion_relay.py.example`
(separate file; we'll flesh it out when the iMac arrives).

## Why this approach (not LibreOffice on the Pi)

- **Fidelity**: real Microsoft Office renders documents exactly as on Windows.
  LibreOffice on the Pi is decent but mangles complex formatting, fonts,
  embedded objects, and PowerPoint animations.
- **Licensing**: the iMac already has Office licensed.
- **Isolation**: heavy conversion work happens off the Pi, leaving the Pi
  free to serve the API and spool CUPS jobs.

## Troubleshooting

- **`osascript` not authorized**: open System Settings → Privacy & Security →
  Automation, tick the Office apps. Re-run after a reboot if prompts reappear.
- **Office wants activation**: sign in to Office once interactively.
- **Files stuck in inbox**: check `/Users/Shared/SchoolPrinter/converter.err`
  and the `errors/` folder.
- **Pi can't reach iMac**: same subnet / VPN as the printer. See project README.
