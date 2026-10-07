"""A per-student iOS Shortcut that sends the shared file to /print.

The Shortcut is for iPads that will not install the AirPrint profile. It shows
up in the Share Sheet, uploads the shared file with the student's key in
`X-Print-Key`, and shows the job status the relay returns. The key and server
address are baked in, so adding it is one tap and there is nothing to paste.

iOS only imports signed Shortcuts. `shortcuts sign` ships with macOS, so the
relay signs on the iMac; if that is unavailable the route says so and the
profile remains the way in.
"""
from __future__ import annotations

import plistlib
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .config import settings

_signed: dict[tuple[str, str], bytes] = {}      # (token, url) -> signed file; builds are deterministic


class SigningUnavailable(Exception):
    pass


def _text(string: str, attachments: dict | None = None) -> dict:
    return {"Value": {"string": string, "attachmentsByRange": attachments or {}},
            "WFSerializationType": "WFTextTokenString"}


def build_shortcut(token: str, print_url: str) -> bytes:
    """The unsigned Shortcut, as a binary plist."""
    ns = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
    upload, status = (str(uuid.uuid5(ns, f"{token}:{n}")).upper() for n in ("upload", "status"))
    prefix = "Print job: "
    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
         "WFWorkflowActionParameters": {
             "UUID": upload, "WFURL": print_url, "WFHTTPMethod": "POST",
             "WFHTTPBodyType": "Form", "ShowHeaders": True,
             "WFHTTPHeaders": {"Value": {"WFDictionaryFieldValueItems": [
                 {"WFItemType": 0, "WFKey": _text("X-Print-Key"), "WFValue": _text(token)}]},
                 "WFSerializationType": "WFDictionaryFieldValue"},
             "WFFormValues": {"Value": {"WFDictionaryFieldValueItems": [
                 {"WFItemType": 5, "WFKey": _text("file"),
                  # A file field's value is a token attachment wrapped in a
                  # parameter state; one level less and the form goes out with
                  # no file (the relay answers 422).
                  "WFValue": {"Value": {"Value": {"Type": "ExtensionInput"},
                                        "WFSerializationType": "WFTextTokenAttachment"},
                              "WFSerializationType": "WFTokenAttachmentParameterState"}}]},
                 "WFSerializationType": "WFDictionaryFieldValue"}}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
         "WFWorkflowActionParameters": {
             "UUID": status, "WFDictionaryKey": "status", "WFGetDictionaryValueType": "Value",
             # Without this the action reads the Shortcut's own input (the PDF),
             # not the relay's reply, and fails to convert it to a dictionary.
             "WFInput": {"Value": {"Type": "ActionOutput", "OutputUUID": upload,
                                   "OutputName": "Contents of URL"},
                         "WFSerializationType": "WFTextTokenAttachment"}}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
         "WFWorkflowActionParameters": {"Text": _text(prefix + "￼", {
             f"{{{len(prefix)}, 1}}": {"Type": "ActionOutput", "OutputUUID": status,
                                       "OutputName": "Dictionary Value"}})}},
    ]
    return plistlib.dumps({
        "WFWorkflowClientVersion": "900",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["ActionExtension"],          # "Show in Share Sheet"
        "WFWorkflowInputContentItemClasses": [
            "WFPDFContentItem", "WFImageContentItem", "WFGenericFileContentItem",
            "WFRichTextContentItem", "WFSafariWebPageContentItem"],
        "WFWorkflowActions": actions,
    }, fmt=plistlib.FMT_BINARY)


def _binary() -> str:
    found = settings.shortcuts_binary or shutil.which("shortcuts")
    if not found:
        raise SigningUnavailable("the `shortcuts` tool is not installed")
    return found


def stored_path(request_id: int) -> Path:
    return settings.data_dir / "shortcuts" / f"{request_id}.shortcut"


def signed_shortcut(token: str, print_url: str, request_id: int | None = None) -> bytes:
    """The importable (signed) Shortcut for this key and address.

    A file placed in data/shortcuts/<id>.shortcut wins: signing uploads to
    iCloud, which fails from the iMac's network, so tools/school-print signs on
    a machine where it works and stores the result here at approval time.
    """
    if request_id is not None and stored_path(request_id).is_file():
        return stored_path(request_id).read_bytes()
    cache_key = (token, print_url)
    if cache_key in _signed:
        return _signed[cache_key]
    binary = _binary()
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d, "in.shortcut"), Path(d, "out.shortcut")
        src.write_bytes(build_shortcut(token, print_url))
        try:
            proc = subprocess.run([binary, "sign", "--mode", "anyone", "--input", str(src),
                                   "--output", str(dst)], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SigningUnavailable(str(e)) from e
        if proc.returncode != 0 or not dst.exists():
            raise SigningUnavailable((proc.stderr or proc.stdout).strip()[:200] or "signing failed")
        _signed[cache_key] = dst.read_bytes()
    return _signed[cache_key]
