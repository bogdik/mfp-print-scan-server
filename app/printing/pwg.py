"""PWG Raster (PWG 5102.4) decoder: the page-image format IPP Everywhere
clients (incl. the Windows IPP Class Driver) send when they render pages
themselves. Supports the types we advertise: sgray_8 and srgb_8."""

import struct
from dataclasses import dataclass

from ..i18n import t

HEADER_SIZE = 1796
SYNC = b"RaS2"

COLORSPACE_SGRAY = 18
COLORSPACE_SRGB = 19


@dataclass
class RasterPage:
    width: int
    height: int
    dpi: tuple[int, int]
    mode: str  # PIL mode: "L" or "RGB"
    data: bytes

    def to_image(self):
        from PIL import Image

        return Image.frombytes(self.mode, (self.width, self.height), self.data)


def iter_pages(data: bytes):
    if data[:4] != SYNC:
        raise ValueError(t("err.not_pwg"))
    pos = 4
    while pos + HEADER_SIZE <= len(data):
        header = data[pos:pos + HEADER_SIZE]
        pos += HEADER_SIZE
        dpi = struct.unpack(">II", header[276:284])
        width, height = struct.unpack(">II", header[372:380])
        bits_per_color, bits_per_pixel, bytes_per_line = struct.unpack(">III", header[384:396])
        color_space = struct.unpack(">I", header[400:404])[0]

        if bits_per_color != 8 or color_space not in (COLORSPACE_SGRAY, COLORSPACE_SRGB):
            raise ValueError(t("err.pwg_unsupported", bits=bits_per_color, space=color_space))
        bpp = bits_per_pixel // 8
        mode = "L" if color_space == COLORSPACE_SGRAY else "RGB"

        pixels, pos = _decode_page(data, pos, height, bytes_per_line, bpp)
        yield RasterPage(width=width, height=height, dpi=dpi, mode=mode, data=pixels)


def _decode_page(data: bytes, pos: int, height: int, bytes_per_line: int, bpp: int) -> tuple[bytes, int]:
    """PackBits-like compression: each line group starts with a repeat count
    (line is used count+1 times), then runs: 0..127 = next pixel repeated
    n+1 times, 129..255 = 257-n literal pixels, 128 = rest of line white."""
    white = b"\xff" * bytes_per_line
    out = bytearray()
    y = 0
    while y < height:
        line_repeat = data[pos] + 1
        pos += 1
        line = bytearray()
        while len(line) < bytes_per_line:
            n = data[pos]
            pos += 1
            if n == 128:
                line += white[len(line):]
            elif n < 128:
                line += data[pos:pos + bpp] * (n + 1)
                pos += bpp
            else:
                count = (257 - n) * bpp
                line += data[pos:pos + count]
                pos += count
        line = bytes(line[:bytes_per_line])
        repeat = min(line_repeat, height - y)
        out += line * repeat
        y += repeat
    return bytes(out), pos
