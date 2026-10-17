# Arch Linux on the iMac (Hyprland)

The relay and per-student AirPrint proxy run as **system services**. They start
at boot, including before Hyprland starts, and keep running after logout.
Office uploads use headless LibreOffice; no Microsoft Office, AppleScript,
automatic desktop login, X11, or Wayland session is required. The iOS app and
AirPrint profiles use the same API and ports as before.

The macOS deploy scripts remain available. The original source was preserved
before this port in a `backup/macos-*` Git branch and in an archive and Git
bundle under `../school-printer-backups/`. A source snapshot does **not** contain
the deployed student database or credentials.

## Before erasing macOS

Run the following **on the deployed iMac**, from this checkout:

```bash
python3 deploy/backup-setup.py --install-dir "$HOME/school-printer"
```

This makes a private tar archive outside the installation with a consistent
SQLite snapshot (including committed WAL data), `.env`, `data/secret_key`,
stored signed Shortcuts, code, spool files, shim settings, queue information,
and any readable CUPS configuration and Fujifilm driver files. Check
`MANIFEST.json` for warnings. If CUPS files cannot be read, repeat using sudo
with **explicit paths**, since sudo can change `$HOME`:

```bash
sudo python3 deploy/backup-setup.py \
  --install-dir /Users/YOUR_USER/school-printer \
  --output-dir /Users/YOUR_USER/school-printer-backups
```

Copy the archive and source backup to another computer or external drive.
Keep them private: they include passwords, student data and printing secrets.
For the final migration, let existing jobs finish and stop both LaunchAgents
before making a final backup to freeze spool files and job state. The backup
command itself never stops the running service. Custom `SP_DATA_DIR` or
`SP_DB_PATH` paths require `--data-dir` or `--db-path`.

Keep the **database and signing secret together** to preserve existing device
tokens. If `SP_SECRET_KEY` was set explicitly, preserve that `.env` value too.
macOS driver files and `/etc/cups` are saved for rollback/reference; Linux needs
its own CUPS configuration and driver binaries.

## Install Arch dependencies

After Arch and networking work on the iMac:

```bash
sudo pacman -Syu --needed python python-pip cups cups-filters avahi \
  libreoffice-fresh rsync curl ripgrep ttf-dejavu noto-fonts noto-fonts-cjk
sudo systemctl enable --now cups.service avahi-daemon.service
# Optional: keep the old Bonjour name. Skip if another imac.local exists.
sudo hostnamectl set-hostname imac
```

Use a DHCP reservation for the iMac. Profiles and stored Shortcuts contain its
address; keep the old school address if possible. Changing the address requires
students to reinstall profiles and regenerate Shortcuts. Install the document
fonts you need; LibreOffice layouts can differ from Microsoft Office. Check a
representative Word, Excel and PowerPoint document before rollout.

Arch provides [`cups`](https://archlinux.org/packages/extra/x86_64/cups/), including
`ippeveprinter`, and [`libreoffice-fresh`](https://archlinux.org/packages/extra/x86_64/libreoffice-fresh/).
Conversion uses LibreOffice's documented
[`--headless`, `--convert-to` and isolated user profile options](https://help.libreoffice.org/latest/en-GB/text/shared/guide/start_parameters.html).

## Set up the campus queue

The known macOS route was `lpd://10.2.20.170/sjbw` and queue `bw`. Verify that
address at school. The old printer's `/ipp/print` endpoint returned not-found;
do not assume `-m everywhere` will work there.

**The exact Fujifilm Xerox model is still required to select a driver.** The
existing macOS PPD calls `/Library/Printers/FUJIFILM/Filter/FFACMMCFilter`, which
is a macOS executable. Copying it to Arch does not make it a Linux filter. Find
the model on the printer label, in the archived `system/cups/ppd/bw.ppd`, or ask
school IT for the supported Linux PPD/filter and school-account settings.

Install that Linux driver first. Configure the queue with its PPD:

```bash
sudo env DEVICE_URI=lpd://10.2.20.170/sjbw \
  PPD_SOURCE=/path/to/LINUX-driver.ppd ./deploy/configure-queue-linux.sh
```

Alternatively, use the exact Linux driver identifier from `lpinfo -m`:

```bash
lpinfo -m
sudo env DEVICE_URI=lpd://10.2.20.170/sjbw \
  MODEL='identifier-from-lpinfo' ./deploy/configure-queue-linux.sh
```

A generic PostScript driver is an option **only if school IT confirms this
printer/cluster accepts PostScript and preserves the supplied school ID**.
The queue script backs up CUPS and keeps `bw` unshared. The Linux installer also
disables CUPS printer sharing; dedicate this host to the school relay if it
currently shares other queues.

Test a one-page PDF at school (this submits an actual job):

```bash
lp -h localhost:631 -U YOUR_SCHOOL_ID -d bw -t 'Linux release test' \
  -o print-color-mode=monochrome -o media=A4 -o sides=one-sided /path/to/test.pdf
```

Log in at the campus printer with that account and confirm you can release it.
Then check duplex and copies. An accepted CUPS job alone does not prove campus
account mapping. Keep TCP 631 and the private backend port 8632 unavailable to
student devices, so they cannot bypass the token proxy.

## Carry over existing registrations (optional)

Do this **before first installation**, on Arch. Extract the trusted backup into
a private temporary directory:

```bash
umask 077
mkdir -p ~/school-printer-migration
tar -xzf /path/to/school-printer-setup-TIMESTAMP.tar.gz -C ~/school-printer-migration
sudo install -d -m 700 /opt/school-printer/data
sudo cp -a ~/school-printer-migration/relay/data/. /opt/school-printer/data/
sudo install -m 600 ~/school-printer-migration/relay/.env /opt/school-printer/.env
sudoedit /opt/school-printer/.env
```

Set `SP_OFFICE_BACKEND=libreoffice`. Remove any macOS converter binary override,
`SP_BASE_DIR`, `SP_DATA_DIR`, `SP_SPOOL_DIR`, `SP_DB_PATH`, `SHIM_STATE_DIR`, and
`SHIM_LOG` from the old `.env`; the units supply Linux paths. Keep the secret,
registry settings, queue and existing passwords. Set `SP_CUPS_SERVER=localhost:631`.
Use port 8000 for the relay and 8631 for AirPrint, which the existing profiles
expect. Review `deploy/linux/env.example` for other settings.

Old CUPS IDs belong to the old installation, and stored spool paths point into
macOS. On the **restored copy**, retire unfinished jobs and clear those stale
references before the service starts (historical rows and registrations remain):

```bash
sudo python3 - <<'PY'
import sqlite3
with sqlite3.connect('/opt/school-printer/data/printers.db') as db:
    db.execute("UPDATE jobs SET status='cancelled', error='Host migrated to Linux; resubmit this job' WHERE status NOT IN ('done', 'error', 'cancelled')")
    db.execute('UPDATE jobs SET cups_job_id=NULL, spool_path=NULL, conversion_claimed_at=NULL')
PY
```

Do not copy the macOS venv, spool directory or CUPS configuration into the Linux
installation. Existing `data/shortcuts/` files can still be served. The open
printer stays off by default; set its owner again only if you want to enable it.

## Deploy

Keep the source checkout separate from `/opt/school-printer`:

```bash
sudo ./deploy/deploy-linux.sh
```

Installs the Python environment and both systemd units, with a dedicated
`school-printer` service user. It leaves existing `.env`, database and signing
secret intact. For a fresh installation it generates an admin bootstrap
password in `/opt/school-printer/.env`. Read it privately with `sudo cat` and
blank `SP_BOOTSTRAP_ADMIN_PASS` after the first successful admin login.

Re-run the installer for updates. If Arch upgrades Python to a new minor
version, stop the services, rename `/opt/school-printer/venv` to `venv-old`, and
redeploy to rebuild dependencies with the new interpreter. Use
`sudo env INSTALL_DIR=/opt/another-path ./deploy/deploy-linux.sh` for a custom
system path; paths under `/home` or `/root` are unsupported by the units' home
directory isolation.

For student access, allow TCP **8000** and **8631** from approved school
subnets in your firewall. Allow UDP **5353** if you want Bonjour discovery.
Profile printers work without Bonjour. Leave TCP **631** and **8632** private.
The installer does not change firewall rules. Prevent system sleep while this
host serves jobs; no Hyprland startup command is needed. For a dedicated
always-on host, `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`
disables sleep system-wide; `unmask` reverses it.

## Operate and verify

```bash
school-print status
sudo school-print requests
sudo school-print approve 12
sudo school-print revoke 12
sudo school-print owner YOUR_SCHOOL_ID
sudo school-print on          # optional open printer; all its jobs use the owner
sudo school-print off
sudo school-print stop
sudo school-print start
sudo journalctl -u school-printer -u school-airprint -f
sudo tail -f /opt/school-printer/logs/airprint-shim.log
curl -s localhost:8000/health | python3 -m json.tool
sudo ss -lntp | rg ':8000|:8631|:8632|:631'
```

`office.backend` should be `libreoffice`, `office.available` true, and
`office.requires_gui` false. False `gui_session` and `licensed` are expected for
LibreOffice. Confirm `cups_reachable` true and `queue_is_raw` false. Port 8632
must listen only on loopback. Check from a student device that direct CUPS access
is refused. Then complete registration, release its verification page, install
the profile, and print/release one PDF. Reboot without logging in and repeat.
Test two school IDs before general use.

Avahi publishes the optional open printer with the
[`_universal` subtype](https://raw.githubusercontent.com/avahi/avahi/master/man/avahi-publish.1.xml.in).
The backend uses [`ippeveprinter -r off`](https://openprinting.github.io/cups/doc/man-ippeveprinter.html)
on Linux, so it is not advertised. The proxy still replaces iOS's `mobile`
username with the registry's school ID and refuses the open endpoint when off.

## iOS Shortcuts on Linux

Linux cannot run Apple's `shortcuts sign`. AirPrint profiles and printing work
without it. Migrated signed files continue to work if the server address stays
the same. New requests need signing on a Mac, or the manual iPad workflow in
[print-shortcut.md](print-shortcut.md):

```bash
# On Arch, export privately, then copy in.shortcut to the Mac:
umask 077
sudo school-print unsigned 12 http://RESERVED_IP:8000/print > in.shortcut

# On the Mac, sign and return the result:
shortcuts sign --mode anyone --input in.shortcut --output out.shortcut
scp out.shortcut YOUR_ADMIN@IMAC:out.shortcut
ssh -t YOUR_ADMIN@IMAC 'sudo install -d -m 700 -o school-printer -g school-printer /opt/school-printer/data/shortcuts && sudo install -m 600 -o school-printer -g school-printer out.shortcut /opt/school-printer/data/shortcuts/12.shortcut && rm out.shortcut'
```

Alternatively, export over SSH with `sudo -n school-print unsigned ...` if your
account has noninteractive sudo authorization. Do not use a pseudo-terminal for
the binary export. Signed Shortcuts contain a bearer printing key; keep the
files private.

## Development checks

```bash
cd server
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

The standard suite stubs printing and macOS conversion. Linux tests cover
headless conversion failures, identity forwarding, Avahi arguments and the
open-printer switch. A real LibreOffice DOCX conversion test runs when
`libreoffice` is installed. The Linux CI workflow installs it and checks shell
scripts and systemd units; it submits no job to the campus printer.

## Control it from your Mac

`tools/school-print` and `School Print.app` on your Mac detect whether the iMac
runs Linux or the old macOS setup and use the right commands, so approving
students and signing their Shortcuts keeps working after the move.

1. Pick the machine once: `tools/school-print host youruser@<reserved-ip>` (an
   address is more reliable than `imac.local` away from the dorm, since Bonjour
   does not cross subnets). `school-print host` shows the current choice.
2. Allow ssh key login for that user, and let it run the one admin script
   without a password. On the iMac, edit `deploy/linux/sudoers-school-print.example`
   (replace `YOUR_USER`), then:

   ```bash
   sudo install -m 440 deploy/linux/sudoers-school-print.example /etc/sudoers.d/school-print
   sudo visudo -c
   ```

   That line allows exactly `/usr/local/bin/school-print`; anyone who can ssh as
   that user can approve or revoke students, so keep the login key-only.
3. Test from the Mac: `tools/school-print status` should print
   `Service: RUNNING | Open printer: OFF (as …) | Pending requests: N`.

Approving a student exports their Shortcut over ssh, signs it **on the Mac**
(`shortcuts sign` needs iCloud and does not exist on Linux), and stores the signed
file on the iMac with `school-print store-shortcut`. If signing fails the profile
still works; `school-print shortcut <id>` retries. `tools/test-school-print.sh`
tests all of this against a fake ssh and never touches the iMac.

## Direct LPD printing (no vendor driver)

The Fujifilm driver exists only for macOS, so a Linux queue may not be possible.
`SP_PRINT_BACKEND=lpd` in `/opt/school-printer/.env` skips CUPS entirely: the relay
converts the PDF to PostScript (`pdftops`, or Ghostscript as a fallback; on Arch
`pacman -S poppler`) and speaks LPD with a PJL job header to `SP_LPD_HOST:515`,
queue `SP_LPD_QUEUE` (defaults `10.2.20.170`, `sjbw`). The protocol is from
[paoprint](https://github.com/PaoDevelopers/paokit) (BSD-2-Clause, Runxi Yu); this
version never sends a password, and sanitises the job name and user so a student
cannot inject LPD/PJL lines.

Limits: PDF and PostScript only; one contiguous page range; a job counts as done
once the printer accepts it and cannot be cancelled afterwards. With this backend
`configure-queue-linux.sh` is not needed, but `deploy-linux.sh` still insists on a
configured queue; relax that check before installing without one.

Inspect exactly what would be sent, without sending: 
`cd /opt/school-printer/server && ../venv/bin/python -m app.lpd_cli --dump --user s21342 file.pdf | less`.

Unverified at the campus printer: `SIMPLEX` for one-sided, empty `@JOEP`. Print one
page released under your own account before relying on it.
