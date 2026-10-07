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


# ---------------------------------------------------------------- Shortcut

FAKE_SIGNER = """#!/usr/bin/env bash
# stands in for `shortcuts sign --mode anyone --input X --output Y`
while [ $# -gt 0 ]; do case "$1" in --input) i="$2";; --output) o="$2";; esac; shift; done
printf 'AEA1' > "$o"; cat "$i" >> "$o"
"""


@pytest.fixture()
def signer(env, monkeypatch):
    import stat
    fake = env.tmp_path / "fake_shortcuts"
    fake.write_text(FAKE_SIGNER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    from app.config import settings
    monkeypatch.setattr(settings, "shortcuts_binary", str(fake))
    return fake


def _unwrap(content):
    assert content[:4] == b"AEA1"
    return plistlib.loads(content[4:])


def test_shortcut_has_the_students_key_url_and_a_file_field(client, env, signer):
    path = _register(client)
    _id, token = _approve(env)
    r = client.get(path + "/print.shortcut")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.shortcut")
    wf = _unwrap(r.content)
    assert "ActionExtension" in wf["WFWorkflowTypes"]               # appears in the Share Sheet
    action = wf["WFWorkflowActions"][0]
    assert action["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
    p = action["WFWorkflowActionParameters"]
    assert p["WFURL"] == "http://testserver/print"
    assert p["WFHTTPMethod"] == "POST" and p["WFHTTPBodyType"] == "Form"
    header = p["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"][0]
    assert header["WFKey"]["Value"]["string"] == "X-Print-Key"
    assert header["WFValue"]["Value"]["string"] == token
    form = p["WFFormValues"]["Value"]["WFDictionaryFieldValueItems"][0]
    assert form["WFKey"]["Value"]["string"] == "file"
    assert form["WFValue"]["Value"]["Value"]["Type"] == "ExtensionInput"      # the shared file


def test_shortcut_reads_the_upload_reply_not_its_own_input(client, env, signer):
    path = _register(client)
    _approve(env)
    acts = _unwrap(client.get(path + "/print.shortcut").content)["WFWorkflowActions"]
    upload_uuid = acts[0]["WFWorkflowActionParameters"]["UUID"]
    ref = acts[1]["WFWorkflowActionParameters"]["WFInput"]["Value"]
    assert ref["OutputUUID"] == upload_uuid


def test_shortcut_is_unavailable_until_approved_and_when_signing_fails(client, env, signer):
    path = _register(client)
    assert client.get(path + "/print.shortcut").status_code == 404
    _approve(env)
    signer.write_text("#!/usr/bin/env bash\nexit 1\n")
    assert client.get(path + "/print.shortcut").status_code == 503


def test_approved_page_offers_the_shortcut(client, env):
    path = _register(client)
    _approve(env)
    assert "print.shortcut" in client.get(path).text


def test_a_presigned_shortcut_is_served_even_if_signing_is_unavailable(client, env, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "shortcuts_binary", str(env.tmp_path / "missing"))
    path = _register(client)
    req_id, _token = _approve(env)
    assert client.get(path + "/print.shortcut").status_code == 503          # nothing stored yet
    d = settings.data_dir / "shortcuts"
    d.mkdir(exist_ok=True)
    (d / f"{req_id}.shortcut").write_bytes(b"AEA1-presigned")
    r = client.get(path + "/print.shortcut")
    assert r.status_code == 200 and r.content == b"AEA1-presigned"


def test_cli_emits_the_unsigned_shortcut_for_signing_on_another_machine(client, env, capsysbinary):
    from app import airprint_cli
    _register(client)
    req_id, token = _approve(env)
    assert airprint_cli.main(["unsigned", str(req_id), "http://10.2.187.53:8000/print"]) == 0
    wf = plistlib.loads(capsysbinary.readouterr().out)
    p = wf["WFWorkflowActions"][0]["WFWorkflowActionParameters"]
    assert p["WFURL"] == "http://10.2.187.53:8000/print"
    assert p["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"][0]["WFValue"]["Value"]["string"] == token
