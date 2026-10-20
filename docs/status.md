# Project status (closed 2026-10-20)

A dorm iMac running Arch Linux acts as an AirPrint printer. Jobs from an iPad go to the
campus Follow-Me printer under the owner's school account and are released at the printer.
Verified end to end: one page printed from an iPad and released.

## How it works now

```
iPad ──AirPrint (IPP, :8631)──> shim (proxy + ippeveprinter) ──> app.lpd_cli ──LPD+PJL──> campus printer 10.2.20.170:515 (queue sjbw)
```

- `SP_PRINT_BACKEND=lpd`: no CUPS queue and no Fujifilm driver. PDFs are converted to
  PostScript with `pdftops` (Ghostscript fallback) and sent with a PJL job header
  ([lpd.py](../server/app/lpd.py)). Protocol from paoprint (BSD-2-Clause, Runxi Yu,
  github.com/PaoDevelopers/paokit); no password is sent.
- Install: `sudo env PRINT_BACKEND=lpd ./deploy/deploy-linux.sh` (see [linux.md](linux.md)).
- Control from the Mac: `tools/school-print on | off | owner <id> | status`.
- The open "School Print" printer prints as the `owner` account (`school-print owner`).

## Changes in this stage

- Direct LPD+PJL backend, with tests (injection-safe job names, user validation, job number
  rotation, conversion, error reporting); relay and shim select it with `SP_PRINT_BACKEND=lpd`.
- `deploy-linux.sh` accepts `PRINT_BACKEND=lpd` and no longer requires a CUPS queue.
- Mac-side `tools/school-print` controls a Linux iMac as well as the old macOS one.
- Arch/Linux port of the relay and AirPrint proxy (systemd units, unprivileged user).
- Deployment lessons: `ufw` drops inbound traffic by default, so port 8631 must be allowed
  (the printer is advertised by Bonjour even when connections are blocked, which shows up as
  "Failed to connect to printer").

## Deliberately not finished (per-student printers)

Registration, verification codes, profiles and Shortcuts exist in the code but are **switched
off on the iMac**: `school-printer.service` is disabled and port 8000 is closed. The school
separates subnets, so only the owner can reach the printer, and classmates are not supported.
Re-running `deploy-linux.sh` re-enables the relay.

## Limitations

- **Single user.** The open printer prints as one account; identity is not checked at the
  printer's door, and anyone who can reach port 8631 prints as the owner.
- **Network.** Works only from the iMac's subnet(s) (ufw allows `10.0.0.0/8` on 8631). The
  iMac's address is DHCP; profiles and Shortcuts bake it in, Bonjour discovery does not.
- **Formats.** PDF only from iOS; direct mode accepts PDF and PostScript, one contiguous page range.
- **No cancel / status.** A job counts as done once the printer accepts it; it cannot be
  cancelled from here.
- **Unverified at the printer:** duplex (`DUPLEX`/`SIMPLEX` values), black-and-white
  behaviour, multiple copies, A3/Letter paper. Only a one-page, one-sided job was released.
- **Job files** are removed by the spool retention setting (24 h); CUPS-mode job retention
  is not relevant in LPD mode.
- **Not published:** a native CUPS filter/PPD for the Fujifilm driver (by DS) lives on the
  local branch `fujifilm-ff-linux`. It is derived from the vendor's driver, whose licence
  forbids reverse engineering and redistribution, so it is not pushed. It is untested at the
  printer and unnecessary while the LPD backend works.
