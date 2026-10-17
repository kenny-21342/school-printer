"""Direct LPD + PJL submission to the campus printer.

The wire format follows paoprint (github.com/PaoDevelopers/paokit, BSD-2-Clause,
(c) Runxi Yu): queue select, control file, data file, each acknowledged by a zero
byte. A fake server records what arrives so the bytes can be checked exactly.
"""
import socket
import stat
import threading

import pytest

from .conftest import MINIMAL_PDF

UEL = b"\x1b%-12345X"
EOJ = b"\x04\x1b%-12345X@PJL EOJ\n"


class FakeLpd:
    """Accepts one connection per job, acknowledges every step, records the bytes."""

    def __init__(self, reject_at=None):
        self.reject_at = reject_at          # name of the step to refuse, e.g. "control body"
        self.jobs = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _ack(self, conn, step):
        conn.sendall(b"\x01" if step == self.reject_at else b"\x00")
        return step != self.reject_at

    @staticmethod
    def _line(f):
        return f.readline()

    def _handle(self, conn):
        f = conn.makefile("rb")
        job = {}
        try:
            line = self._line(f)
            assert line[:1] == b"\x02"
            job["queue"] = line[1:-1].decode()
            if not self._ack(conn, "queue"):
                return
            hdr = self._line(f)
            assert hdr[:1] == b"\x02"
            n, name = hdr[1:-1].split(b" ", 1)
            job["cf_name"] = name.decode()
            if not self._ack(conn, "control header"):
                return
            body = f.read(int(n))
            assert f.read(1) == b"\x00"
            job["control"] = body.decode()
            if not self._ack(conn, "control body"):
                return
            hdr = self._line(f)
            assert hdr[:1] == b"\x03"
            n, name = hdr[1:-1].split(b" ", 1)
            job["df_name"], job["df_len"] = name.decode(), int(n)
            if not self._ack(conn, "data header"):
                return
            data = f.read(int(n))
            assert f.read(1) == b"\x00"
            job["data"] = data
            self._ack(conn, "data")
            self.jobs.append(job)
        except AssertionError:
            job["protocol_error"] = True
            self.jobs.append(job)
        finally:
            conn.close()

    def close(self):
        self.srv.close()


@pytest.fixture()
def lpd_server(env, monkeypatch):
    server = FakeLpd()
    from app.config import settings
    monkeypatch.setattr(settings, "lpd_host", "127.0.0.1")
    monkeypatch.setattr(settings, "lpd_port", server.port)
    monkeypatch.setattr(settings, "lpd_queue", "sjbw")
    yield server
    server.close()


@pytest.fixture()
def ps_file(tmp_path):
    p = tmp_path / "doc.ps"
    p.write_bytes(b"%!PS-Adobe-3.0\nshowpage\n")
    return p


def test_the_protocol_bytes(lpd_server, ps_file):
    from app import lpd
    number = lpd.submit_ps(ps_file, user="s21342", title="Homework", copies=2, duplex=True)
    (job,) = lpd_server.jobs
    assert "protocol_error" not in job
    assert job["queue"] == "sjbw"
    host = lpd.local_host()
    assert job["cf_name"] == f"cfA{number:03d}{host}" and job["df_name"] == f"dfA{number:03d}{host}"
    assert job["control"].splitlines() == [
        f"H{host}", "Ps21342", f"l{job['df_name']}", "JHomework", f"U{job['df_name']}", "NHomework"]
    data = job["data"]
    assert data.startswith(UEL + b"@PJL ")
    assert data.endswith(b"%!PS-Adobe-3.0\nshowpage\n" + EOJ)
    assert job["df_len"] == len(data)                     # the announced length is exact
    assert data.index(b"@PJL ENTER LANGUAGE=POSTSCRIPT\r\n") < data.index(b"%!PS-Adobe")


def test_pjl_header_matches_paoprints_and_has_no_password():
    from app import lpd
    header = lpd.pjl_header(host="imac", user="s21342", title="Homework", copies=1, duplex=True)
    assert header == (
        "@PJL COMMENT FXJOBINFO STAPLE=OFF\r\n"
        "@PJL SET STRINGCODESET=UTF8\r\n"
        "@PJL JOB MODE=PRINTER\r\n"
        '@PJL SET JOBATTR="@CNAM=imac"\r\n'
        '@PJL SET JOBATTR="@LUNA=s21342"\r\n'
        '@PJL SET JOBATTR="@HOAD=I0A02BCE5"\r\n'
        '@PJL SET JOBATTR="@NLPP=1"\r\n'
        '@PJL SET JOBATTR="@JOAU=s21342"\r\n'
        '@PJL SET JOBATTR="@JOEP="\r\n'
        '@PJL SET JOBATTR="@DAID="\r\n'
        '@PJL SET JOBATTR="@ACNA=Homework"\r\n'
        '@PJL SET JOBATTR="@DIND=PORTRAIT"\r\n'
        "@PJL SET RENDERMODE=COLOR\r\n"
        "@PJL COMMENT FXJOBINFO VERSION=1.5.0\r\n"
        "@PJL COMMENT FXJOBINFO BEGIN\r\n"
        "@PJL COMMENT FXJOBINFO PDLTYPE=PDL[POSTSCRIPT]:VERSION[3]\r\n"
        "@PJL COMMENT FXJOBINFO JOBTYPE=PRINT\r\n"
        "@PJL COMMENT FXJOBINFO JOBCOPIES=1\r\n"
        "@PJL COMMENT FXJOBINFO PAGECOPIES=1\r\n"
        "@PJL COMMENT FXJOBINFO DUPLEXTYPE=DUPLEX\r\n"
        "@PJL COMMENT FXJOBINFO COLORMODE=COLOR\r\n"
        "@PJL COMMENT FXJOBINFO NUP=1\r\n"
        "@PJL COMMENT FXJOBINFO ORIENTATION=PORTRAIT\r\n"
        "@PJL COMMENT FXJOBINFO MIXEDSIZETYPE=NONE\r\n"
        "@PJL COMMENT FXJOBINFO CUD=OFF\r\n"
        "@PJL COMMENT FXJOBINFO END\r\n"
        "@PJL ENTER LANGUAGE=POSTSCRIPT\r\n")


def test_copies_and_sides_reach_the_job_ticket():
    from app import lpd
    h = lpd.pjl_header(host="h", user="s21342", title="t", copies=3, duplex=False)
    assert "JOBCOPIES=3\r\n" in h and "PAGECOPIES=1\r\n" in h
    assert "DUPLEXTYPE=SIMPLEX\r\n" in h


@pytest.mark.parametrize("title", [
    'a"b', "line1\nline2", "x\r\n@PJL SET JOBATTR=\"@JOAU=victim\"", "tab\tname", "back\\slash", "\x1b%-12345X",
])
def test_a_job_name_cannot_inject_lpd_or_pjl_lines(lpd_server, ps_file, title):
    from app import lpd
    lpd.submit_ps(ps_file, user="s21342", title=title)
    (job,) = lpd_server.jobs
    assert len(job["control"].splitlines()) == 6          # still exactly H P l J U N
    header = job["data"].split(b"@PJL ENTER LANGUAGE=POSTSCRIPT\r\n")[0]
    assert header.count(b"@JOAU=") == 1                    # no second identity smuggled in
    assert header.count(b"\x1b%-12345X") == 1              # only our own UEL
    assert b"@JOAU=s21342" in header


def test_unicode_job_names_survive_and_long_ones_are_cut(lpd_server, ps_file):
    from app import lpd
    lpd.submit_ps(ps_file, user="s21342", title="作业 第三章.pdf")
    lpd.submit_ps(ps_file, user="s21342", title="x" * 500)
    first, second = lpd_server.jobs
    assert "J作业 第三章.pdf" in first["control"]
    assert len(second["control"].splitlines()[3].encode()) <= 101


@pytest.mark.parametrize("user", ["", "a", "bad user", "x;y", "../x", "a\nb", "é" * 3])
def test_only_plausible_school_ids_are_accepted(lpd_server, ps_file, user):
    from app import lpd
    with pytest.raises(lpd.LpdError):
        lpd.submit_ps(ps_file, user=user, title="t")
    assert lpd_server.jobs == []


@pytest.mark.parametrize("step", ["queue", "control header", "control body", "data header", "data"])
def test_a_refusal_at_any_step_is_an_error_naming_the_step(env, monkeypatch, ps_file, step):
    from app import lpd
    from app.config import settings
    server = FakeLpd(reject_at=step)
    monkeypatch.setattr(settings, "lpd_host", "127.0.0.1")
    monkeypatch.setattr(settings, "lpd_port", server.port)
    with pytest.raises(lpd.LpdError, match=step):
        lpd.submit_ps(ps_file, user="s21342", title="t")
    server.close()


def test_unreachable_printer_is_a_clear_error(env, monkeypatch, ps_file):
    from app import lpd
    from app.config import settings
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    monkeypatch.setattr(settings, "lpd_host", "127.0.0.1")
    monkeypatch.setattr(settings, "lpd_port", port)
    with pytest.raises(lpd.LpdError, match="printer"):
        lpd.submit_ps(ps_file, user="s21342", title="t")


def test_job_numbers_increase_and_wrap_at_1000(env):
    from app import lpd
    first = lpd.next_job_number()
    assert lpd.next_job_number() == (first + 1) % 1000
    (env.tmp_path / "data" / "lpd_job_number").write_text("999")
    assert lpd.next_job_number() == 0
    assert lpd.next_job_number() == 1


def _fake_pdftops(tmp_path, ok=True):
    """Stands in for `pdftops [options] in.pdf out.ps`."""
    p = tmp_path / "fake_pdftops"
    p.write_text("#!/usr/bin/env bash\n" + (
        'echo "$@" >> "$PDFTOPS_ARGV"\nin="${@: -2:1}"; out="${@: -1}"\n'
        'printf "%%!PS-Adobe-3.0\\n%% converted\\n" > "$out"; cat "$in" >> "$out"\n' if ok else "exit 1\n"))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_pdf_is_converted_to_postscript_before_sending(lpd_server, env, monkeypatch):
    from app import lpd
    from app.config import settings
    monkeypatch.setenv("PDFTOPS_ARGV", str(env.tmp_path / "argv"))
    monkeypatch.setattr(settings, "pdftops_binary", str(_fake_pdftops(env.tmp_path)))
    pdf = env.tmp_path / "in.pdf"
    pdf.write_bytes(MINIMAL_PDF)
    lpd.print_pdf(pdf, user="s21342", title="t", copies=1, duplex=True, paper="A4", page_range="2-4")
    (job,) = lpd_server.jobs
    assert b"% converted" in job["data"]
    argv = (env.tmp_path / "argv").read_text()
    assert "-level3" in argv and "-paper A4" in argv and "-duplex" in argv
    assert "-f 2" in argv and "-l 4" in argv


def test_postscript_input_is_sent_as_is(lpd_server, env):
    from app import lpd
    ps = env.tmp_path / "in.ps"
    ps.write_bytes(b"%!PS-Adobe-3.0\nshowpage\n")
    lpd.print_pdf(ps, user="s21342", title="t")
    assert b"showpage" in lpd_server.jobs[0]["data"]


def test_unsupported_input_and_page_ranges_are_explained(lpd_server, env, monkeypatch):
    from app import lpd
    from app.config import settings
    monkeypatch.setattr(settings, "pdftops_binary", str(_fake_pdftops(env.tmp_path)))
    img = env.tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    with pytest.raises(lpd.LpdError, match="PDF"):
        lpd.print_pdf(img, user="s21342", title="t")
    pdf = env.tmp_path / "in.pdf"
    pdf.write_bytes(MINIMAL_PDF)
    with pytest.raises(lpd.LpdError, match="page range"):
        lpd.print_pdf(pdf, user="s21342", title="t", page_range="1-3,5")
    assert lpd_server.jobs == []


def test_a_failing_converter_is_reported(lpd_server, env, monkeypatch):
    from app import lpd
    from app.config import settings
    monkeypatch.setattr(settings, "pdftops_binary", str(_fake_pdftops(env.tmp_path, ok=False)))
    pdf = env.tmp_path / "in.pdf"
    pdf.write_bytes(MINIMAL_PDF)
    with pytest.raises(lpd.LpdError, match="convert"):
        lpd.print_pdf(pdf, user="s21342", title="t")


def test_real_pdftops_produces_postscript_the_printer_can_take(lpd_server, env):
    import shutil
    if not (shutil.which("pdftops") or shutil.which("gs")):
        pytest.skip("neither pdftops nor Ghostscript installed")
    from app import lpd
    from app.airprint import verification_pdf
    pdf = env.tmp_path / "v.pdf"
    pdf.write_bytes(verification_pdf("123456", "s21342"))
    lpd.print_pdf(pdf, user="s21342", title="t")
    body = lpd_server.jobs[0]["data"].split(b"@PJL ENTER LANGUAGE=POSTSCRIPT\r\n", 1)[1]
    assert body.startswith(b"%!PS")


def test_relay_prints_through_lpd_when_selected(client, env, lpd_server, student_auth, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "print_backend", "lpd")
    monkeypatch.setattr(settings, "pdftops_binary", str(_fake_pdftops(env.tmp_path)))
    monkeypatch.setenv("PDFTOPS_ARGV", str(env.tmp_path / "argv"))
    r = client.post("/print", headers=student_auth, data={"copies": "2", "duplex": "true"},
                    files={"file": ("hw.pdf", MINIMAL_PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"
    (job,) = lpd_server.jobs
    assert "Palice\n" in job["control"] and b"@JOAU=alice" in job["data"]
    assert b"JOBCOPIES=2" in job["data"]
    assert env.argv_log.read_text() == ""                          # CUPS (lp) was not used
    jid = r.json()["id"]
    refreshed = client.post(f"/jobs/{jid}/refresh", headers=student_auth)
    assert refreshed.json()["status"] == "done"                    # accepted by the printer


def test_relay_reports_a_printer_failure_to_the_student(client, env, student_auth, monkeypatch):
    from app.config import settings
    server = FakeLpd(reject_at="control body")
    monkeypatch.setattr(settings, "print_backend", "lpd")
    monkeypatch.setattr(settings, "lpd_host", "127.0.0.1")
    monkeypatch.setattr(settings, "lpd_port", server.port)
    monkeypatch.setattr(settings, "pdftops_binary", str(_fake_pdftops(env.tmp_path)))
    r = client.post("/print", headers=student_auth,
                    files={"file": ("hw.pdf", MINIMAL_PDF, "application/pdf")})
    assert r.status_code == 201
    assert r.json()["status"] == "error" and "control body" in (r.json()["error"] or "")
    server.close()


def test_cli_dump_mode_writes_the_exact_stream_without_a_network(env, ps_file, capsysbinary):
    from app import lpd_cli
    assert lpd_cli.main(["--dump", "--user", "s21342", "--title", "t", str(ps_file)]) == 0
    out = capsysbinary.readouterr().out
    assert out.startswith(b"\x02sjbw\n") and out.endswith(EOJ + b"\x00")


def test_cli_prints_and_exits_nonzero_on_refusal(env, monkeypatch, ps_file):
    from app import lpd_cli
    from app.config import settings
    ok, bad = FakeLpd(), FakeLpd(reject_at="queue")
    monkeypatch.setattr(settings, "lpd_host", "127.0.0.1")
    monkeypatch.setattr(settings, "lpd_port", ok.port)
    assert lpd_cli.main(["--user", "s21342", "--title", "t", "--copies", "2", "--sides", "two-sided-long-edge", str(ps_file)]) == 0
    assert b"JOBCOPIES=2" in ok.jobs[0]["data"] and b"DUPLEXTYPE=DUPLEX" in ok.jobs[0]["data"]
    monkeypatch.setattr(settings, "lpd_port", bad.port)
    assert lpd_cli.main(["--user", "s21342", "--title", "t", str(ps_file)]) == 1
    ok.close(); bad.close()
