"""Proving a school ID is yours: a code is printed under that ID at the campus printer."""
import re

import pytest

from .conftest import FAKE_LP_FAIL


@pytest.fixture()
def verify(env, monkeypatch):
    """Verification on, auto-approve on, and a known code instead of a random one."""
    from app import airprint
    from app.config import settings
    monkeypatch.setattr(settings, "airprint_verify", True)
    monkeypatch.setattr(settings, "airprint_auto_approve_verified", True)
    monkeypatch.setattr(airprint, "_new_code", lambda: "123456")
    return airprint


def _register(client, school_id="s21342"):
    r = client.post("/register", data={"school_id": school_id, "name": "K"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]


def _verify(client, path, code):
    return client.post(path + "/verify", data={"code": code}, follow_redirects=False)


def test_registering_prints_a_code_under_that_school_id(client, env, verify):
    path = _register(client)
    argv = env.argv_log.read_text().split()
    assert argv[argv.index("-U") + 1] == "s21342"                 # released by that account's login
    assert "verification" in env.argv_log.read_text().lower()
    page = client.get(path).text
    assert "campus printer" in page.lower() and 'name="code"' in page
    assert "profile.mobileconfig" not in page                      # nothing until proven


def test_right_code_approves_automatically(client, env, verify):
    path = _register(client)
    assert _verify(client, path, "123456").status_code == 303
    page = client.get(path).text
    assert "profile.mobileconfig" in page
    assert client.get(path + "/profile.mobileconfig").status_code == 200


def test_right_code_waits_for_the_administrator_when_auto_approve_is_off(client, env, verify, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "airprint_auto_approve_verified", False)
    path = _register(client)
    _verify(client, path, "123456")
    assert "profile.mobileconfig" not in client.get(path).text
    row = verify.list_requests()[0]
    assert row["verified_at"] and row["status"] == "pending"
    verify.approve(row["id"])                                      # the manual route still works
    assert "profile.mobileconfig" in client.get(path).text


def test_wrong_codes_are_counted_and_lock_after_five(client, env, verify):
    path = _register(client)
    for _ in range(5):
        r = _verify(client, path, "000000")
        assert r.status_code == 200 and "incorrect" in r.text.lower()
    r = _verify(client, path, "123456")                            # right code, but locked out
    assert r.status_code == 429
    assert "profile.mobileconfig" not in client.get(path).text


def test_resend_issues_a_new_code_and_invalidates_the_old_one(client, env, verify, monkeypatch):
    path = _register(client)
    monkeypatch.setattr(verify, "_new_code", lambda: "654321")
    verify.RESEND_DELAY_S = 0
    assert client.post(path + "/resend", follow_redirects=False).status_code == 303
    _verify(client, path, "123456")
    assert "profile.mobileconfig" not in client.get(path).text
    _verify(client, path, "654321")
    assert "profile.mobileconfig" in client.get(path).text


def test_resend_is_limited(client, env, verify):
    path = _register(client)
    verify.RESEND_DELAY_S = 0
    codes = [client.post(path + "/resend", follow_redirects=False).status_code for _ in range(6)]
    assert 429 in codes


def test_one_school_id_cannot_be_flooded_with_verification_pages(client, env, verify):
    for _ in range(verify.MAX_PER_ID_PER_HOUR):
        _register(client)
    r = client.post("/register", data={"school_id": "s21342", "name": "x"}, follow_redirects=False)
    assert r.status_code == 429
    assert _register(client, "s99999")                              # other IDs unaffected


def test_a_failed_print_is_reported_and_can_be_retried(client, env, verify):
    env.lp.write_text(FAKE_LP_FAIL)
    path = _register(client)
    page = client.get(path).text
    assert "could not" in page.lower() or "try again" in page.lower()
    assert "profile.mobileconfig" not in page


def test_verification_pdf_is_a_valid_pdf_with_the_code():
    from app.airprint import verification_pdf
    pdf = verification_pdf("123456", "s21342")
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert b"123456" in pdf and b"s21342" in pdf
    # xref offsets must point at the objects they claim to
    start = int(re.search(rb"startxref\s+(\d+)", pdf).group(1))
    assert pdf[start:start + 4] == b"xref"
    offsets = re.findall(rb"(\d{10}) 00000 n", pdf)
    for n, off in enumerate(offsets, start=1):
        assert pdf[int(off):].startswith(b"%d 0 obj" % n)
