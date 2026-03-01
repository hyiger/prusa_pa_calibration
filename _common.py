"""
_common.py — Shared infrastructure for Prusa calibration G-code generators.

Provides printer/filament presets, G-code utilities, base configuration,
base generator class, and CLI helpers shared by pa_calibration.py and temperature_tower.py.
"""

import argparse
import base64
import io
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

# ── Rounding precision ─────────────────────────────────────────────────────────
_PA = 4   # decimal places for LA/PA (K) values
_Z  = 3   # decimal places for Z coordinates
_XY = 4   # decimal places for X/Y coordinates
_E  = 5   # decimal places for extrusion amounts

# ── Prusa Core One bed size (fallback defaults) ────────────────────────────────
BED_X = 250.0
BED_Y = 220.0
MAX_Z = 270.0

# ── Filament presets ───────────────────────────────────────────────────────────
# Fields: hotend_temp, bed_temp, fan_speed (%), first_layer_fan (%), retract_dist (mm)
FILAMENT_PRESETS: dict[str, dict] = {
    "PLA":  dict(hotend_temp=215, bed_temp=60,  fan_speed=100, first_layer_fan=0, retract_dist=0.6),
    "PETG": dict(hotend_temp=235, bed_temp=85,  fan_speed=50,  first_layer_fan=0, retract_dist=0.8),
    "ABS":  dict(hotend_temp=245, bed_temp=100, fan_speed=0,   first_layer_fan=0, retract_dist=1.0),
    "ASA":  dict(hotend_temp=255, bed_temp=100, fan_speed=20,  first_layer_fan=0, retract_dist=1.0),
    "PA":   dict(hotend_temp=260, bed_temp=90,  fan_speed=0,   first_layer_fan=0, retract_dist=1.0),
    "TPU":  dict(hotend_temp=230, bed_temp=60,  fan_speed=50,  first_layer_fan=0, retract_dist=0.0),
    "PC":   dict(hotend_temp=275, bed_temp=110, fan_speed=0,   first_layer_fan=0, retract_dist=1.0),
}

# ── Printer presets ────────────────────────────────────────────────────────────
# Fields: bed_x (mm), bed_y (mm), max_z (mm), model (M862.3 string)
PRINTER_PRESETS: dict[str, dict] = {
    "MINI":     dict(bed_x=180.0, bed_y=180.0, max_z=180.0, model="MINI"),
    "MK4S":     dict(bed_x=250.0, bed_y=210.0, max_z=220.0, model="MK4S"),
    "COREONE":  dict(bed_x=250.0, bed_y=220.0, max_z=270.0, model="COREONE"),
    "COREONEL": dict(bed_x=300.0, bed_y=300.0, max_z=330.0, model="COREONEL"),
    "XL":       dict(bed_x=360.0, bed_y=360.0, max_z=360.0, model="XL"),
}
_DEFAULT_PRINTER = "COREONE"

# ── Built-in G-code directory ──────────────────────────────────────────────────
# Default start/end templates live in gcode/{printer}_{start|end}.gcode.
# coreone_start.gcode / coreone_end.gcode are used as the fallback default.
_GCODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcode")


def _builtin_gcode(name: str) -> str:
    """Load a built-in G-code template from the gcode/ directory.

    Exits with an error message if the file cannot be read.
    """
    path = os.path.join(_GCODE_DIR, name)
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        print(f"ERROR: cannot read built-in G-code template {path}: {e}", file=sys.stderr)
        sys.exit(1)


# ── Utility functions ──────────────────────────────────────────────────────────

def _r(v: float, places: int) -> float:
    """Round v to the given number of decimal places."""
    f = 10 ** places
    return round(v * f) / f


def _render(template: str, vars: dict) -> str:
    """
    Substitute {simple_var} markers in template using vars dict.
    Only replaces markers that are a single lowercase identifier (a-z, 0-9, _),
    so PrusaSlicer {if ...} expressions and other constructs are left intact.
    Unknown markers are also left unchanged.
    """
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(vars[key]) if key in vars else m.group(0)
    return re.sub(r"\{([a-z][a-z0-9_]*)\}", sub, template)


# ── Thumbnail generation ───────────────────────────────────────────────────────

_THUMB_BG = (30, 30, 30)    # dark background (near-black)
_THUMB_FG = (250, 104, 49)  # Prusa orange (#FA6831)


def _make_png(width: int, height: int, pixels: list) -> bytes:
    """Build a minimal PNG file from a flat list of (r, g, b) tuples (row-major).

    Uses only struct and zlib from the Python stdlib — no PIL required.
    """
    import struct
    import zlib as _zlib

    def _crc32(data: bytes) -> int:
        return _zlib.crc32(data) & 0xFFFF_FFFF

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data))
                + tag + data
                + struct.pack(">I", _crc32(tag + data)))

    # IHDR: width, height, bit_depth=8, color_type=2 (RGB),
    #       compression_method=0, filter_method=0, interlace_method=0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    # Each scanline: filter byte 0 (None) followed by RGB triples
    raw = bytearray()
    for y in range(height):
        raw.append(0)   # filter type = None
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw += bytes([r & 0xFF, g & 0xFF, b & 0xFF])

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", _zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


class _Raster:
    """Minimal pixel canvas used to draw bgcode thumbnails."""

    def __init__(self, w: int, h: int,
                 bg=_THUMB_BG, fg=_THUMB_FG):
        self.w  = w
        self.h  = h
        self._fg = fg
        self._px: list = [bg] * (w * h)

    def _set(self, x: int, y: int, c=None) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self._px[y * self.w + x] = c if c is not None else self._fg

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, c=None) -> None:
        col = c if c is not None else self._fg
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self._px[y * self.w + x] = col

    def line(self, x0: int, y0: int, x1: int, y1: int,
             thick: int = 1, c=None) -> None:
        """Draw a line using Bresenham's algorithm with integer thickness."""
        col = c if c is not None else self._fg
        dx  = abs(x1 - x0)
        dy  = abs(y1 - y0)
        sx  = 1 if x0 < x1 else -1
        sy  = 1 if y0 < y1 else -1
        err = dx - dy
        x, y = x0, y0
        ht   = thick // 2
        while True:
            for tx in range(-ht, ht + 1):
                for ty in range(-ht, ht + 1):
                    self._set(x + tx, y + ty, col)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x   += sx
            if e2 < dx:
                err += dx
                y   += sy

    def to_png(self) -> bytes:
        return _make_png(self.w, self.h, self._px)


def _thumbnail_pa(w: int, h: int) -> bytes:
    """Thumbnail for PA calibration: rows of V-chevron shapes on dark background."""
    r   = _Raster(w, h)
    n   = max(2, min(5, h // 20))
    mx  = max(1, w // 10)
    my  = max(1, h // 10)
    thick = max(1, min(w, h) // 22)
    band  = (h - 2 * my) / n
    for i in range(n):
        cy   = my + (i + 0.5) * band
        arm  = max(1, int(band * 0.38))
        r.line(mx,      int(cy),       w - mx, int(cy - arm), thick=thick)
        r.line(mx,      int(cy),       w - mx, int(cy + arm), thick=thick)
    return r.to_png()


# Pre-rendered temperature-tower thumbnails (Prusa-orange silhouette traced from
# an actual PrusaSlicer preview, recoloured to match our dark-bg + orange scheme).
# Generated once with Pillow; embedded here so runtime stays pure stdlib.
_TOWER_PNG_220x124 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAANwAAAB8CAIAAACNJEk4AAADVUlEQVR42u3dQVKsMBiF0UixA+c9"
    "cjVvwa7mbcE9OOiZpUBDEm7gfCMnlqWeCpAf6LfH41GkpGZ/AjXt/7/35xcfn18bv+XNSqnq/v5q"
    "o0soVR/fQZdQqgm+Iy6hVFuCO1xCCd85LbiEEr44l1DyF+cSSvjiXELJX5xLEx344g7fUPIXd/UN"
    "JX9BHKGEL44jlPyF5uobvrjF8tYo+ct0eReU/A3k8moo4buAy4FR8ndVlwOghO9uLrNQ8sdlOWuf"
    "Ej4tHL5n/pR29T3Dp7TN85k/RQ2+11Hyp/5NtXRLtRa4yR9LaS6niqcCUhWXU91TVOm4y6n6pZN0"
    "0OXU4pJeOuJyarTVJO12ueeGDJuXKoH3U3KpxGd0uFSjc7+JSEWJLGbfKiM+982lOq+Xxowq482+"
    "uVTi7Fsy+5bZN5cy+5YqjxltEinxrWtcqt0+pdm3smaMxXPfKsaMslgWm+cqo71Pz+xbY76MgEvF"
    "3ZDBpUJvyOBSWTdkcKluLn0MnsoVxox2LpU4ZuRS7U78zL6VNfh+GSWI6uDS7FsDzr5tA6mkzb65"
    "VOiL+LlU3OxbMvuW2TeXMmYUi8aMutDmOZfqOWN09a0y5CO2FkV1XiytlCo5A8bXziktlor7yBI7"
    "lOqWw7eGvSHDYqnQGzK4VOLhm0ud/+FOXGqM+ym51EYnP6hscWn2rR6H6R9alhe1GUR1OGv8+Pza"
    "jmdmUf1n3E9Rf32LMaNO4Lj8jS+cU3KpKte7qy6NGVXnsnof5V9XOu8S0jlbgQvr5ctbQlzi2Nql"
    "MaMaHqn3AfU0o3ovQAt4nj/RREcNOa4i+fUHeRG/6nDc56/4FFvVcrNs4DlU3O3bI7YIHvVXTnnu"
    "m8ubEOzvz5iRvzh/PnGMvzH2no0Z+StDvkuIS/6iUXLJXyJKLvkLutBhkb8zUW7ZsoePv4aH7yO7"
    "VvfRyV/vc8qXnsy9vM7VX3/LL8tfhQudLU+aXU+n9W8YlLX+0GlAjy+B/PXeEmrh8hSa1r9L7VM2"
    "ctkOKH+3+MiS1i5366ziD8FRJzp9XC4Ysv5Buf7/PvcfzF8x0TlxZsOflfKx3Ufr1yQgqE13CdVy"
    "6fpDDZ9mdAmiiPspF1xaAhXx4NiRfRyp1SO2/CnrcQipXd9IvP3n1++aOwAAAABJRU5ErkJggg==")

_TOWER_PNG_16x16 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAASUlEQVR42mOUk5NjIA7c8RJW2faW"
    "haAiNBEWYhRh0YBfETJgIkk1VANJYFQDfqCy7S0JGiCqsScNTEXIgBGeWiHxjVURdg1EAgDLXBFt"
    "toQHLwAAAABJRU5ErkJggg==")

_TOWER_PNGS = {(220, 124): _TOWER_PNG_220x124, (16, 16): _TOWER_PNG_16x16}


def _thumbnail_tower(w: int, h: int) -> bytes:
    """Return a pre-rendered thumbnail for the temperature tower.

    Uses orange-on-dark silhouettes traced from an actual PrusaSlicer preview.
    Falls back to a simple drawn approximation for non-standard sizes.
    """
    if (w, h) in _TOWER_PNGS:
        return _TOWER_PNGS[(w, h)]
    # Fallback for non-standard sizes: horizontal bridge-slab pattern
    r = _Raster(w, h)
    n = max(2, min(7, h // 16))
    top_m  = max(1, h // 10)
    seg_h  = max(3, (h - top_m) // n)
    slab_h = max(1, seg_h // 3)
    slab_w = max(6, w * 4 // 5)
    wall_w = max(1, slab_w // 5)
    slab_x = (w - slab_w) // 2
    for i in range(n):
        y0 = top_m + i * seg_h
        r.fill_rect(slab_x,                  y0,          slab_x + slab_w, y0 + slab_h)
        r.fill_rect(slab_x,                  y0 + slab_h, slab_x + wall_w, y0 + seg_h)
        r.fill_rect(slab_x + slab_w - wall_w, y0 + slab_h, slab_x + slab_w, y0 + seg_h)
    return r.to_png()


def _thumbnails_to_gcode_comments(thumbnails) -> str:
    """Encode thumbnails as PrusaSlicer-style thumbnail comment blocks.

    Returns a string (possibly empty) to prepend to the ASCII G-code.
    Format (understood by PrusaSlicer, OrcaSlicer, Bambu Studio):

        ; thumbnail begin WxH <base64_char_count>
        ; <base64 data, up to 78 chars per line>
        ; thumbnail end

    The block is repeated for each (width, height, png_bytes) tuple.
    """
    import base64
    out: list[str] = []
    for w, h, png in thumbnails:
        b64 = base64.b64encode(png).decode()
        out.append(f"; thumbnail begin {w}x{h} {len(b64)}")
        for i in range(0, len(b64), 78):
            out.append(f"; {b64[i : i + 78]}")
        out.append("; thumbnail end")
        out.append(";")
    return "\n".join(out) + "\n" if out else ""


def _write_bgcode(gcode_text: str, dest, thumbnails=()) -> int:
    """
    Write gcode_text as a Prusa binary G-code v1 (.bgcode) file.

    dest       — file path (str/Path) or a binary-mode file object
                 (e.g. sys.stdout.buffer).
    thumbnails — optional sequence of (width, height, png_bytes) tuples;
                 emitted as Thumbnail blocks between PrinterMetadata and
                 PrintMetadata (the position PrusaSlicer uses).
    Returns the number of bytes written.

    Format reference: https://github.com/prusa3d/libbgcode
    """
    import struct
    import zlib as _zlib

    MAGIC            = b"GCDE"
    VERSION          = 1
    CKSUM_CRC32      = 1
    BLK_PRINTER_META = 3
    BLK_PRINT_META   = 4
    BLK_GCODE        = 1
    BLK_THUMBNAIL    = 5
    THUMB_FMT_PNG    = 0
    COMP_NONE        = 0
    COMP_DEFLATE     = 1
    ENC_INI          = 0
    ENC_RAW          = 0

    def _crc(data: bytes, prev: int = 0) -> int:
        return _zlib.crc32(data, prev) & 0xFFFFFFFF

    def _deflate(data: bytes) -> bytes:
        # Use zlib-wrapped DEFLATE (wbits=15, header 0x78 0x9C).
        # libbgcode's uncompress() calls inflateInit() which defaults to
        # zlib format — raw DEFLATE (wbits=-15) is rejected.
        obj = _zlib.compressobj(level=6, wbits=15)
        return obj.compress(data) + obj.flush()

    def _block(btype: int, params: bytes, data: bytes, compress: bool = False) -> bytes:
        # Per the libbgcode spec, the block layout is:
        #   header (8 or 12 bytes)
        #   params (block-specific parameters, written UNCOMPRESSED)
        #   payload (compressed or raw data, NOT including params)
        #   checksum (CRC32 of header + params + payload)
        # uncompressed_size / compressed_size refer to the payload only.
        if compress:
            payload = _deflate(data)
            hdr     = struct.pack("<HHII", btype, COMP_DEFLATE, len(data), len(payload))
        else:
            payload = data
            hdr     = struct.pack("<HHI",  btype, COMP_NONE,    len(data))
        cksum = _crc(hdr)
        cksum = _crc(params, cksum)
        cksum = _crc(payload, cksum)
        return hdr + params + payload + struct.pack("<I", cksum)

    def _meta_block(btype: int, fields: Optional[dict] = None) -> bytes:
        ini    = "".join(f"{k}={v}\n" for k, v in (fields or {}).items())
        params = struct.pack("<H", ENC_INI)
        data   = ini.encode("utf-8")
        return _block(btype, params, data)

    def _thumb_block(tw: int, th: int, png_bytes: bytes) -> bytes:
        # Thumbnail params: format(uint16) + width(uint16) + height(uint16) = 6 bytes
        # (verified against PrusaSlicer reference file — other block types use 2 bytes)
        params = struct.pack("<HHH", THUMB_FMT_PNG, tw, th)
        return _block(BLK_THUMBNAIL, params, png_bytes, compress=False)

    BLK_FILE_META    = 0
    BLK_SLICER_META  = 2

    # Required block sequence (verified against PrusaSlicer 2.9.4 output):
    #   FileMetadata → PrinterMetadata → [Thumbnails] → PrintMetadata → SlicerMetadata → GCode+
    # GCode MUST be last; libbgcode convert() seeks back past GCode to find
    # PrintMetadata and SlicerMetadata, so they must precede the GCode blocks.
    file_hdr   = MAGIC + struct.pack("<IH", VERSION, CKSUM_CRC32)
    # Use COMP_NONE (compress=False) for the GCode block.
    # Prusa firmware's PrusaPackGcodeReader::init_decompression() only handles
    # ECompressionType::None, Heatshrink_11_4, and Heatshrink_12_4 — it returns
    # false (→ "The file is corrupt") for ECompressionType::Deflate (comp=1).
    # libbgcode's is_valid_binary_gcode() never decompresses GCode (uses skip_block),
    # so DEFLATE passes format validation but fails at print time.
    gcode_blk  = _block(BLK_GCODE,
                        params=struct.pack("<H", ENC_RAW),
                        data=gcode_text.encode("utf-8"),
                        compress=False)
    thumb_data = b"".join(_thumb_block(tw, th, png) for tw, th, png in thumbnails)
    content = (
        file_hdr
        + _meta_block(BLK_FILE_META,    {"Producer": "prusa_pa_calibration"})
        + _meta_block(BLK_PRINTER_META)
        + thumb_data
        + _meta_block(BLK_PRINT_META,   {"generator": "prusa_pa_calibration"})
        + _meta_block(BLK_SLICER_META)
        + gcode_blk
    )

    if isinstance(dest, (str, bytes)):
        with open(dest, "wb") as f:
            f.write(content)
    else:
        dest.write(content)

    return len(content)


def bgcode_to_ascii(data: bytes) -> str:
    """Extract ASCII G-code from Prusa binary G-code (.bgcode) data.

    Parses every block in the file, locates all GCode blocks (type 1),
    decompresses their payloads if needed, and returns the concatenated
    G-code as a plain text string.  CRC32 is validated for every block.

    Supported compression:
        COMP_NONE (0)    — raw payload, no decompression needed
        COMP_DEFLATE (1) — zlib-wrapped DEFLATE (wbits=15)

    Supported encoding:
        ENC_RAW (0) — plain UTF-8 text

    Args:
        data: raw bytes of a .bgcode file (e.g. from open(path, "rb").read()).

    Returns:
        ASCII G-code string (concatenation of all GCode blocks in order).

    Raises:
        ValueError: on bad magic bytes, CRC mismatch, truncated data,
                    no GCode blocks found, or an unsupported compression /
                    encoding type (Heatshrink, MeatPack).
    """
    import struct
    import zlib as _zlib

    MAGIC         = b"GCDE"
    BLK_GCODE     = 1
    BLK_THUMBNAIL = 5   # thumbnail params are 6 bytes; all others are 2
    COMP_NONE     = 0
    COMP_DEFLATE  = 1
    ENC_RAW       = 0   # plain UTF-8; same numeric value as ENC_INI for metadata

    if len(data) < 10:
        raise ValueError(
            f"Data too short ({len(data)} bytes) to be a bgcode file"
        )
    if data[:4] != MAGIC:
        raise ValueError(
            f"Not a bgcode file: expected magic {MAGIC!r}, got {data[:4]!r}"
        )

    # File header: magic(4) + version(uint32) + checksum_type(uint16) = 10 bytes
    pos = 10
    parts: list[str] = []

    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError(f"Truncated block header at offset {pos}")

        btype, comp = struct.unpack_from("<HH", data, pos)
        uncomp_size, = struct.unpack_from("<I", data, pos + 4)

        if comp == COMP_NONE:
            comp_size = uncomp_size
            hdr_len = 8
        elif comp in (COMP_DEFLATE, 2, 3):   # DEFLATE or either Heatshrink variant
            if pos + 12 > len(data):
                raise ValueError(f"Truncated block header at offset {pos}")
            comp_size, = struct.unpack_from("<I", data, pos + 8)
            hdr_len = 12
        else:
            raise ValueError(f"Unknown compression type {comp} at offset {pos}")

        # Thumbnail blocks have 6-byte params; every other block type has 2.
        params_len   = 6 if btype == BLK_THUMBNAIL else 2
        params_start = pos + hdr_len
        payload_start = params_start + params_len
        payload_end   = payload_start + comp_size

        if payload_end + 4 > len(data):
            raise ValueError(
                f"Truncated block payload at offset {pos}: "
                f"need {payload_end + 4 - len(data)} more bytes"
            )

        # CRC32 covers: block header bytes + params bytes + payload bytes
        stored_crc, = struct.unpack_from("<I", data, payload_end)
        computed_crc = _zlib.crc32(data[pos:payload_end]) & 0xFFFFFFFF
        if computed_crc != stored_crc:
            raise ValueError(
                f"CRC32 mismatch in block type {btype} at offset {pos}: "
                f"computed {computed_crc:#010x}, stored {stored_crc:#010x}"
            )

        if btype == BLK_GCODE:
            enc, = struct.unpack_from("<H", data, params_start)
            payload = data[payload_start:payload_end]

            if comp == COMP_NONE:
                raw = payload
            elif comp == COMP_DEFLATE:
                try:
                    raw = _zlib.decompress(payload)
                except _zlib.error as e:
                    raise ValueError(
                        f"DEFLATE decompression failed at offset {pos}: {e}"
                    ) from e
            else:
                raise ValueError(
                    f"Unsupported GCode block compression {comp} at offset {pos} "
                    f"(Heatshrink decoding requires a third-party library)"
                )

            if enc != ENC_RAW:
                raise ValueError(
                    f"Unsupported GCode block encoding {enc} at offset {pos} "
                    f"(MeatPack decoding is not supported)"
                )

            parts.append(raw.decode("utf-8"))

        pos = payload_end + 4   # advance past payload + CRC32

    if not parts:
        raise ValueError("No GCode blocks found in bgcode data")

    return "".join(parts)


def _upload_prusaconnect(
    data: bytes,
    filename: str,
    start_print: bool = False,
) -> None:
    """Upload G-code to PrusaConnect using saved OAuth2 tokens.

    Reads (and auto-refreshes) the token file written by prusa_login.py.
    Upload is a two-step process matching PrusaSlicer's PrusaConnectNew class:
      1. POST /app/users/teams/{team_id}/uploads  — register the upload
      2. PUT  /app/teams/{team_id}/files/raw?upload_id={id}  — send the file

    Both calls use Authorization: Bearer {access_token}.
    """
    import json as _json
    import pathlib
    import urllib.error
    import urllib.request
    import time as _time

    TOKEN_FILE = pathlib.Path.home() / ".config" / "prusa_calibration" / "tokens.json"

    # ── Load tokens ────────────────────────────────────────────────────────
    if not TOKEN_FILE.exists():
        print(
            "ERROR: not logged in to PrusaConnect.\n"
            "       Run: python3 prusa_login.py",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        tokens = _json.loads(TOKEN_FILE.read_text())
    except Exception as e:
        print(f"ERROR: cannot read {TOKEN_FILE}: {e}", file=sys.stderr)
        sys.exit(1)

    def _save(t: dict) -> None:
        TOKEN_FILE.write_text(_json.dumps(t, indent=2))
        TOKEN_FILE.chmod(0o600)

    # ── Auto-refresh if expired ────────────────────────────────────────────
    if _time.time() >= tokens.get("expires_at", 0):
        refresh_tok = tokens.get("refresh_token", "")
        if not refresh_tok:
            print(
                "ERROR: PrusaConnect token expired and no refresh token stored.\n"
                "       Run: python3 prusa_login.py",
                file=sys.stderr,
            )
            sys.exit(1)
        print("PrusaConnect token expired — refreshing…", file=sys.stderr)
        CLIENT_ID = "oamhmhZez7opFosnwzElIgE2oGgI2iJORSkw587O"
        TOKEN_URL = "https://account.prusa3d.com/o/token/"
        body = urllib.parse.urlencode({
            "grant_type":    "refresh_token",
            "client_id":     CLIENT_ID,
            "refresh_token": refresh_tok,
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = _json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(
                f"ERROR: token refresh failed (HTTP {e.code}).\n"
                "       Run: python3 prusa_login.py",
                file=sys.stderr,
            )
            sys.exit(1)
        tokens["access_token"] = raw["access_token"]
        if "refresh_token" in raw:
            tokens["refresh_token"] = raw["refresh_token"]
        tokens["expires_at"] = _time.time() + int(raw.get("expires_in", 3600)) - 60
        _save(tokens)

    access_token  = tokens["access_token"]
    team_id       = tokens.get("team_id", "")
    printer_uuid  = tokens.get("printer_uuid", "")
    printer_name  = tokens.get("printer_name", printer_uuid)

    if not team_id or not printer_uuid:
        print(
            "ERROR: token file is missing team_id or printer_uuid.\n"
            "       Run: python3 prusa_login.py",
            file=sys.stderr,
        )
        sys.exit(1)

    base = "https://connect.prusa3d.com"

    def _bearer_req(url: str, method: str, body=None, content_type: str = "") -> urllib.request.Request:
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {access_token}")
        if content_type:
            req.add_header("Content-Type", content_type)
        return req

    # ── Step 1: register the upload ────────────────────────────────────────
    init_url  = f"{base}/app/users/teams/{team_id}/uploads"
    init_body = _json.dumps({
        "printer_uuid": printer_uuid,
        "filename":     filename,
        "size":         len(data),
        "position":     -1,         # append to print queue
    }).encode()

    print(
        f"Uploading {filename} ({len(data):,} bytes) to PrusaConnect "
        f"(printer: {printer_name})…",
        file=sys.stderr,
    )
    try:
        req = _bearer_req(init_url, "POST", init_body, "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            init_resp = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        print(f"ERROR: upload registration failed: HTTP {e.code} — {body_txt}",
              file=sys.stderr)
        sys.exit(1)

    upload_id = init_resp.get("id")
    if not upload_id:
        print(f"ERROR: server did not return an upload id. Response: {init_resp}",
              file=sys.stderr)
        sys.exit(1)

    # ── Step 2: PUT the file ───────────────────────────────────────────────
    put_url = f"{base}/app/teams/{team_id}/files/raw?upload_id={upload_id}"
    if start_print:
        put_url += "&to_print=true"

    try:
        req = _bearer_req(put_url, "PUT", data, "text/x.gcode")
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        print(f"ERROR: file upload failed: HTTP {e.code} — {body_txt}", file=sys.stderr)
        sys.exit(1)

    action = " (print queued)" if start_print else ""
    print(f"Upload complete — HTTP {status}{action}", file=sys.stderr)


def _upload_prusalink(
    data: bytes,
    url: str,
    key: str,
    filename: str,
    start_print: bool,
) -> None:
    """Upload G-code to a printer running PrusaLink and optionally start printing.

    Uses the PrusaLink v1 REST API (PUT /api/v1/files/local/{filename}).
    Authentication is via the X-Api-Key header.  Print-After-Upload uses the
    RFC 8941 boolean header as documented in the PrusaLink OpenAPI spec.

    PrusaConnect (connect.prusa3d.com) exposes the same API: pass its URL and
    the printer's API key from the Connect web UI.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    base = url.rstrip("/")
    upload_url = f"{base}/api/v1/files/local/{urllib.parse.quote(filename, safe='')}"

    headers = {
        "X-Api-Key": key,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(data)),
        "Overwrite": "?1",
    }
    if start_print:
        headers["Print-After-Upload"] = "?1"

    req = urllib.request.Request(
        upload_url,
        data=data,
        method="PUT",
        headers=headers,
    )

    print(f"Uploading {filename} ({len(data):,} bytes) to {base} …", file=sys.stderr)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        print(f"ERROR: upload failed: HTTP {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: upload failed: {e.reason}", file=sys.stderr)
        sys.exit(1)

    action = "uploaded and print started" if start_print else "uploaded"
    print(f"OK — {filename} {action} (HTTP {status})", file=sys.stderr)


# ── Shared configuration dataclass ────────────────────────────────────────────

@dataclass
class CommonConfig:
    """Configuration fields shared by all calibration scripts."""

    # Bed / build volume
    bed_x: float = BED_X
    bed_y: float = BED_Y
    max_z: float = MAX_Z

    # Toolhead
    nozzle_dia:   float = 0.4
    filament_dia: float = 1.75

    # Temperatures
    bed_temp:    int = 60
    hotend_temp: int = 215

    # Layer heights
    first_layer_height: float = 0.25
    layer_height:       float = 0.20

    # Speeds (mm/s)
    print_speed:       float = 100.0
    first_layer_speed: float = 30.0
    travel_speed:      float = 150.0

    # Extrusion
    line_width_pct:        float = 112.5   # % of nozzle diameter
    anchor_line_width_pct: float = 140.0
    extrusion_multiplier:  float = 0.98

    # Retraction / Z-hop
    retract_dist:    float = 0.6
    retract_speed:   float = 45.0
    unretract_speed: float = 45.0
    zhop:            float = 0.1   # 0 = disabled

    # Anchor (frame | layer | none)
    anchor:            str = "frame"
    anchor_perimeters: int = 4

    # Display
    show_lcd: bool = True

    # Fan (0–100 %)
    fan_speed:       int = 100
    first_layer_fan: int = 0

    # Custom start / end G-code file paths (None = use built-in Core One defaults)
    start_gcode_file: Optional[str] = None
    end_gcode_file:   Optional[str] = None

    # Output
    output: Optional[str] = None


# ── Printer state ──────────────────────────────────────────────────────────────

class _State:
    __slots__ = ("x", "y", "z", "retracted", "hopped")

    def __init__(self):
        self.x = self.y = self.z = 0.0
        self.retracted = self.hopped = False


# ── Base G-code generator ──────────────────────────────────────────────────────

class BaseGenerator:
    """Shared G-code generation infrastructure for all calibration scripts."""

    def __init__(self, cfg: CommonConfig,
                 start_template: Optional[str] = None,
                 end_template:   Optional[str] = None):
        self.cfg = cfg
        self._st  = _State()
        self._buf: list[str] = []
        self._start_tmpl = start_template if start_template is not None else _builtin_gcode("coreone_start.gcode")
        self._end_tmpl   = end_template   if end_template   is not None else _builtin_gcode("coreone_end.gcode")

        c = cfg
        self._lw  = c.nozzle_dia * (c.line_width_pct / 100.0)
        self._alw = c.nozzle_dia * (c.anchor_line_width_pct / 100.0)

    # ── buffer helpers ─────────────────────────────────────────────────────────

    def _emit(self, line: str):
        self._buf.append(line)

    def _comment(self, text: str):
        self._emit(f"; {text}")

    def _blank(self):
        self._emit("")

    # ── motion helpers ─────────────────────────────────────────────────────────

    def _e_amount(self, dist: float, lh: float, lw: float) -> float:
        """Extrusion length (mm of filament) for a move of given XY distance."""
        fil_r = self.cfg.filament_dia / 2.0
        vol   = dist * lh * lw
        return _r(vol / (math.pi * fil_r ** 2) * self.cfg.extrusion_multiplier, _E)

    @staticmethod
    def _dist(x0: float, y0: float, x1: float, y1: float) -> float:
        return math.hypot(x1 - x0, y1 - y0)

    def _retract(self):
        """Retract filament then z-hop (idempotent — safe to call repeatedly)."""
        if self._st.retracted:
            return
        e = _r(self.cfg.retract_dist, _E)
        self._emit(f"G1 E-{e} F{int(self.cfg.retract_speed * 60)}")
        self._st.retracted = True
        if self.cfg.zhop > 0 and not self._st.hopped:
            z = _r(self._st.z + self.cfg.zhop, _Z)
            self._emit(f"G0 Z{z} F{int(self.cfg.travel_speed * 60)}")
            self._st.hopped = True

    def _unretract(self):
        """Undo z-hop then unretract (idempotent)."""
        if not self._st.retracted:
            return
        if self._st.hopped:
            self._emit(f"G0 Z{_r(self._st.z, _Z)} F{int(self.cfg.travel_speed * 60)}")
            self._st.hopped = False
        e = _r(self.cfg.retract_dist, _E)
        self._emit(f"G1 E{e} F{int(self.cfg.unretract_speed * 60)}")
        self._st.retracted = False

    def _travel(self, x: float, y: float, threshold: float = 2.0):
        """Rapid XY travel with auto retract/unretract for long moves."""
        dist = self._dist(self._st.x, self._st.y, x, y)
        if dist > threshold:
            self._retract()
        self._emit(f"G0 X{_r(x, _XY)} Y{_r(y, _XY)} F{int(self.cfg.travel_speed * 60)}")
        self._st.x = x
        self._st.y = y
        if dist > threshold:
            self._unretract()

    def _line(self, x: float, y: float, speed: float, lh: float, lw: float):
        """Extrusion move from current position to (x, y)."""
        dist = self._dist(self._st.x, self._st.y, x, y)
        if dist < 1e-6:
            return
        e = self._e_amount(dist, lh, lw)
        self._emit(f"G1 X{_r(x, _XY)} Y{_r(y, _XY)} E{e} F{int(speed * 60)}")
        self._st.x = x
        self._st.y = y

    # ── drawing primitives ─────────────────────────────────────────────────────

    def _perimeter(self, x0: float, y0: float, x1: float, y1: float,
                   speed: float, lh: float, lw: float):
        """Draw a single closed rectangular perimeter."""
        self._travel(x0, y0)
        self._line(x1, y0, speed, lh, lw)
        self._line(x1, y1, speed, lh, lw)
        self._line(x0, y1, speed, lh, lw)
        self._line(x0, y0, speed, lh, lw)

    def _anchor_frame(self, x0: float, y0: float, sx: float, sy: float,
                      lh: float, lw: float, perims: int, speed: float):
        """Draw concentric rectangular perimeter loops."""
        spacing = lw - lh * (1.0 - math.pi / 4.0)
        for i in range(perims):
            off = i * spacing
            bx0, by0 = x0 + off, y0 + off
            bx1, by1 = x0 + sx - off, y0 + sy - off
            if bx0 >= bx1 or by0 >= by1:
                break
            self._perimeter(bx0, by0, bx1, by1, speed, lh, lw)

    def _anchor_layer(self, x0: float, y0: float, sx: float, sy: float,
                      lh: float, lw: float, speed: float):
        """Draw a filled anchor layer: 2 perimeters + horizontal infill."""
        self._anchor_frame(x0, y0, sx, sy, lh, lw, 2, speed)
        spacing = lw - lh * (1.0 - math.pi / 4.0)
        ix0 = x0 + 2 * spacing
        iy0 = y0 + 2 * spacing
        ix1 = x0 + sx - 2 * spacing
        iy1 = y0 + sy - 2 * spacing
        y = iy0
        lr = True
        while y <= iy1 + 1e-6:
            if lr:
                self._travel(ix0, y)
                self._line(ix1, y, speed, lh, lw)
            else:
                self._travel(ix1, y)
                self._line(ix0, y, speed, lh, lw)
            y += spacing
            lr = not lr

    def _circle(self, cx: float, cy: float, r: float, speed: float, lh: float, lw: float):
        """Draw a single closed circular perimeter of radius r centred at (cx, cy)."""
        if r < lw * 0.5:
            return
        n_segs = max(12, int(2 * math.pi * r / lw))
        pts = [
            (_r(cx + r * math.cos(2 * math.pi * i / n_segs), _XY),
             _r(cy + r * math.sin(2 * math.pi * i / n_segs), _XY))
            for i in range(n_segs)
        ]
        self._travel(pts[0][0], pts[0][1])
        for x, y in pts[1:]:
            self._line(x, y, speed, lh, lw)
        self._line(pts[0][0], pts[0][1], speed, lh, lw)  # close the loop

    # ── 7-segment number rendering ─────────────────────────────────────────────

    _SEGS: dict[str, tuple] = {
        "0": (1, 1, 1, 1, 1, 1, 0),
        "1": (0, 1, 1, 0, 0, 0, 0),
        "2": (1, 1, 0, 1, 1, 0, 1),
        "3": (1, 1, 1, 1, 0, 0, 1),
        "4": (0, 1, 1, 0, 0, 1, 1),
        "5": (1, 0, 1, 1, 0, 1, 1),
        "6": (1, 0, 1, 1, 1, 1, 1),
        "7": (1, 1, 1, 0, 0, 0, 0),
        "8": (1, 1, 1, 1, 1, 1, 1),
        "9": (1, 1, 1, 1, 0, 1, 1),
    }
    _SEG_LEN: float = 2.0   # mm — length of each 7-segment bar
    _SEG_GAP: float = 1.0   # mm — gap between characters

    def _digit_width(self) -> float:
        return self._SEG_LEN + self._SEG_GAP

    def _num_tab_height(self) -> float:
        """Vertical space needed for a row of number labels."""
        return self._SEG_LEN * 2.0 + 3.0   # glyph height + padding

    def _draw_digit(self, x: float, y: float, ch: str,
                    lh: float, lw: float, spd: float) -> float:
        """Draw a single glyph at bottom-left (x, y). Returns x-advance in mm."""
        SL = self._SEG_LEN
        if ch == ".":
            self._travel(x, y)
            self._line(x + 0.6, y, spd, lh, lw)
            return 0.6 + self._SEG_GAP
        if ch not in self._SEGS:
            return self._digit_width()
        t, tr, br, bot, bl, tl, mid = self._SEGS[ch]
        if t:   self._travel(x,      y + 2*SL); self._line(x + SL, y + 2*SL, spd, lh, lw)
        if tr:  self._travel(x + SL, y +   SL); self._line(x + SL, y + 2*SL, spd, lh, lw)
        if br:  self._travel(x + SL, y       ); self._line(x + SL, y +   SL, spd, lh, lw)
        if bot: self._travel(x,      y       ); self._line(x + SL, y,        spd, lh, lw)
        if bl:  self._travel(x,      y       ); self._line(x,      y +   SL, spd, lh, lw)
        if tl:  self._travel(x,      y +   SL); self._line(x,      y + 2*SL, spd, lh, lw)
        if mid: self._travel(x,      y +   SL); self._line(x + SL, y +   SL, spd, lh, lw)
        return self._digit_width()

    def _draw_number(self, x: float, y: float, value: float,
                     lh: float, lw: float, spd: float, *,
                     no_leading_zeros: bool = False):
        """Render a floating-point value as 7-segment glyphs starting at (x, y)."""
        text = f"{value:.{_PA}f}".rstrip("0").rstrip(".")
        if "." not in text and value != int(value):
            text = f"{value:.1f}"
        if no_leading_zeros and text.startswith("0."):
            text = text[1:]
        cx = x
        for ch in text:
            cx += self._draw_digit(cx, y, ch, lh, lw * 0.8, spd)

    # ── geometry helpers ───────────────────────────────────────────────────────

    def _m555(self, orig_x: float, orig_y: float,
              total_w: float, total_h: float) -> tuple[int, int, int, int]:
        """
        Compute M555 print-area bounds using the same formula as PrusaSlicer's
        Prusa Core One profile (which also accounts for the purge-line area).
        """
        c      = self.cfg
        pmin_x = orig_x
        pmax_x = orig_x + total_w
        pmin_y = orig_y
        pmax_y = orig_y + total_h

        x = min(c.bed_x, pmin_x + 32) - 32
        y = max(0.0,     pmin_y)       - 4
        w = min(c.bed_x, max(pmin_x + 32, pmax_x)) - x
        h = pmax_y - y
        return round(x), round(y), round(w), round(h)

    def _base_tmpl_vars(self, max_layer_z: float, orig_x: float, orig_y: float,
                        total_w: float, total_h: float) -> dict:
        """Build the standard template variable dict for start/end G-code."""
        c        = self.cfg
        mbl_temp = max(155, c.hotend_temp - 50)
        cool_fan = ("M106 S70  ; cool enclosure (PLA bed temp)"
                    if c.bed_temp <= 60 else "M107")
        park_z   = _r(min(max_layer_z + 1.0, c.max_z), _Z)
        m555_x, m555_y, m555_w, m555_h = self._m555(orig_x, orig_y, total_w, total_h)
        return dict(
            bed_temp    = c.bed_temp,
            hotend_temp = c.hotend_temp,
            mbl_temp    = mbl_temp,
            nozzle_dia  = c.nozzle_dia,
            filament_dia= c.filament_dia,
            cool_fan    = cool_fan,
            m555_x      = m555_x,
            m555_y      = m555_y,
            m555_w      = m555_w,
            m555_h      = m555_h,
            park_z      = park_z,
            max_layer_z = max_layer_z,
        )


# ── CLI helpers ────────────────────────────────────────────────────────────────

def add_common_args(p: argparse.ArgumentParser, default_stem: str = "output") -> None:
    """Add shared CLI arguments to parser p (modifies in place).

    Adds: --printer, --filament, all shared option groups, -o/--ascii,
    and the PrusaLink/PrusaConnect upload groups.

    Call this first in _build_parser(), then add script-specific groups.
    """
    p.add_argument(
        "--printer",
        type=str.upper,
        choices=list(PRINTER_PRESETS),
        default=_DEFAULT_PRINTER,
        metavar="MODEL",
        help=(
            "Printer model — sets bed size and max Z height, and selects the "
            "matching start/end G-code from the gcode/ directory. "
            f"Choices: {', '.join(PRINTER_PRESETS)}  "
            f"(default: {_DEFAULT_PRINTER})."
        ),
    )
    p.add_argument(
        "--filament",
        type=str.upper,
        choices=list(FILAMENT_PRESETS),
        metavar="TYPE",
        default=None,
        help=(
            "Filament preset — sets hotend/bed temps, fan speed, and retraction. "
            "Explicit flags override the preset. "
            f"Choices: {', '.join(FILAMENT_PRESETS)}"
        ),
    )

    g = p.add_argument_group("Temperatures")
    g.add_argument("--hotend-temp", type=int, default=None, metavar="°C",
                   help="Hotend temperature (default: from --filament preset, or 215)")
    g.add_argument("--bed-temp",    type=int, default=None, metavar="°C",
                   help="Bed temperature (default: from --filament preset, or 60)")

    g = p.add_argument_group("Printer / toolhead")
    g.add_argument("--nozzle-dia",   type=float, default=0.4,  metavar="mm")
    g.add_argument("--filament-dia", type=float, default=1.75, metavar="mm")
    g.add_argument("--bed-x",        type=float, default=None, metavar="mm",
                   help="Bed X size (default: from --printer preset)")
    g.add_argument("--bed-y",        type=float, default=None, metavar="mm",
                   help="Bed Y size (default: from --printer preset)")
    g.add_argument("--max-z",        type=float, default=None, metavar="mm",
                   help="Maximum build height (default: from --printer preset)")

    g = p.add_argument_group("Layer settings")
    g.add_argument("--first-layer-height", type=float, default=0.25, metavar="mm")
    g.add_argument("--layer-height",       type=float, default=0.20, metavar="mm")

    g = p.add_argument_group("Speed (mm/s)")
    g.add_argument("--print-speed",       type=float, default=100.0, metavar="mm/s")
    g.add_argument("--first-layer-speed", type=float, default=30.0,  metavar="mm/s")
    g.add_argument("--travel-speed",      type=float, default=150.0, metavar="mm/s")

    g = p.add_argument_group("Extrusion")
    g.add_argument("--line-width-pct",       type=float, default=112.5,
                   metavar="%", help="Line width as %% of nozzle diameter")
    g.add_argument("--extrusion-multiplier", type=float, default=0.98, metavar="ratio")

    g = p.add_argument_group("Retraction")
    g.add_argument("--retract-dist",    type=float, default=None, metavar="mm",
                   help="Retraction distance (default: from --filament preset, or 0.6)")
    g.add_argument("--retract-speed",   type=float, default=45.0, metavar="mm/s")
    g.add_argument("--unretract-speed", type=float, default=45.0, metavar="mm/s")
    g.add_argument("--zhop",            type=float, default=0.1,
                   metavar="mm", help="Z-hop height; 0 to disable")

    g = p.add_argument_group("Anchor")
    g.add_argument("--anchor", choices=["frame", "layer", "none"], default="frame",
                   help="Anchor type printed on first layer for adhesion")
    g.add_argument("--anchor-perimeters", type=int, default=4, metavar="N")

    g = p.add_argument_group("Options")
    g.add_argument("--no-lcd", dest="show_lcd", action="store_false",
                   help="Suppress M117 display messages")
    g.add_argument("--fan-speed",       type=int, default=None, metavar="%",
                   help="Part-cooling fan speed from layer 2 onward "
                        "(default: from --filament preset, or 100)")
    g.add_argument("--first-layer-fan", type=int, default=None, metavar="%",
                   help="Part-cooling fan speed on first layer "
                        "(default: from --filament preset, or 0)")

    g = p.add_argument_group("G-code templates (override built-in Prusa defaults)")
    g.add_argument("--start-gcode", metavar="FILE",
                   help="File containing custom start G-code template")
    g.add_argument("--end-gcode",   metavar="FILE",
                   help="File containing custom end G-code template")

    p.add_argument("-o", "--output", metavar="FILE",
                   help="Write G-code to FILE (default: stdout)")
    p.add_argument("--ascii", action="store_true",
                   help=(
                       "Output plain ASCII G-code instead of Prusa binary (.bgcode). "
                       "Binary is the default; use -o with a .gcode extension when "
                       "passing --ascii, e.g. -o cal.gcode."
                   ))

    g = p.add_argument_group("PrusaLink upload (local network)")
    g.add_argument("--prusalink-url", metavar="URL",
                   help="Base URL of the printer's PrusaLink interface "
                        "(e.g. http://192.168.1.100). "
                        "Uploads the generated G-code to the printer after generation. "
                        "Requires --prusalink-key.")
    g.add_argument("--prusalink-key", metavar="KEY",
                   help="PrusaLink API key (Settings → API Key in the printer web UI)")
    g.add_argument("--prusalink-filename", metavar="NAME",
                   help=f"Filename to store on the printer "
                        f"(default: basename of -o, or {default_stem}.bgcode)")
    g.add_argument("--prusalink-print", action="store_true",
                   help="Start printing immediately after upload")

    g = p.add_argument_group("PrusaConnect upload (cloud)")
    g.add_argument("--prusaconnect", action="store_true",
                   help="Upload to PrusaConnect using saved login tokens. "
                        "Run 'python3 prusa_login.py' once to authenticate "
                        "and select a printer.")
    g.add_argument("--prusaconnect-filename", metavar="NAME",
                   help=f"Filename to store in PrusaConnect "
                        f"(default: basename of -o, or {default_stem}.bgcode)")
    g.add_argument("--prusaconnect-print", action="store_true",
                   help="Start printing immediately after upload")


def resolve_presets(args, script_dir: str) -> tuple:
    """Resolve printer/filament presets and load G-code templates.

    Prints a summary line to stderr for each resolved preset.
    Returns (ppreset, fpreset, start_tmpl, end_tmpl).
    """
    def _load(path: str) -> str:
        try:
            with open(path) as f:
                return f.read()
        except OSError as e:
            print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def _resolve_template(arg_path: Optional[str], printer_key: str, role: str) -> str:
        """Load template: explicit arg > printer gcode file > Core One default."""
        if arg_path is not None:
            return _load(arg_path)
        candidate = os.path.join(_GCODE_DIR, f"{printer_key.lower()}_{role}.gcode")
        if os.path.exists(candidate):
            return _load(candidate)
        # No printer-specific file — fall back to the Core One default with a warning.
        if printer_key != "COREONE":
            print(
                f"WARNING: no gcode/{printer_key.lower()}_{role}.gcode found — "
                f"using Core One default (gcode/coreone_{role}.gcode). "
                f"Supply --{role}-gcode to override.",
                file=sys.stderr,
            )
        return _builtin_gcode(f"coreone_{role}.gcode")

    ppreset = PRINTER_PRESETS[args.printer]
    print(f"Printer: {args.printer}  "
          f"bed {ppreset['bed_x']:.0f}×{ppreset['bed_y']:.0f} mm  "
          f"max Z {ppreset['max_z']:.0f} mm",
          file=sys.stderr)

    start_tmpl = _resolve_template(getattr(args, "start_gcode", None), args.printer, "start")
    end_tmpl   = _resolve_template(getattr(args, "end_gcode",   None), args.printer, "end")

    fpreset = FILAMENT_PRESETS.get(args.filament, {}) if args.filament else {}
    if fpreset:
        print(f"Filament: {args.filament}  "
              f"hotend {fpreset['hotend_temp']} °C  "
              f"bed {fpreset['bed_temp']} °C  "
              f"fan {fpreset['fan_speed']} %  "
              f"retract {fpreset['retract_dist']} mm",
              file=sys.stderr)

    return ppreset, fpreset, start_tmpl, end_tmpl


def handle_output(gcode: str, args, default_stem: str, thumbnails=()) -> None:
    """Write G-code to file/stdout and handle PrusaLink/PrusaConnect uploads.

    Default output format is Prusa binary G-code v1 (.bgcode).
    Pass --ascii to get plain text instead.

    thumbnails — optional sequence of (width, height, png_bytes) tuples:
                 embedded as BLK_THUMBNAIL blocks in binary output, or as
                 PrusaSlicer-style base64 comment blocks in ASCII output.
    """
    if not getattr(args, "ascii", False):
        # Binary output (default)
        if args.output:
            n = _write_bgcode(gcode, args.output, thumbnails=thumbnails)
            lines = gcode.count("\n")
            print(f"Wrote {lines} G-code lines as bgcode ({n:,} bytes) → {args.output}",
                  file=sys.stderr)
        else:
            _write_bgcode(gcode, sys.stdout.buffer, thumbnails=thumbnails)
    else:
        # ASCII output (--ascii): prepend PrusaSlicer-style base64 thumbnail
        # comment blocks so slicer previews (OrcaSlicer, PrusaSlicer, etc.)
        # can show the image.
        thumb_hdr = _thumbnails_to_gcode_comments(thumbnails)
        out_gcode = thumb_hdr + gcode
        if args.output:
            with open(args.output, "w") as f:
                f.write(out_gcode)
            lines = out_gcode.count("\n")
            print(f"Wrote {lines} lines ({len(out_gcode):,} bytes) → {args.output}",
                  file=sys.stderr)
        else:
            sys.stdout.write(out_gcode)

    def _upload_data() -> bytes:
        # Always upload as bgcode (DEFLATE-compressed) regardless of local
        # output format — PrusaLink/PrusaConnect accept bgcode natively and
        # the compressed payload avoids HTTP 413 errors on large files.
        buf = io.BytesIO()
        _write_bgcode(gcode, buf, thumbnails=thumbnails)
        return buf.getvalue()

    def _remote_name(service: str) -> str:
        explicit = getattr(args, f"{service}_filename", None)
        if explicit:
            return explicit
        if args.output:
            stem = os.path.splitext(os.path.basename(args.output))[0]
            return stem + ".bgcode"
        return default_stem + ".bgcode"

    if getattr(args, "prusalink_url", None):
        if not getattr(args, "prusalink_key", None):
            print("ERROR: --prusalink-key is required when --prusalink-url is set",
                  file=sys.stderr)
            sys.exit(1)
        _upload_prusalink(
            _upload_data(),
            url=args.prusalink_url,
            key=args.prusalink_key,
            filename=_remote_name("prusalink"),
            start_print=getattr(args, "prusalink_print", False),
        )

    if getattr(args, "prusaconnect", False):
        _upload_prusaconnect(
            _upload_data(),
            filename=_remote_name("prusaconnect"),
            start_print=getattr(args, "prusaconnect_print", False),
        )
