"""The shim's IPP attribute rewriter (deploy/airprint-shim/ipp.py)."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "airprint-shim"))
import ipp  # noqa: E402


def attr(tag, name, value):
    n, v = name.encode(), value if isinstance(value, bytes) else value.encode()
    return bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(v)) + v


def more(tag, value):                      # additional value of a 1setOf
    v = value.encode()
    return bytes([tag]) + b"\x00\x00" + struct.pack(">H", len(v)) + v


HEADER = b"\x01\x01\x00\x02\x00\x00\x00\x07"      # IPP 1.1, Print-Job, request 7
OPS = b"\x01" + attr(0x47, "attributes-charset", "utf-8") + attr(0x48, "attributes-natural-language", "en") \
    + attr(0x45, "printer-uri", "ipp://x/ipp/print") + attr(0x42, "requesting-user-name", "mobile")
DOC = b"%PDF-1.4 binary \x03\x01 data"


def build(ops=OPS, doc=DOC):
    return HEADER + ops + b"\x02" + attr(0x21, "copies", b"\x00\x00\x00\x01") + b"\x03" + doc


def test_rewrites_user_name_and_keeps_everything_else():
    out = ipp.set_attribute(build(), "requesting-user-name", "s21342", 0x42)
    assert attr(0x42, "requesting-user-name", "s21342") in out
    assert b"mobile" not in out
    assert out.endswith(b"\x03" + DOC)               # document bytes untouched
    assert out.startswith(HEADER)
    assert attr(0x21, "copies", b"\x00\x00\x00\x01") in out


def test_inserts_user_name_when_missing():
    ops = b"\x01" + attr(0x47, "attributes-charset", "utf-8")
    out = ipp.set_attribute(build(ops), "requesting-user-name", "s21342", 0x42)
    assert attr(0x42, "requesting-user-name", "s21342") in out
    assert out.index(b"requesting-user-name") < out.index(b"copies")   # in the operation group
    assert out.endswith(b"\x03" + DOC)


def test_walks_past_collections_and_replaces_only_top_level_names():
    coll = (bytes([0x34]) + b"\x00\x0bmedia-col-x" + b"\x00\x00"
            + bytes([0x4A]) + b"\x00\x00" + struct.pack(">H", 12) + b"printer-name"
            + attr(0x42, "", "inner") + bytes([0x37]) + b"\x00\x00\x00\x00")
    resp = b"\x01\x01\x00\x00\x00\x00\x00\x07" + b"\x04" + coll \
        + attr(0x42, "printer-name", "School_Print_backend") + attr(0x41, "printer-info", "old") + b"\x03"
    out = ipp.set_attribute(resp, "printer-name", "Print as s21342", 0x42)
    out = ipp.set_attribute(out, "printer-info", "Print as s21342", 0x41)
    assert attr(0x42, "printer-name", "Print as s21342") in out
    assert attr(0x41, "printer-info", "Print as s21342") in out
    assert b"old" not in out and b"School_Print_backend" not in out
    assert b"inner" in out                              # collection member untouched


def test_operation_id_and_malformed_input():
    assert ipp.operation_id(build()) == 0x0002
    assert ipp.operation_id(b"\x01") is None
    assert ipp.set_attribute(b"\x01\x01\x00\x02", "requesting-user-name", "x", 0x42) == b"\x01\x01\x00\x02"
    truncated = HEADER + b"\x01" + b"\x42\x00\x20abc"
    assert ipp.set_attribute(truncated, "requesting-user-name", "x", 0x42) == truncated


def test_response_rewrite_changes_the_printer_group_not_the_operation_group():
    resp = (b"\x01\x01\x00\x00\x00\x00\x00\x07" + b"\x01" + attr(0x47, "attributes-charset", "utf-8")
            + b"\x04" + attr(0x42, "printer-name", "backend") + b"\x03")
    out = ipp.set_attribute(resp, "printer-name", "Print as s21342", 0x42, insert=False)
    assert out.count(b"printer-name") == 1
    assert b"backend" not in out and b"Print as s21342" in out
    assert ipp.set_attribute(resp, "printer-info", "x", 0x41, insert=False) == resp   # absent: unchanged
