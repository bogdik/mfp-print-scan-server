"""Finished scans on disk: the file itself plus a thumbnail and a small JSON
sidecar (dimensions, dpi, mode) in a hidden subfolder."""

import io
import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..printing.layout import PDFIUM_LOCK

FORMATS = {
    "jpeg": ("jpg", "JPEG"),
    "png": ("png", "PNG"),
    "tiff": ("tif", "TIFF"),
    "pdf": ("pdf", "PDF"),
}
THUMB_SIZE = 320
NAME_RE = re.compile(r"^scan_[\w.-]+\.(jpg|png|tif|pdf)$")


@dataclass
class ScanRecord:
    name: str
    format: str
    size: int  # bytes
    created: str  # ISO timestamp
    pages: int = 1
    width_px: int | None = None
    height_px: int | None = None
    dpi: int | None = None
    mode: str | None = None


class ScanStore:
    def __init__(self, directory: Path):
        self.dir = directory
        self.meta_dir = directory / ".meta"
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _unique_name(self, ext: str, suffix: str = "") -> str:
        base = "scan_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + suffix
        name, n = f"{base}.{ext}", 2
        while (self.dir / name).exists():
            name, n = f"{base}_{n}.{ext}", n + 1
        return name

    def path(self, name: str) -> Path:
        """Resolves a scan name from a request; anything that isn't one of
        our files (e.g. "../main.py") is rejected."""
        if not NAME_RE.match(name) or not (self.dir / name).is_file():
            raise FileNotFoundError(name)
        return self.dir / name

    def thumb_path(self, name: str) -> Path:
        self.path(name)
        return self.meta_dir / f"{name}.jpg"

    def save(self, image, fmt: str, mode: str, quality: int = 90) -> ScanRecord:
        ext, pil_format = FORMATS[fmt]
        dpi = image.info.get("dpi", (300, 300))
        with self._lock:
            name = self._unique_name(ext)
            path = self.dir / name
            path.touch()  # reserve the name before releasing the lock
        img = image
        kwargs: dict = {"dpi": dpi}
        if fmt == "jpeg":
            img = image.convert("L" if image.mode in ("1", "L") else "RGB")
            kwargs.update(quality=quality, optimize=True)
        elif fmt == "tiff":
            kwargs["compression"] = "group4" if image.mode == "1" else "tiff_lzw"
        elif fmt == "pdf":
            kwargs = {"resolution": float(dpi[0])}
            if image.mode == "RGB":
                kwargs["quality"] = quality
        img.save(path, pil_format, **kwargs)

        record = ScanRecord(
            name=name, format=fmt, size=path.stat().st_size, created=datetime.now().isoformat(timespec="seconds"),
            width_px=image.width, height_px=image.height, dpi=int(dpi[0]), mode=mode,
        )
        self._write_meta(record, image)
        return record

    def _write_meta(self, record: ScanRecord, image) -> None:
        thumb = image.convert("RGB")
        thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
        thumb.save(self.meta_dir / f"{record.name}.jpg", "JPEG", quality=80)
        (self.meta_dir / f"{record.name}.json").write_text(json.dumps(asdict(record), ensure_ascii=False))

    def list_scans(self) -> list[ScanRecord]:
        records = []
        for meta in self.meta_dir.glob("*.json"):
            name = meta.name[:-5]
            path = self.dir / name
            if not path.is_file():
                continue
            try:
                data = json.loads(meta.read_text())
                data["size"] = path.stat().st_size
                records.append(ScanRecord(**data))
            except (ValueError, TypeError):
                continue
        return sorted(records, key=lambda r: r.created, reverse=True)

    def delete(self, name: str) -> None:
        path = self.path(name)
        path.unlink()
        for extra in (self.meta_dir / f"{name}.jpg", self.meta_dir / f"{name}.json"):
            extra.unlink(missing_ok=True)

    def merge_pdf(self, names: list[str]) -> ScanRecord:
        """Joins scans (images and PDFs, in the given order) into one PDF."""
        import pypdfium2 as pdfium
        from PIL import Image

        paths = [self.path(n) for n in names]
        with PDFIUM_LOCK:
            merged = pdfium.PdfDocument.new()
            for path in paths:
                if path.suffix == ".pdf":
                    src = pdfium.PdfDocument(str(path))
                else:
                    with Image.open(path) as img:
                        dpi = img.info.get("dpi", (300, 300))
                        buf = io.BytesIO()
                        img.convert("L" if img.mode in ("1", "L") else "RGB").save(buf, "PDF", resolution=float(dpi[0]))
                    src = pdfium.PdfDocument(buf.getvalue())
                merged.import_pages(src)
                src.close()

            with self._lock:
                name = self._unique_name("pdf", "_merged")
                (self.dir / name).touch()
            merged.save(self.dir / name)
            pages = len(merged)
            first_page = merged[0].render(scale=THUMB_SIZE / max(merged[0].get_size())).to_pil()
            merged.close()

        path = self.dir / name
        record = ScanRecord(
            name=name, format="pdf", size=path.stat().st_size, created=datetime.now().isoformat(timespec="seconds"),
            pages=pages,
        )
        self._write_meta(record, first_page)
        return record
