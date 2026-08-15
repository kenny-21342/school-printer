"""PDF inspection and thumbnail rendering via poppler-utils."""
from __future__ import annotations

import base64
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PdfMeta:
    pages: int
    page_size: str | None  # e.g. "595 x 842 pts" -> we parse to "A4" later if needed


def is_pdf(path: str | Path) -> bool:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return True
    # sniff magic
    try:
        with open(p, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def pdf_info(path: str | Path) -> PdfMeta:
    """Use pdfinfo to get page count and page size."""
    binary = shutil.which("pdfinfo")
    if not binary:
        return PdfMeta(pages=0, page_size=None)
    try:
        proc = subprocess.run(
            [binary, str(path)],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return PdfMeta(pages=0, page_size=None)
        pages = 0
        size = None
        for line in proc.stdout.splitlines():
            if line.startswith("Pages:"):
                try: pages = int(line.split(":", 1)[1].strip())
                except ValueError: pass
            elif line.startswith("Page size:"):
                size = line.split(":", 1)[1].strip()
        return PdfMeta(pages=pages, page_size=size)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return PdfMeta(pages=0, page_size=None)


def render_page_thumbnail(
    path: str | Path, page: int = 1, width_px: int = 300
) -> tuple[bytes, int, int] | None:
    """Render a single page as JPEG bytes. Returns (jpeg_bytes, w, h) or None.

    Uses pdftoppm with -f/-l for a single page.
    """
    binary = shutil.which("pdftoppm")
    if not binary:
        return None
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        out_prefix = str(Path(tmpdir) / "page")
        try:
            proc = subprocess.run(
                [binary, "-f", str(page), "-l", str(page),
                 "-jpeg", "-r", "72", "-scale-to", str(width_px),
                 str(path), out_prefix],
                capture_output=True, timeout=30,
            )
            if proc.returncode != 0:
                return None
            # output file: out_prefix-NN.jpg (NN zero-padded)
            candidates = list(Path(tmpdir).glob("page-*.jpg"))
            if not candidates:
                return None
            data = candidates[0].read_bytes()
            # quick dimension probe from JPEG SOF0
            w, h = _jpeg_dims(data)
            return (data, w, h)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None


def _jpeg_dims(data: bytes) -> tuple[int, int]:
    """Parse JPEG SOF0 to get width/height. Falls back to (0,0)."""
    try:
        i = 0
        while i < len(data) - 1:
            if data[i] == 0xFF:
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h = (data[i + 5] << 8) | data[i + 6]
                    w = (data[i + 7] << 8) | data[i + 8]
                    return (w, h)
                elif 0xD0 <= marker <= 0xD9 or marker == 0x01:
                    i += 2
                    continue
                elif marker == 0xD8 or marker == 0xD9:
                    i += 2
                    continue
                else:
                    length = (data[i + 2] << 8) | data[i + 3]
                    i += 2 + length
                    continue
            i += 1
    except (IndexError, ValueError):
        pass
    return (0, 0)


# ---- MIME type / Office detection ----

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


def needs_conversion(mime: str) -> bool:
    return mime in OFFICE_MIMES


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
