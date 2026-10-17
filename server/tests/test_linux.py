"""Linux conversion and AirPrint behavior without a physical printer."""
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

import pytest

SHIM_DIR = Path(__file__).resolve().parents[2] / "deploy" / "airprint-shim"
sys.path.insert(0, str(SHIM_DIR))
import shim


@pytest.fixture
def libreoffice(env, monkeypatch):
    from app.config import settings
    fake = env.tmp_path / "libreoffice"
    log = env.tmp_path / "lo-args.json"
    fake.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ['LO_ARGS']).write_text(json.dumps(args))
src = Path(args[-1])
out = Path(args[args.index('--outdir') + 1])
(out / (src.stem + '.pdf')).write_bytes(b'%PDF-1.4\\nconverted\\n%%EOF')
''')
    fake.chmod(0o755)
    monkeypatch.setenv("LO_ARGS", str(log))
    monkeypatch.setattr(settings, "office_backend", "libreoffice")
    monkeypatch.setattr(settings, "office_convert_binary", str(fake))
    return fake, log


def test_backend_auto_selects_linux_and_preserves_macos(env, monkeypatch):
    from app import office
    from app.config import settings
    monkeypatch.setattr(settings, "office_backend", "auto")
    monkeypatch.setattr(office.sys, "platform", "linux")
    assert office.backend() == "libreoffice"
    monkeypatch.setattr(office.sys, "platform", "darwin")
    assert office.backend() == "applescript"


def test_linux_upload_conversion_preserves_school_identity(client, student_auth, libreoffice):
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    result = client.post("/print", headers=student_auth,
                         files={"file": ("report.docx", b"PK\x03\x04fake", mime)},
                         data={"duplex": "true"})
    assert result.status_code == 201
    assert client.env.main.run_conversions_once() == 1
    job = client.get(f"/jobs/{result.json()['id']}", headers=student_auth).json()
    assert job["status"] == "pending"
    argv = client.env.argv_log.read_text()
    assert "-U alice " in argv
    assert "sides=two-sided-long-edge" in argv
    assert argv.strip().endswith(".pdf")
    args = json.loads(libreoffice[1].read_text())
    assert "--headless" in args
    assert args[0].startswith("-env:UserInstallation=file://")
    assert not list((client.env.tmp_path / "data").glob("convert-*"))


def test_linux_health_does_not_require_gui_or_office_license(client, libreoffice):
    health = client.get("/health").json()["office"]
    assert health["backend"] == "libreoffice"
    assert health["available"] is True
    assert health["requires_gui"] is False
    assert health["gui_session"] is False


@pytest.mark.parametrize("mode", ["missing-pdf", "bad-pdf", "exit-error", "timeout"])
def test_linux_conversion_fails_closed(env, libreoffice, monkeypatch, mode):
    from app import office
    from app.config import settings
    fake, _ = libreoffice
    body = {
        "missing-pdf": "pass",
        "bad-pdf": "from pathlib import Path; a=sys.argv; (Path(a[a.index('--outdir')+1])/(Path(a[-1]).stem+'.pdf')).write_bytes(b'not pdf')",
        "exit-error": "sys.exit(2)",
        "timeout": "import time; time.sleep(20)",
    }[mode]
    fake.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    monkeypatch.setattr(settings, "office_convert_timeout_s", 0.1)
    src, dst = env.tmp_path / "document.docx", env.tmp_path / "result.pdf"
    src.write_bytes(b"test")
    dst.write_bytes(b"stale PDF")
    with pytest.raises(office.ConversionError):
        office.convert_to_pdf(src, dst)
    assert not dst.exists()
    assert not list(settings.data_dir.glob("convert-*"))


def test_linux_missing_converter_is_reported(env, monkeypatch):
    from app import office
    from app.config import settings
    monkeypatch.setattr(settings, "office_backend", "libreoffice")
    monkeypatch.setattr(settings, "office_convert_binary", "")
    monkeypatch.setattr(office.shutil, "which", lambda _: None)
    assert office.office_status()["available"] is False
    src = env.tmp_path / "document.docx"
    src.write_bytes(b"test")
    with pytest.raises(office.ConversionError, match="LibreOffice is not installed"):
        office.convert_to_pdf(src, env.tmp_path / "result.pdf")


def test_avahi_has_airprint_subtype_and_private_backend_not_advertised(monkeypatch):
    monkeypatch.setattr(shim.sys, "platform", "linux")
    args = shim.advertisement_command()
    assert "--subtype=_universal._sub._ipp._tcp" in args
    assert "_ipp._tcp" in args
    backend = shim.backend_command("/tmp/spool")
    assert backend[backend.index("-n") + 1] == "localhost"
    assert backend[backend.index("-r") + 1] == "off"


def test_macos_advertisement_is_preserved(monkeypatch):
    monkeypatch.setattr(shim.sys, "platform", "darwin")
    assert "_ipp._tcp,_universal" in shim.advertisement_command()


@pytest.mark.asyncio
async def test_linux_state_directory_keeps_open_printer_off(tmp_path, monkeypatch):
    monkeypatch.setattr(shim, "STATE_DIR", str(tmp_path))
    (tmp_path / "owner").write_text("alice")
    response = await shim.respond("POST", "/ipp/print", {}, b"")
    assert response[0] == 403
    (tmp_path / "bonjour.on").touch()
    assert shim.open_owner() == "alice"
    (tmp_path / "bonjour.on").unlink()
    assert shim.open_owner() is None


def test_linux_print_command_passes_authenticated_owner_and_settings(tmp_path):
    fake_lp = tmp_path / "lp"
    logfile = tmp_path / "argv"
    fake_lp.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGS"\necho "request id is bw-9 (1 file)"\n')
    fake_lp.chmod(0o755)
    proc = subprocess.run(["bash", str(SHIM_DIR / "print-job.sh"), "document.pdf"],
        env={**os.environ, "SHIM_LP_BINARY": str(fake_lp), "SHIM_LOG": str(tmp_path / "print.log"),
             "TEST_ARGS": str(logfile), "IPP_JOB_ORIGINATING_USER_NAME": "alice",
             "IPP_SIDES": "two-sided-long-edge", "IPP_COPIES": "2", "CONTENT_TYPE": "application/pdf"},
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    args = logfile.read_text().splitlines()
    assert args[:4] == ["-h", "localhost:631", "-U", "alice"]
    assert "sides=two-sided-long-edge" in args
    assert args[args.index("-n") + 1] == "2"


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("ippeveprinter") is None,
                    reason="Linux CUPS backend not installed")
def test_real_linux_backend_starts_without_advertising_or_printing(tmp_path, monkeypatch):
    """Get printer attributes from real CUPS; submit no document or print job."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(shim, "BACKEND_PORT", port)
    spool = tmp_path / "backend-spool"
    spool.mkdir()
    # CUPS initializes Avahi even with advertising off. Give container tests
    # their own D-Bus socket; no daemon or desktop on the host is changed.
    bus, process_env = None, os.environ.copy()
    if not Path("/run/dbus/system_bus_socket").exists():
        daemon = shutil.which("dbus-daemon")
        if not daemon:
            pytest.skip("CUPS needs D-Bus; dbus-daemon is not installed")
        address = f"unix:path={tmp_path / 'dbus'}"
        bus = subprocess.Popen([daemon, "--session", "--nofork", "--nopidfile", f"--address={address}"])
        process_env["DBUS_SYSTEM_BUS_ADDRESS"] = address
        for _ in range(100):
            if (tmp_path / "dbus").exists():
                break
            time.sleep(0.01)
    with (tmp_path / "backend.log").open("w+") as log:
        proc = subprocess.Popen(shim.backend_command(str(spool)), stdout=log, stderr=log, env=process_env)
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    log.seek(0)
                    pytest.fail(f"IPP backend failed to start: {log.read()}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                pytest.fail("IPP backend did not listen")
            def attr(tag, name, value):
                name, value = name.encode(), value.encode()
                return bytes([tag]) + struct.pack(">H", len(name)) + name + struct.pack(">H", len(value)) + value
            body = struct.pack(">BBHI", 1, 1, 0x000B, 1) + b"\x01"
            body += attr(0x47, "attributes-charset", "utf-8")
            body += attr(0x48, "attributes-natural-language", "en")
            body += attr(0x45, "printer-uri", f"ipp://localhost:{port}/ipp/print") + b"\x03"
            request = urllib.request.Request(f"http://127.0.0.1:{port}/ipp/print", data=body,
                                             headers={"Content-Type": "application/ipp"})
            with urllib.request.urlopen(request, timeout=10) as reply:
                response = reply.read()
            assert response[2:4] == b"\x00\x00"
            assert b"printer-name" in response
            assert not list(spool.iterdir())
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            if bus is not None:
                bus.terminate()
                bus.wait(timeout=10)


@pytest.mark.skipif(shutil.which("libreoffice") is None, reason="LibreOffice not installed")
def test_real_headless_libreoffice_docx_to_pdf(env, monkeypatch):
    """Runs in Linux CI with the real converter, independent of any desktop."""
    from app import office
    from app.config import settings
    monkeypatch.setattr(settings, "office_backend", "libreoffice")
    monkeypatch.setattr(settings, "office_convert_binary", "")
    src = env.tmp_path / "sample.docx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("[Content_Types].xml", '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
        z.writestr("_rels/.rels", '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
        z.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>School Print Linux conversion test</w:t></w:r></w:p></w:body></w:document>''')
    dst = env.tmp_path / "converted.pdf"
    office.convert_to_pdf(src, dst)
    assert dst.read_bytes().startswith(b"%PDF-")
    assert dst.stat().st_size > 100
