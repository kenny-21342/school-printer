"""Regression tests for the audit findings.

Each test names the finding ID from the remediation plan so a future failure
points straight at what it is protecting.
"""
from __future__ import annotations

import time

import pytest

from .conftest import MINIMAL_PDF, _token


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def test_login_success_and_failure(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert r.status_code == 200
    assert r.json()["is_admin"] is True

    r = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401

    r = client.post("/auth/login", json={"username": "nobody", "password": "wrong"})
    assert r.status_code == 401


def test_unknown_username_is_not_faster_than_wrong_password(client):
    """M12: the constant-time mitigation must actually burn argon2 time."""
    def timed(username):
        start = time.perf_counter()
        client.post("/auth/login", json={"username": username, "password": "wrong"})
        return time.perf_counter() - start

    miss = min(timed("definitely-not-a-user") for _ in range(3))
    hit = min(timed("admin") for _ in range(3))
    # The miss path must be within an order of magnitude of the real check.
    # Before the fix it returned essentially instantly.
    assert miss > hit / 5, f"username miss too fast: {miss:.4f}s vs {hit:.4f}s"


def test_account_locks_out_after_repeated_failures(client):
    """H1: per-username throttling, which IP limits cannot provide behind NAT."""
    for _ in range(10):
        client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    r = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 429
    # Even the CORRECT password is refused while locked out.
    r = client.post("/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert r.status_code == 429


def test_successful_login_clears_failure_counter(client):
    for _ in range(3):
        client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert client.post("/auth/login", json={"username": "admin", "password": "adminpass123"}).status_code == 200
    for _ in range(8):
        client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    # 8 < 10 because the counter reset, so this still succeeds.
    assert client.post("/auth/login", json={"username": "admin", "password": "adminpass123"}).status_code == 200


def test_password_change_revokes_existing_tokens(client, admin_auth):
    """H8: a stolen token must not outlive a password reset."""
    assert client.get("/users/me", headers=admin_auth).status_code == 200
    r = client.patch("/users/me", headers=admin_auth, json={
        "current_password": "adminpass123", "new_password": "newpass4567",
    })
    assert r.status_code == 200, r.text
    assert client.get("/users/me", headers=admin_auth).status_code == 401
    assert _token(client, "admin", "newpass4567")


def test_self_password_change_requires_current_password(client, student_auth):
    r = client.patch("/users/me", headers=student_auth, json={
        "current_password": "wrong", "new_password": "brandnew123",
    })
    assert r.status_code == 403


def test_students_can_change_their_own_password(client, student_auth):
    """L7: previously only admins could change any password."""
    r = client.patch("/users/me", headers=student_auth, json={
        "current_password": "alicepass1", "new_password": "alicenew999",
    })
    assert r.status_code == 200
    assert _token(client, "alice", "alicenew999")


def test_admin_endpoints_reject_students(client, student_auth):
    assert client.get("/users", headers=student_auth).status_code == 403
    assert client.post("/users", headers=student_auth, json={
        "username": "bob", "password": "bobpass12",
    }).status_code == 403


def test_missing_and_malformed_tokens_rejected(client):
    assert client.get("/users/me").status_code == 401
    assert client.get("/users/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_admin_cannot_lock_themselves_out(client, admin_auth):
    """L8: demoting/disabling the only admin would strand the deployment."""
    me = client.get("/users/me", headers=admin_auth).json()
    assert client.patch(f"/users/{me['id']}", headers=admin_auth,
                        json={"is_admin": False}).status_code == 400
    assert client.patch(f"/users/{me['id']}", headers=admin_auth,
                        json={"is_active": False}).status_code == 400
    assert client.delete(f"/users/{me['id']}", headers=admin_auth).status_code == 400


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------

def test_print_pdf_submits_to_cups(client, student_auth):
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")},
                    data={"copies": "2", "duplex": "true", "color_mode": "monochrome"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["cups_job_id"] == 42       # M3: parsed from "bw-42", not "1"
    assert job["status"] == "pending"
    assert job["copies"] == 2

    argv = client.env.argv_log.read_text()
    assert "-d bw" in argv
    assert "-n 2" in argv
    assert "sides=two-sided-long-edge" in argv


def test_job_response_does_not_leak_spool_path(client, student_auth):
    """L4: server filesystem paths were being returned to every client."""
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")})
    assert "spool_path" not in r.json()


def test_layout_direction_uses_number_up_layout(client, student_auth):
    """F1: cupsBackSide is a PPD attribute, not a user-settable option."""
    client.post("/print", headers=student_auth,
                files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")},
                data={"layout_direction": "rtl"})
    argv = client.env.argv_log.read_text()
    assert "number-up-layout=rltb" in argv
    assert "cupsBackSide" not in argv


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
KEYNOTE = "application/vnd.apple.keynote"


def _upload_docx(client, auth, name="report.docx", **data):
    return client.post("/print", headers=auth,
                       files={"file": (name, b"PK\x03\x04fake", DOCX)},
                       data=data)


def test_office_upload_is_queued_for_conversion(client, student_auth):
    """Office files are accepted now: the API runs on the Mac that has Word."""
    r = _upload_docx(client, student_auth)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "converting"
    assert job["needs_conversion"] is True
    # Nothing has reached the printer yet.
    assert job["cups_job_id"] is None
    assert client.env.argv_log.read_text() == ""


def test_conversion_worker_converts_then_prints(client, student_auth):
    job_id = _upload_docx(client, student_auth, duplex="true").json()["id"]

    assert client.env.main.run_conversions_once() == 1

    job = client.get(f"/jobs/{job_id}", headers=student_auth).json()
    assert job["status"] == "pending"
    assert job["cups_job_id"] == 42

    # The PDF is what went to lp, not the .docx, and the print options survived
    # the round trip through the database.
    argv = client.env.argv_log.read_text()
    assert argv.strip().endswith(".pdf")
    assert "sides=two-sided-long-edge" in argv
    # The original upload is gone; only the converted PDF is kept.
    spooled = sorted(p.suffix for p in client.env.spool.iterdir())
    assert spooled == [".pdf"]


def test_conversion_failure_surfaces_as_job_error(client, student_auth, monkeypatch):
    """A student must see why their document didn't print (C5 again)."""
    from tests.conftest import FAKE_OSASCRIPT_FAIL
    client.env.osascript.write_text(FAKE_OSASCRIPT_FAIL)

    job_id = _upload_docx(client, student_auth).json()["id"]
    client.env.main.run_conversions_once()

    job = client.get(f"/jobs/{job_id}", headers=student_auth).json()
    assert job["status"] == "error"
    assert job["error"]
    assert client.env.argv_log.read_text() == ""      # never printed
    assert list(client.env.spool.iterdir()) == []     # nothing left on disk


def test_cancel_during_conversion_never_prints(client, student_auth):
    job_id = _upload_docx(client, student_auth).json()["id"]
    assert client.delete(f"/jobs/{job_id}", headers=student_auth).status_code == 204

    client.env.main.run_conversions_once()

    job = client.get(f"/jobs/{job_id}", headers=student_auth).json()
    assert job["status"] == "cancelled"
    assert client.env.argv_log.read_text() == ""


def test_only_one_worker_can_claim_a_conversion(client, student_auth):
    """Four uvicorn processes run this loop; a double claim prints twice."""
    job_id = _upload_docx(client, student_auth).json()["id"]
    main = client.env.main
    assert main._claim_conversion(job_id) is True
    assert main._claim_conversion(job_id) is False


def test_stale_claim_is_retried(client, student_auth):
    """A process that died mid-conversion must not strand the job forever."""
    job_id = _upload_docx(client, student_auth).json()["id"]
    main = client.env.main
    assert main._claim_conversion(job_id) is True
    with main.get_conn() as conn:
        conn.execute("UPDATE jobs SET conversion_claimed_at = 0 WHERE id = ?", (job_id,))
    assert main._claim_conversion(job_id) is True


def test_iwork_files_are_rejected_with_a_way_out(client, student_auth):
    """Keynote can't be converted, so say so rather than queue it forever."""
    r = client.post("/print", headers=student_auth,
                    files={"file": ("deck.key", b"PK\x03\x04fake", KEYNOTE)})
    assert r.status_code == 415
    assert "PDF" in r.json()["detail"]
    assert client.get("/jobs", headers=student_auth).json() == []


def test_office_can_be_switched_off(client, student_auth):
    from app.config import settings
    settings.office_conversion_enabled = False
    try:
        r = _upload_docx(client, student_auth)
    finally:
        settings.office_conversion_enabled = True
    assert r.status_code == 415
    assert client.get("/jobs", headers=student_auth).json() == []


def test_health_reports_office_status(client):
    office = client.get("/health").json()["office"]
    assert office["enabled"] is True
    assert office["script_present"] is True


def test_unsupported_mime_rejected(client, student_auth):
    r = client.post("/print", headers=student_auth,
                    files={"file": ("x.bin", b"\x00\x01", "application/octet-stream")})
    assert r.status_code == 415


@pytest.mark.parametrize("data,field", [
    ({"copies": "0"}, "copies"),
    ({"copies": "51"}, "copies"),
    ({"color_mode": "sepia"}, "color_mode"),
    ({"orientation": "sideways"}, "orientation"),
    ({"layout_direction": "ttb"}, "layout_direction"),
    ({"scaling": "0"}, "scaling"),
    ({"scaling": "9999"}, "scaling"),
    ({"scaling": "huge"}, "scaling"),
])
def test_print_option_validation(client, student_auth, data, field):
    """M4 among others: bounds must match what the client and cups.py allow."""
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")},
                    data=data)
    assert r.status_code == 422, f"{field}={data} should have been rejected"


def test_scaling_within_bounds_accepted(client, student_auth):
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")},
                    data={"scaling": "150"})
    assert r.status_code == 201
    assert "cupsScaling=150" in client.env.argv_log.read_text()


def test_upload_size_limit(client, student_auth, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)
    r = client.post("/print", headers=student_auth,
                    files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413
    # L12: the partial upload must not be left behind.
    assert list(client.env.spool.iterdir()) == []


def test_failed_lp_submit_records_error_and_cleans_up(client, student_auth):
    """A job that can never print must not keep its upload on disk."""
    client.env.lp.write_text("#!/usr/bin/env bash\necho 'lp: not responding' >&2\nexit 1\n")
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")})
    assert r.status_code == 201
    assert r.json()["status"] == "error"
    assert list(client.env.spool.iterdir()) == []


# --------------------------------------------------------------------------
# Job access control
# --------------------------------------------------------------------------

def test_students_cannot_read_each_others_jobs(client, admin_auth, student_auth):
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")})
    job_id = r.json()["id"]

    client.post("/users", headers=admin_auth,
                json={"username": "bob", "password": "bobpass123"})
    bob = {"Authorization": f"Bearer {_token(client, 'bob', 'bobpass123')}"}

    assert client.get(f"/jobs/{job_id}", headers=bob).status_code == 403
    assert client.post(f"/jobs/{job_id}/refresh", headers=bob).status_code == 403
    assert client.delete(f"/jobs/{job_id}", headers=bob).status_code == 403
    # the owner still can
    assert client.get(f"/jobs/{job_id}", headers=student_auth).status_code == 200
    # and an admin can
    assert client.get(f"/jobs/{job_id}", headers=admin_auth).status_code == 200


def test_jobs_list_scoping(client, admin_auth, student_auth):
    client.post("/print", headers=student_auth,
                files={"file": ("a.pdf", MINIMAL_PDF, "application/pdf")})
    client.post("/print", headers=admin_auth,
                files={"file": ("b.pdf", MINIMAL_PDF, "application/pdf")})

    assert len(client.get("/jobs", headers=student_auth).json()) == 1
    assert len(client.get("/jobs", headers=admin_auth).json()) == 1
    assert len(client.get("/jobs?include_all=true", headers=admin_auth).json()) == 2
    # a student cannot widen their own scope
    assert len(client.get("/jobs?include_all=true", headers=student_auth).json()) == 1


def test_cancel_marks_job_and_frees_spool(client, student_auth):
    """C3: the spool file must go once the job is in a terminal state."""
    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")})
    job_id = r.json()["id"]
    assert len(list(client.env.spool.iterdir())) == 1

    assert client.delete(f"/jobs/{job_id}", headers=student_auth).status_code == 204
    assert client.get(f"/jobs/{job_id}", headers=student_auth).json()["status"] == "cancelled"
    assert list(client.env.spool.iterdir()) == []


# --------------------------------------------------------------------------
# Spool reaper
# --------------------------------------------------------------------------

def test_sweep_removes_expired_files(client, student_auth):
    """C3: nothing in the original code ever deleted an uploaded file."""
    from app.db import get_conn

    r = client.post("/print", headers=student_auth,
                    files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")})
    job_id = r.json()["id"]
    assert len(list(client.env.spool.iterdir())) == 1

    # Age the job past the retention window and mark it finished.
    old = int(time.time()) - 48 * 3600
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET status='done', created_at=? WHERE id=?", (old, job_id))

    removed = client.env.main.sweep_spool()
    assert removed >= 1
    assert list(client.env.spool.iterdir()) == []


def test_sweep_keeps_current_files(client, student_auth):
    client.post("/print", headers=student_auth,
                files={"file": ("doc.pdf", MINIMAL_PDF, "application/pdf")})
    client.env.main.sweep_spool()
    assert len(list(client.env.spool.iterdir())) == 1


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

def test_health_reports_queue_capabilities(client):
    """F1: /health must make the raw-queue question answerable remotely."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["queue"] == "bw"
    assert "queue_is_raw" in body
    assert "cups_reachable" in body
