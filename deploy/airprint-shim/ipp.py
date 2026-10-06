"""Just enough IPP wire parsing for the shim to rewrite one attribute.

An IPP message is an 8-byte header, then attribute groups, then an
end-of-attributes tag (0x03), then (for Print-Job and Send-Document) the raw
document. Delimiter tags (0x00-0x0F) are one byte; every other tag introduces
`name-length(2) name value-length(2) value`. An attribute with a zero-length
name is an additional value of the one before it, which is also how members of
a collection appear, so matching only non-empty names stays at the top level.

Anything that does not parse is returned unchanged: the shim is a proxy, not a
validator, and ippeveprinter will reject a bad request itself.
"""
from __future__ import annotations

import struct

OPERATION_GROUP = 0x01
END_OF_ATTRIBUTES = 0x03


def operation_id(data: bytes) -> int | None:
    if len(data) < 8:
        return None
    return struct.unpack(">H", data[2:4])[0]


def _attribute(tag: int, name: str, value: str) -> bytes:
    n, v = name.encode(), value.encode()
    return bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(v)) + v


def set_attribute(data: bytes, name: str, value: str, tag: int, insert: bool = True) -> bytes:
    """Set top-level attribute `name` to a single `value` of syntax `tag`.

    Replaces the attribute wherever it occurs. If it is absent and `insert` is
    true, adds it at the end of the operation-attributes group. A response must
    pass insert=False: its operation group does not hold the attribute, and
    inserting there would leave the real one (in the printer group) untouched.
    """
    try:
        return _set_attribute(data, name, value, tag, insert)
    except (struct.error, IndexError, ValueError):
        return data


def _set_attribute(data: bytes, name: str, value: str, tag: int, insert: bool) -> bytes:
    if len(data) < 9:
        return data
    target = name.encode()
    new = _attribute(tag, name, value)
    out = bytearray(data[:8])
    pos, group, replaced, dropping = 8, None, False, False

    def leave_operation_group() -> None:
        nonlocal replaced
        if insert and group == OPERATION_GROUP and not replaced:
            out.extend(new)
            replaced = True

    while True:
        t = data[pos]
        if t <= 0x0F:                                # delimiter
            leave_operation_group()
            out.append(t)
            pos += 1
            dropping = False
            if t == END_OF_ATTRIBUTES:
                out.extend(data[pos:])               # the document, untouched
                return bytes(out)
            group = t
            continue
        nlen = struct.unpack(">H", data[pos + 1:pos + 3])[0]
        nstart = pos + 3
        vlen_at = nstart + nlen
        vlen = struct.unpack(">H", data[vlen_at:vlen_at + 2])[0]
        end = vlen_at + 2 + vlen
        if end > len(data):
            raise ValueError("truncated attribute")
        if nlen:
            dropping = False
            if data[nstart:nstart + nlen] == target and not replaced:
                out.extend(new)
                replaced = dropping = True
                pos = end
                continue
        elif dropping:                               # extra values of the replaced attribute
            pos = end
            continue
        out.extend(data[pos:end])
        pos = end
