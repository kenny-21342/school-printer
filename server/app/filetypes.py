"""Upload MIME-type classification: what prints directly, what must be converted.

This was pdftools.py, which also held pdfinfo/pdftoppm wrappers for the
/pdf-info and /preview endpoints. Those are gone: the iOS app renders its page
previews on-device with PDFKit and never called them, so they were an upload
endpoint nobody used — and with them went the server's only dependency on
poppler.
"""
from __future__ import annotations

# Everything that is not directly printable and has to become a PDF first.
OFFICE_MIMES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.apple.pages",
    "application/vnd.apple.numbers",
    "application/vnd.apple.keynote",
}


# The subset the converter can actually handle: Microsoft Office, driven through
# AppleScript on the iMac (see office.py). The iWork formats above are detected
# so the upload can be refused with a useful message -- Pages, Numbers and
# Keynote are not scripted here, and a student can export a PDF from the app
# itself in two clicks.
CONVERTIBLE_MIMES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def needs_conversion(mime: str) -> bool:
    return mime in OFFICE_MIMES


def can_convert(mime: str) -> bool:
    """True when office.convert_to_pdf() knows how to handle this type."""
    return mime in CONVERTIBLE_MIMES


def office_extension_for_mime(mime: str) -> str:
    mapping = {
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    }
    return mapping.get(mime, ".bin")
