"""Per-student AirPrint registry: request -> approve -> profile / key -> print."""
import plistlib
import re

import pytest
from fastapi.testclient import TestClient

from .conftest import MINIMAL_PDF


def _register(client, school_id="s21342", name="Kenny"):
    r = client.post("/register", data={"school_id": school_id, "name": name},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]          # /register/<code>


def _approve(env, school_id="s21342"):
    from app import airprint
    req = [r for r in airprint.list_requests() if r["school_id"] == school_id][-1]
    return req["id"], airprint.approve(req["id"])


@pytest.fixture()
def local(env):
    """A client that appears to come from loopback, like the shim."""
    with TestClient(env.app, client=("127.0.0.1", 50000)) as c:
        yield c


def test_register_page_and_pending(client):
    assert "School ID" in client.get("/register").text
    path = _register(client)
    page = client.get(path)
    assert page.status_code == 200
    assert "waiting" in page.text.lower()
    assert "mobileconfig" not in page.text


@pytest.mark.parametrize("bad", ["", "a", "x y", "../etc", "s1;rm", "é" * 3, "a" * 65])
def test_register_rejects_bad_school_ids(client, bad):
    r = client.post("/register", data={"school_id": bad, "name": "x"}, follow_redirects=False)
    assert r.status_code in (400, 422)


def test_unknown_request_code_is_404(client):
    assert client.get("/register/" + "0" * 32).status_code == 404


def test_html_is_escaped(client):
    path = _register(client, name="<script>alert(1)</script>")
    assert "<script>alert" not in client.get(path).text


def test_approved_request_serves_profile(client, env):
    path = _register(client)
    _id, token = _approve(env)
    page = client.get(path).text
    assert "profile.mobileconfig" in page
    assert token in page                      # shown for the Shortcut
    prof = client.get(path + "/profile.mobileconfig")
    assert prof.status_code == 200
    assert prof.headers["content-type"].startswith("application/x-apple-aspen-config")
    data = plistlib.loads(prof.content)
    printer = data["PayloadContent"][0]["AirPrint"][0]
    assert printer["ResourcePath"] == f"ipp/print/{token}"
    assert printer["Port"] == 8631
    assert printer["ForceTLS"] is False
    assert printer["IPAddress"] == "testserver"


def test_profile_not_available_until_approved(client):
    path = _register(client)
    assert client.get(path + "/profile.mobileconfig").status_code == 404


def test_resolve_is_loopback_only(client, local, env):
    _register(client)
    _id, token = _approve(env)
    assert client.get(f"/airprint/resolve/{token}").status_code == 403   # "testclient"
    r = local.get(f"/airprint/resolve/{token}")
    assert r.status_code == 200
    assert r.json()["school_id"] == "s21342"


def test_resolve_rejects_unknown_forged_and_revoked(client, local, env):
    from app import airprint
    _register(client)
    req_id, token = _approve(env)
    assert local.get("/airprint/resolve/garbage").status_code == 404
    assert local.get(f"/airprint/resolve/{req_id}-{'0' * 32}").status_code == 404
    airprint.revoke(req_id)
    assert local.get(f"/airprint/resolve/{token}").status_code == 404


def test_print_with_key_submits_as_school_account(client, env):
    _register(client)
    _id, token = _approve(env)
    r = client.post("/print", headers={"X-Print-Key": token},
                    files={"file": ("a.pdf", MINIMAL_PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    argv = env.argv_log.read_text().split()
    assert argv[argv.index("-U") + 1] == "s21342"
    assert argv[argv.index("-h") + 1] == "localhost:631"


def test_print_with_bad_or_revoked_key_is_refused(client, env):
    from app import airprint
    _register(client)
    req_id, token = _approve(env)
    files = {"file": ("a.pdf", MINIMAL_PDF, "application/pdf")}
    assert client.post("/print", headers={"X-Print-Key": "nope"}, files=files).status_code == 401
    airprint.revoke(req_id)
    assert client.post("/print", headers={"X-Print-Key": token}, files=files).status_code == 401


def test_jwt_print_also_carries_the_username(client, env, student_auth):
    r = client.post("/print", headers=student_auth,
                    files={"file": ("a.pdf", MINIMAL_PDF, "application/pdf")})
    assert r.status_code == 201
    argv = env.argv_log.read_text().split()
    assert argv[argv.index("-U") + 1] == "alice"


def test_approving_twice_gives_the_same_token_and_reuses_the_user(client, env):
    from app import airprint
    _register(client)
    req_id, token = _approve(env)
    assert airprint.approve(req_id) == token
    _register(client)                          # a second device, same school ID
    _id2, token2 = _approve(env)
    assert token2 != token
    assert airprint.resolve_token(token2)["user_id"] == airprint.resolve_token(token)["user_id"]


def test_pending_requests_are_capped(client, env):
    from app import airprint
    for i in range(airprint.MAX_PENDING):
        airprint.request_access(f"s{i:05d}", "x")
    r = client.post("/register", data={"school_id": "s99999", "name": "x"}, follow_redirects=False)
    assert r.status_code == 429
