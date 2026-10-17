#!/usr/bin/env python3
"""AirPrint front end for the bw queue.

Runs three things as one unit, so launchd supervises them together:
  - /usr/bin/ippeveprinter on a private port, which speaks IPP and runs
    print-job.sh for each job (but is not advertised as an AirPrint printer);
  - this HTTP proxy on the public port, which de-chunks each request and
    resends it to ippeveprinter with a Content-Length. iOS sends Create-Job as
    a chunked TLS request, and ippeveprinter 2.3.4 judges it "followed by
    document data" before the final chunk has arrived;
  - dns-sd -R, while `bonjour.on` exists, which registers the open printer's
    _ipp._tcp service with the _universal subtype and the URF key that iOS
    requires before it lists a printer.
If the backend or the proxy dies the whole thing exits and launchd restarts it.

Identity. iOS always reports its user as "mobile", and the campus system takes
whatever name arrives with a job, so this proxy decides the name:
  - /ipp/print/<token>  a student's printer. The token is resolved by the relay
    (it owns the registry; see server/app/airprint.py) to a school ID, which
    replaces requesting-user-name before ippeveprinter sees the request.
  - /ipp/print          the open printer, advertised over Bonjour. Only while
    `bonjour.on` exists, and then jobs go out as the school ID in the `owner`
    file next to this script; otherwise it is refused.
Anything else is a 404, so ippeveprinter's own pages are never exposed. The
backend listens on loopback only; if it did not, anyone could skip this proxy
and choose their own name.
"""
import asyncio
import collections
import http.client
import json
import os
import signal
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ipp  # noqa: E402

PUBLIC_PORT = int(os.environ.get("SHIM_PORT", os.environ.get("SP_AIRPRINT_PORT", "8631")))
BACKEND_PORT = int(os.environ.get("SHIM_BACKEND_PORT", "8632"))
BACKEND_URI = f"ipp://localhost:{BACKEND_PORT}/ipp/print"
NAME = "School Print"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("SHIM_STATE_DIR", HERE)
URF = "CP1,IS1-4-5-19,MT1-2-3-4-5-6,RS600,V1.4,W8,DM1"
TXT = [
    "txtvers=1", "qtotal=1", "rp=ipp/print", f"ty={NAME}", f"product=({NAME})",
    "pdl=application/pdf,image/urf", "Color=F", "Duplex=T", "Copies=T",
    f"URF={URF}", "UUID=df2b775c-abe3-3720-692c-959a4e00c692", "priority=0",
    "note=", "printer-state=3", "printer-type=0x809046",
]
RELAY = os.environ.get("SHIM_RELAY", "http://127.0.0.1:8000")
JOBS_PER_HOUR = int(os.environ.get("SHIM_JOBS_PER_HOUR", "30"))
PRINT_JOB, SEND_DOCUMENT = 0x0002, 0x0006
_cache: dict[str, tuple[float, str | None]] = {}     # token -> (expires, school id)
_recent: dict[str, collections.deque] = collections.defaultdict(collections.deque)
HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te",
       "proxy-connection", "expect", "content-length"}


def advertisement_command() -> list[str]:
    if sys.platform == "darwin":
        return [shutil.which("dns-sd") or "/usr/bin/dns-sd", "-R", NAME,
                "_ipp._tcp,_universal", "local", str(PUBLIC_PORT), *TXT]
    return [shutil.which("avahi-publish-service") or "avahi-publish-service",
            "--no-fail", "--subtype=_universal._sub._ipp._tcp",
            NAME, "_ipp._tcp", str(PUBLIC_PORT), *TXT]


def backend_command(spool: str) -> list[str]:
    return [shutil.which("ippeveprinter") or "/usr/bin/ippeveprinter",
            "-n", "localhost", "-p", str(BACKEND_PORT), "-d", spool,
            "-c", os.path.join(HERE, "print-job.sh"), "-f", "application/pdf,image/urf",
            "-2", "-s", "20", "-r", "_internal" if sys.platform == "darwin" else "off",
            "-M", "School", "-m", "Print", f"{NAME} backend"]


def resolve(token: str) -> str | None:
    """School ID for an approved token; None if unknown or revoked. Raises if the relay is down."""
    hit = _cache.get(token)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        with urllib.request.urlopen(f"{RELAY}/airprint/resolve/{token}", timeout=5) as r:
            school_id = json.load(r)["school_id"]
            ttl = 20
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        school_id, ttl = None, 5
    _cache[token] = (time.monotonic() + ttl, school_id)
    return school_id


def open_owner() -> str | None:
    """The account the open printer prints as, only while it is switched on.

    `off` must mean off: without this check the path stays reachable by address
    (a profile, or "Add Printer" by IP) after the Bonjour entry is withdrawn.
    """
    if not os.path.exists(os.path.join(STATE_DIR, "bonjour.on")):
        return None
    try:
        with open(os.path.join(STATE_DIR, "owner")) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def over_limit(identity: str) -> bool:
    now, q = time.monotonic(), _recent[identity]
    while q and q[0] < now - 3600:
        q.popleft()
    if len(q) >= JOBS_PER_HOUR:
        return True
    q.append(now)
    return False


async def read_body(reader, writer, headers):
    if headers.get("expect", "").lower() == "100-continue":
        writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        await writer.drain()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = bytearray()
        while True:
            size = int((await reader.readline()).split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while (await reader.readline()).strip():  # trailers
                    pass
                return bytes(body)
            body += await reader.readexactly(size)
            await reader.readexactly(2)
    return await reader.readexactly(int(headers.get("content-length", 0)))


def forward(method, path, headers, body):
    conn = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=120)
    out = {k: v for k, v in headers.items() if k not in HOP and k != "host"}
    out["Host"] = f"localhost:{BACKEND_PORT}"
    out["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=out)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.reason, resp.getheaders(), data


def _plain(status: int, reason: str, text: str):
    return status, reason, [("Content-Type", "text/plain")], text.encode(), None


async def respond(method, path, headers, body):
    """Authorize, rewrite, forward. Returns status, reason, headers, data, identity."""
    base, _, token = path.partition("/ipp/print/")
    if path != "/ipp/print" and not (base == "" and token and "/" not in token):
        return _plain(404, "Not Found", "not found")
    try:
        identity = await asyncio.to_thread(resolve, token) if token else open_owner()
    except Exception as e:  # relay down: fail closed
        print(f"resolve failed: {e!r}", file=sys.stderr, flush=True)
        return _plain(503, "Service Unavailable", "registry unavailable")
    if not identity:
        return _plain(403, "Forbidden", "no printer here")
    display = f"Print as {identity}" if token else NAME
    if method == "POST":
        if ipp.operation_id(body) in (PRINT_JOB, SEND_DOCUMENT) and over_limit(identity):
            return _plain(429, "Too Many Requests", "hourly print limit reached")
        body = ipp.set_attribute(body, "requesting-user-name", identity, 0x42)
        # The request names the printer by the URL the client used, token and
        # all; ippeveprinter refuses a printer-uri it does not recognise.
        body = ipp.set_attribute(body, "printer-uri", BACKEND_URI, 0x45, insert=False)
    status, reason, rh, data = await asyncio.to_thread(forward, method, "/ipp/print", headers, body)
    if any(k.lower() == "content-type" and v.startswith("application/ipp") for k, v in rh):
        for attr_name, tag in (("printer-name", 0x42), ("printer-info", 0x41),
                               ("printer-make-and-model", 0x41)):
            data = ipp.set_attribute(data, attr_name, display, tag, insert=False)
    return status, reason, rh, data, identity


async def handle(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            method, path, _ = line.decode().split(" ", 2)
            headers = {}
            while True:
                h = (await reader.readline()).decode()
                if h in ("\r\n", "\n", ""):
                    break
                k, _, v = h.partition(":")
                headers[k.strip().lower()] = v.strip()
            body = await read_body(reader, writer, headers)
            peer = writer.get_extra_info("peername")
            who = peer[0] if peer else "?"
            status, reason, rh, data, identity = await respond(method, path, headers, body)
            head = f"HTTP/1.1 {status} {reason}\r\n"
            for k, v in rh:
                if k.lower() not in HOP:
                    head += f"{k}: {v}\r\n"
            head += f"Content-Length: {len(data)}\r\nConnection: keep-alive\r\n\r\n"
            writer.write(head.encode() + data)
            await writer.drain()
            shown = "/ipp/print/<token>" if "/ipp/print/" in path else path
            print(f"{who} {method} {shown} as={identity or '-'} {len(body)}B -> {status}", flush=True)
    except (asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    except Exception as e:  # keep serving other clients
        print(f"proxy error: {e!r}", file=sys.stderr, flush=True)
    finally:
        writer.close()


async def bonjour_loop():
    """Advertise the open printer only while `bonjour.on` exists and an owner is set.

    Students' printers are installed by profile and need no advertisement; the
    Bonjour entry is the one anyone on the network could see, so it is a switch
    (tools/school-print on|off) and not a default.
    """
    flag, proc = os.path.join(STATE_DIR, "bonjour.on"), None
    try:
        while True:
            want = os.path.exists(flag) and open_owner() is not None
            if proc is not None and proc.returncode is not None:
                proc = None
            if want and proc is None:
                proc = await asyncio.create_subprocess_exec(
                    *advertisement_command(), stdout=asyncio.subprocess.DEVNULL)
            elif not want and proc is not None:
                proc.terminate()
                await proc.wait()
                proc = None
            await asyncio.sleep(2)
    finally:
        if proc is not None and proc.returncode is None:
            proc.terminate()
            await proc.wait()


async def main():
    spool = os.path.join(STATE_DIR, "spool")
    os.makedirs(spool, exist_ok=True)
    backend = await asyncio.create_subprocess_exec(*backend_command(spool))
    tasks, server = [], None
    try:
        await asyncio.sleep(2)
        if backend.returncode is not None:
            raise RuntimeError(f"IPP backend exited with {backend.returncode}")
        # asyncio makes a "::" socket IPv6-only, so name both families explicitly.
        server = await asyncio.start_server(handle, ["0.0.0.0", "::"], PUBLIC_PORT)
        print(f"listening on {PUBLIC_PORT}, backend {BACKEND_PORT}", flush=True)
        tasks = [asyncio.create_task(backend.wait()), asyncio.create_task(bonjour_loop()),
                 asyncio.create_task(server.serve_forever())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # surface failures to the supervisor
        return 1
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if server is not None:
            server.close()
            await server.wait_closed()
        if backend.returncode is None:
            backend.terminate()
        await backend.wait()


async def supervised_main():
    task = asyncio.create_task(main())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        return await task
    except asyncio.CancelledError:
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(supervised_main()))
