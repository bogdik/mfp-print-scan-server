"""Office documents and plain text → PDF through LibreOffice, when it's
installed (Windows or Linux).

The PDF then goes the same way as an uploaded PDF: exact preview and
printing by the server with per-job settings. Without LibreOffice the
backends fall back to their old path (Windows: the program registered to
print the file type; Linux: `lp` with the original file).

Conversions are cached by file content, so printing right after a preview
doesn't convert the same document twice."""

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from ..config import settings
from .layout import IMAGE_EXTENSIONS, PDFIUM_LOCK

logger = logging.getLogger(__name__)

CONVERTIBLE_EXTENSIONS = {
    ".txt", ".rtf", ".doc", ".docx", ".odt",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
}
CONVERT_TIMEOUT = 180  # seconds; a big presentation can take a while
CACHE_MAX_FILES = 30

_WINDOWS_CANDIDATES = [
    Path(os.environ.get(var, "")) / "LibreOffice" / "program" / "soffice.exe"
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
    if os.environ.get(var)
]

# One conversion at a time: soffice instances sharing a profile don't mix.
_lock = threading.Lock()


def find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return next((str(p) for p in _WINDOWS_CANDIDATES if p.is_file()), None)


def can_convert(path: Path) -> bool:
    return path.suffix.lower() in CONVERTIBLE_EXTENSIONS and find_soffice() is not None


def _cache_dir() -> Path:
    d = settings.data_dir / "pdf-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trim_cache(directory: Path) -> None:
    pdfs = sorted(directory.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in pdfs[CACHE_MAX_FILES:]:
        old.unlink(missing_ok=True)


def _utf8_text(path: Path, workdir: Path) -> Path:
    """LibreOffice reads a .txt as UTF-8 only if it's marked as such; files in
    another encoding (e.g. Windows-1251 from Notepad) would come out as
    mojibake. Re-encode to UTF-8 with a BOM."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    fixed = workdir / "document.txt"
    fixed.write_text(text, encoding="utf-8-sig")
    return fixed


def to_pdf(path: Path) -> Path | None:
    """PDF version of `path`, or None if it can't be converted (no
    LibreOffice, unsupported type, conversion failed — the caller falls back)."""
    soffice = find_soffice()
    if soffice is None or path.suffix.lower() not in CONVERTIBLE_EXTENSIONS:
        return None

    cache = _cache_dir()
    digest = hashlib.sha256(path.read_bytes() + path.suffix.lower().encode()).hexdigest()[:24]
    result = cache / f"{digest}.pdf"
    if result.exists():
        result.touch()  # keep recently used entries when trimming
        return result

    with _lock:
        if result.exists():
            return result
        work = cache / f"work-{digest}"
        work.mkdir(exist_ok=True)
        try:
            source = work / f"document{path.suffix.lower()}"
            if path.suffix.lower() == ".txt":
                source = _utf8_text(path, work)
            else:
                shutil.copyfile(path, source)
            # A private profile: doesn't clash with a LibreOffice the user has
            # open, and works for the service account too.
            profile = (settings.data_dir / "libreoffice-profile").resolve().as_uri()
            proc = subprocess.run(
                [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                 "--convert-to", "pdf", "--outdir", str(work), str(source)],
                capture_output=True, timeout=CONVERT_TIMEOUT,
            )
            produced = work / f"{source.stem}.pdf"
            if proc.returncode != 0 or not produced.exists():
                logger.warning("LibreOffice couldn't convert %s: %s", path.name,
                               proc.stderr.decode(errors="replace").strip() or proc.returncode)
                return None
            produced.replace(result)
            _trim_cache(cache)
            return result
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("LibreOffice conversion of %s failed: %s", path.name, exc)
            return None
        finally:
            shutil.rmtree(work, ignore_errors=True)


def page_count(path: Path) -> int | None:
    """Best-effort sheet count for job history / usage stats. Goes through
    the same to_pdf() (and its cache) that printing itself uses, so calling
    this before print_file() doesn't convert the document twice. None means
    "couldn't tell" (unknown format, no LibreOffice, conversion failed) —
    the caller should treat that as "not counted", not zero."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return 1
    pdf = path if suffix == ".pdf" else to_pdf(path)
    if pdf is None:
        return None
    try:
        import pypdfium2 as pdfium

        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(pdf))
            try:
                return len(doc)
            finally:
                doc.close()
    except Exception:
        logger.warning("Couldn't count pages in %s", path.name, exc_info=True)
        return None
