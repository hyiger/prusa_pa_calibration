#!/usr/bin/env python3
"""
pa_cal.py — Linear Advance calibration G-code generator for Prusa Core One

Generates a test print with multiple corner-pattern pieces, each at a different
Linear Advance (M900 K) value, so you can visually pick the best setting.

Usage:
    python pa_cal.py -o la_cal.gcode
    python pa_cal.py --la-start 0 --la-end 4 --la-step 1 -o coarse.gcode
    python pa_cal.py --la-start 1.5 --la-end 2.5 --la-step 0.1 --side-length 11 -o fine.gcode
    python pa_cal.py --hotend-temp 235 --bed-temp 85 -o petg.gcode

Inspect output in PrusaSlicer / OrcaSlicer preview or any G-code viewer.
The pattern with the sharpest corners (no bulge, no gap) gives the best K value.

Template variables available in --start-gcode / --end-gcode files:
    {bed_temp}      Bed temperature (°C)
    {hotend_temp}   Hotend temperature (°C)
    {mbl_temp}      Nozzle temp used during mesh bed leveling (lower, no-ooze)
    {nozzle_dia}    Nozzle diameter (mm)
    {filament_dia}  Filament diameter (mm)
    {cool_fan}      "M106 S70" when bed_temp<=60 (enclosure cool), else "M107"
    {m555_x}        M555 print-area origin X
    {m555_y}        M555 print-area origin Y
    {m555_w}        M555 print-area width
    {m555_h}        M555 print-area height
    {park_z}        Safe Z height for end-of-print parking
    {max_layer_z}   Highest layer Z in the calibration print
"""

import argparse
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

# ── Prusa Core One bed size ────────────────────────────────────────────────────
BED_X       = 250.0
BED_Y       = 220.0
MAX_Z       = 270.0   # Core One build height

# ── Filament presets ───────────────────────────────────────────────────────────
# Each entry overrides only the parameters that differ from PLA defaults.
# Explicit CLI flags always win over preset values.
#
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
# Sets bed dimensions and max build height.
# The built-in start/end G-code is tuned for the Core One; all other printers
# should supply --start-gcode / --end-gcode.
#
# Fields: bed_x (mm), bed_y (mm), max_z (mm), model (M862.3 string)
PRINTER_PRESETS: dict[str, dict] = {
    "MINI":     dict(bed_x=180.0, bed_y=180.0, max_z=180.0, model="MINI"),
    "MK4S":     dict(bed_x=250.0, bed_y=210.0, max_z=220.0, model="MK4S"),
    "COREONE":  dict(bed_x=250.0, bed_y=220.0, max_z=270.0, model="COREONE"),
    "COREONEL": dict(bed_x=300.0, bed_y=300.0, max_z=330.0, model="COREONEL"),
    "XL":       dict(bed_x=360.0, bed_y=360.0, max_z=360.0, model="XL"),
}
_DEFAULT_PRINTER = "COREONE"

# ── Default start / end G-code ─────────────────────────────────────────────────
# Derived from PrusaSlicer's Prusa Core One start G-code.
# PrusaSlicer conditionals have been resolved to their common-case concrete values:
#   • No chamber heating (chamber_minimal_temperature = 0)
#   • Non-FLEX filament (standard 2 mm retraction)
#   • Standard idle temp of 100 °C
# Variables in {braces} are substituted by pa_cal; everything else is literal.

DEFAULT_START_GCODE = """\
M17 ; enable steppers
M862.1 P{nozzle_dia} ; nozzle check
M862.3 P "COREONE" ; printer model check
M862.5 P2 ; g-code level check
M862.6 P"Input shaper" ; FW feature check
M115 U6.4.0+11974
M555 X{m555_x} Y{m555_y} W{m555_w} H{m555_h}
G90 ; use absolute coordinates
M83 ; extruder relative mode
M140 S{bed_temp} ; set bed temp
M109 R{mbl_temp} ; preheat nozzle to no-ooze temp for bed leveling
M84 E ; turn off E motor
G28 ; home all without mesh bed level
M104 S100 ; set idle temp
M190 R{bed_temp} ; wait for bed temp
{cool_fan}
G0 Z40 F10000
M104 S100 ; keep idle temp
M190 R{bed_temp} ; wait for bed temp (confirm after Z move)
M107
G29 G ; absorb heat
M109 R{mbl_temp} ; wait for MBL temp
M302 S155 ; lower cold extrusion limit to 155 C
G1 E-2 F2400 ; retraction
M84 E ; turn off E motor
G29 P9 X208 Y-2.5 W32 H4
;
; MBL
;
M84 E ; turn off E motor
G29 P1 ; invalidate mbl and probe print area
G29 P1 X150 Y0 W100 H20 C ; probe near purge place
G29 P3.2 ; interpolate mbl probes
G29 P3.13 ; extrapolate mbl outside probe area
G29 A ; activate mbl
; prepare for purge
M104 S{hotend_temp}
G0 X249 Y-2.5 Z15 F4800 ; move away and ready for the purge
M109 S{hotend_temp}
G92 E0
M569 S0 E ; set spreadcycle mode for extruder
M591 S0 ; disable stuck filament detection
;
; Purge line
;
G92 E0 ; reset extruder position
G1 E2 F2400 ; deretraction after the initial one
G0 E5 X235 Z0.2 F500 ; purge
G0 X225 E4 F500 ; purge
G0 X215 E4 F650 ; purge
G0 X205 E4 F800 ; purge
G0 X202 Z0.05 F8000 ; wipe, move close to the bed
G0 X199 Z0.2 F8000 ; wipe, move away from the bed
M591 R ; restore stuck filament detection
G92 E0
M221 S100 ; set flow to 100%
"""

DEFAULT_END_GCODE = """\
G1 Z{park_z} F720 ; move print head up
M104 S0 ; turn off hotend
M140 S0 ; turn off heatbed
M141 S0 ; disable chamber temp control
M107 ; turn off fan
G1 X242 Y211 F10200 ; park
G4 ; wait
M572 S0 ; reset pressure advance (ignored on Marlin)
M900 K0 ; reset Linear Advance
M84 X Y E ; disable motors
; max_layer_z = {max_layer_z}
"""


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


def _write_bgcode(gcode_text: str, dest) -> int:
    """
    Write gcode_text as a Prusa binary G-code v1 (.bgcode) file.

    dest — file path (str/Path) or a binary-mode file object (e.g. sys.stdout.buffer).
    Returns the number of bytes written.

    Format reference: https://github.com/prusa3d/libbgcode
    File structure:
        File header   (10 bytes):  magic "GCDE" + version uint32_le + checksum_type uint16_le
        Block*        (N bytes):   header + payload + CRC32
    Checksum is CRC32 over (block_header_bytes + written_payload_bytes).
    """
    import struct
    import zlib as _zlib

    # ── constants ────────────────────────────────────────────────────────────────
    MAGIC           = b"GCDE"
    VERSION         = 1
    CKSUM_CRC32     = 1        # checksum_type in file header

    BLK_PRINTER_META = 3       # PrinterMetadata block type
    BLK_PRINT_META   = 4       # PrintMetadata block type
    BLK_GCODE        = 1       # GCode block type

    COMP_NONE        = 0       # no compression
    COMP_DEFLATE     = 1       # raw RFC-1951 DEFLATE (no zlib wrapper)

    ENC_INI          = 0       # metadata encoding: INI key=value text
    ENC_RAW          = 0       # G-code encoding: raw UTF-8 text

    # ── helpers ──────────────────────────────────────────────────────────────────
    def _crc(data: bytes, prev: int = 0) -> int:
        return _zlib.crc32(data, prev) & 0xFFFFFFFF

    def _deflate(data: bytes) -> bytes:
        """Raw DEFLATE (RFC 1951) — no zlib/gzip wrapper."""
        obj = _zlib.compressobj(level=6, wbits=-15)
        return obj.compress(data) + obj.flush()

    def _block(btype: int, payload: bytes, compress: bool = False) -> bytes:
        """Build one complete bgcode block: header + data + CRC32."""
        if compress:
            data = _deflate(payload)
            hdr  = struct.pack("<HHII", btype, COMP_DEFLATE, len(payload), len(data))
        else:
            data = payload
            hdr  = struct.pack("<HHI",  btype, COMP_NONE,    len(payload))
        cksum = _crc(hdr)
        cksum = _crc(data, cksum)
        return hdr + data + struct.pack("<I", cksum)

    def _meta_block(btype: int, fields: Optional[dict] = None) -> bytes:
        """Build a metadata block with optional INI key=value content."""
        ini = "".join(f"{k}={v}\n" for k, v in (fields or {}).items())
        payload = struct.pack("<H", ENC_INI) + ini.encode("utf-8")
        return _block(btype, payload)

    # ── assemble file ─────────────────────────────────────────────────────────
    file_hdr  = MAGIC + struct.pack("<IH", VERSION, CKSUM_CRC32)
    meta_blks = (
        _meta_block(BLK_PRINTER_META)
        + _meta_block(BLK_PRINT_META, {"generator": "pa_cal.py"})
    )
    gcode_payload = struct.pack("<H", ENC_RAW) + gcode_text.encode("utf-8")
    gcode_blk = _block(BLK_GCODE, gcode_payload, compress=True)

    content = file_hdr + meta_blks + gcode_blk

    if isinstance(dest, (str, bytes)):
        with open(dest, "wb") as f:
            f.write(content)
    else:
        dest.write(content)

    return len(content)


def _upload_prusalink(
    data: bytes,
    url: str,
    key: str,
    filename: str,
    start_print: bool,
) -> None:
    """Upload G-code to a printer running PrusaLink and optionally start printing.

    Uses the PrusaLink v1 REST API (PUT /api/v1/files/local/{filename}).
    Authentication is via the X-Api-Key header (Settings → API Key in the
    printer's web UI).  Print-After-Upload uses the RFC 8941 boolean header
    as documented in the PrusaLink OpenAPI spec.

    url         — base URL of the printer, e.g. http://192.168.1.100
    key         — PrusaLink API key
    filename    — filename to create on the printer's local storage
    start_print — set Print-After-Upload: ?1 to auto-start after upload
    """
    import urllib.error
    import urllib.request

    base = url.rstrip("/")
    upload_url = f"{base}/api/v1/files/local/{filename}"

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


# ── Configuration dataclass ────────────────────────────────────────────────────

@dataclass
class Config:
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

    # Linear Advance range.
    # Default = coarse scan K 0–4 in steps of 1 (5 patterns, fits 220 mm bed).
    # After picking a winner, re-run with a fine step, e.g.:
    #   --la-start 1.5 --la-end 2.5 --la-step 0.1 --side-length 11
    la_start: float = 0.0
    la_end:   float = 4.0
    la_step:  float = 1.0

    # Layer heights
    first_layer_height: float = 0.25
    layer_height:       float = 0.20
    layer_count:        int   = 4

    # Speeds (mm/s)
    print_speed:       float = 100.0
    first_layer_speed: float = 30.0
    travel_speed:      float = 150.0

    # Extrusion
    line_width_pct:        float = 112.5  # % of nozzle diameter
    anchor_line_width_pct: float = 140.0
    extrusion_multiplier:  float = 0.98

    # Retraction / Z-hop (Core One is direct drive)
    retract_dist:    float = 0.6
    retract_speed:   float = 45.0
    unretract_speed: float = 45.0
    zhop:            float = 0.1   # 0 = disabled

    # Pattern geometry.
    # side_length=20 fits 5 patterns on a 220 mm bed.
    # Decrease for fine-step scans with more patterns.
    wall_count:      int   = 3
    side_length:     float = 20.0
    pattern_spacing: float = 2.0
    corner_angle:    float = 90.0  # degrees at apex

    # Anchor (frame | layer | none)
    anchor:            str = "frame"
    anchor_perimeters: int = 4

    # First-layer features
    number_tab:       bool = True
    show_lcd:         bool = True
    no_leading_zeros: bool = False

    # Fan (0–100 %)
    fan_speed:       int = 100
    first_layer_fan: int = 0

    # Custom start / end G-code file paths (None = use built-in Prusa defaults)
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


# ── G-code generator ───────────────────────────────────────────────────────────

class Generator:
    """Translates a Config into a G-code string ready for a Prusa Core One."""

    # 7-segment digit definitions.
    # Segment order: top, top-right, bottom-right, bottom, bottom-left, top-left, middle
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
    _SEG_LEN = 2.0   # mm — length of each 7-segment bar
    _SEG_GAP = 1.0   # mm — gap between characters

    def __init__(self, cfg: Config,
                 start_template: Optional[str] = None,
                 end_template:   Optional[str] = None):
        self.cfg = cfg
        self._st  = _State()
        self._buf: list[str] = []
        self._start_tmpl = start_template if start_template is not None else DEFAULT_START_GCODE
        self._end_tmpl   = end_template   if end_template   is not None else DEFAULT_END_GCODE

        # Derived widths
        c = cfg
        self._lw  = c.nozzle_dia * (c.line_width_pct / 100.0)
        self._alw = c.nozzle_dia * (c.anchor_line_width_pct / 100.0)

        # Half-angle of the corner (radians from horizontal)
        self._half = math.radians((180.0 - c.corner_angle) / 2.0)

        # Total number of patterns
        n = round((c.la_end - c.la_start) / c.la_step)
        self._n_patterns = n + 1

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

    def _set_la(self, value: float):
        v = _r(value, _PA)
        self._emit(f"M900 K{v}")
        if self.cfg.show_lcd:
            self._emit(f"M117 LA {v}")

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
            y += 2.0 * spacing
            lr = not lr

    def _pattern(self, px: float, py: float, lh: float, lw: float, speed: float):
        """
        Draw one LA test pattern: wall_count nested V-shaped walls.

        The pattern opens to the right; the apex points left.  Each successive
        wall is shifted slightly inward (perpendicular to the leg direction) so
        the walls are nested.  Corners are where pressure-advance artifacts show.

        Layout for a 90° corner_angle (half-angle α = 45°):

            wall 0 (outermost):  start ──45°──> apex ──(−45°)──> end
            wall 1:              shifted inward by line_spacing
            ...
        """
        c = self.cfg
        cos_a = math.cos(self._half)
        sin_a = math.sin(self._half)
        spacing = lw - lh * (1.0 - math.pi / 4.0)

        for w in range(c.wall_count):
            # Perpendicular-right to the left-leg direction (cos α, sin α) is (sin α, −cos α).
            # This shifts successive walls toward the symmetry axis (inward).
            off    = w * spacing
            perp_x =  sin_a * off
            perp_y = -cos_a * off

            p0x = px + perp_x                            # start of left leg
            p0y = py + perp_y

            p1x = p0x + c.side_length * cos_a            # apex
            p1y = p0y + c.side_length * sin_a

            p2x = p1x + c.side_length * cos_a            # end of right leg
            p2y = p1y - c.side_length * sin_a

            self._travel(p0x, p0y)
            self._line(p1x, p1y, speed, lh, lw)
            self._line(p2x, p2y, speed, lh, lw)

    # ── 7-segment number rendering ─────────────────────────────────────────────

    def _digit_width(self) -> float:
        return self._SEG_LEN + self._SEG_GAP

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
                     lh: float, lw: float, spd: float):
        """Render a floating-point value as 7-segment glyphs starting at (x, y)."""
        text = f"{value:.{_PA}f}".rstrip("0").rstrip(".")
        if "." not in text and value != int(value):
            text = f"{value:.1f}"
        if self.cfg.no_leading_zeros and text.startswith("0."):
            text = text[1:]
        cx = x
        for ch in text:
            cx += self._draw_digit(cx, y, ch, lh, lw * 0.8, spd)

    # ── geometry helpers ───────────────────────────────────────────────────────

    def _pattern_width(self) -> float:
        """Bounding-box width of a single test pattern."""
        c       = self.cfg
        cos_a   = math.cos(self._half)
        sin_a   = math.sin(self._half)
        spacing = self._lw - c.layer_height * (1.0 - math.pi / 4.0)
        return 2.0 * c.side_length * cos_a + (c.wall_count - 1) * spacing * sin_a

    def _pattern_height(self) -> float:
        """Bounding-box height of a single test pattern."""
        c       = self.cfg
        sin_a   = math.sin(self._half)
        cos_a   = math.cos(self._half)
        spacing = self._lw - c.layer_height * (1.0 - math.pi / 4.0)
        return c.side_length * sin_a + (c.wall_count - 1) * spacing * cos_a

    def _num_tab_height(self) -> float:
        """Vertical space needed for the number labels."""
        return self._SEG_LEN * 2.0 + 3.0   # glyph height + padding

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

    # ── main generation entry point ────────────────────────────────────────────

    def generate(self) -> str:
        c  = self.cfg
        st = self._st

        lw_n  = self._lw    # normal line width (mm)
        lw_a  = self._alw   # anchor line width (mm)

        pat_w   = self._pattern_width()
        pat_h   = self._pattern_height()
        n_tab_h = self._num_tab_height() if c.number_tab else 0.0

        # Anchor margin: space between outer print edge and pattern area
        if c.anchor == "none":
            ax_margin = 2.0
            ay_margin = 2.0
        else:
            spacing_a = lw_a - c.first_layer_height * (1.0 - math.pi / 4.0)
            ax_margin = c.anchor_perimeters * spacing_a + c.pattern_spacing
            ay_margin = c.anchor_perimeters * spacing_a + c.pattern_spacing

        # Bottom margin must fit number labels
        ay_bottom = max(ay_margin, n_tab_h + ay_margin) if c.number_tab else ay_margin

        # Total occupied area on bed
        total_w = (2.0 * ax_margin
                   + self._n_patterns * pat_w
                   + (self._n_patterns - 1) * c.pattern_spacing)
        total_h = ay_bottom + ay_margin + pat_h

        if total_w > c.bed_x or total_h > c.bed_y:
            available  = c.bed_x - 2.0 * ax_margin
            per_slot   = available / self._n_patterns
            pat_budget = per_slot - c.pattern_spacing
            cos_a      = math.cos(self._half)
            safe_sl    = max(int(pat_budget / (2.0 * cos_a)) - 1, 5)
            print(
                f"WARNING: pattern area {total_w:.1f}×{total_h:.1f} mm "
                f"exceeds bed {c.bed_x}×{c.bed_y} mm.\n"
                f"  Fix: use --side-length {safe_sl} "
                f"(or a larger --la-step / narrower range)",
                file=sys.stderr,
            )

        # Bottom-left origin of the entire print area (centred on bed)
        orig_x = (c.bed_x - total_w) / 2.0
        orig_y = (c.bed_y - total_h) / 2.0

        # Pattern and label positions
        pat_start_x = orig_x + ax_margin
        pat_start_y = orig_y + ay_bottom
        num_y       = orig_y + ay_margin + 0.5

        # Derived template variables
        max_layer_z = _r(c.first_layer_height + (c.layer_count - 1) * c.layer_height, _Z)
        park_z      = _r(min(max_layer_z + 1.0, c.max_z), _Z)
        mbl_temp    = max(155, c.hotend_temp - 50)
        cool_fan    = "M106 S70  ; cool enclosure (PLA bed temp)" if c.bed_temp <= 60 else "M107"
        m555_x, m555_y, m555_w, m555_h = self._m555(orig_x, orig_y, total_w, total_h)

        tmpl_vars = dict(
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

        # ── header comments ────────────────────────────────────────────────────
        self._comment("=" * 60)
        self._comment("Linear Advance Calibration — Prusa Core One")
        self._comment("=" * 60)
        self._comment(f"LA range: {c.la_start} → {c.la_end}  step {c.la_step}"
                      f"  ({self._n_patterns} patterns)")
        self._comment(f"Nozzle: {c.nozzle_dia} mm   Filament: {c.filament_dia} mm")
        self._comment(f"Line width: {lw_n:.3f} mm ({c.line_width_pct} % of nozzle)")
        self._comment(f"Layer height: {c.layer_height} mm   "
                      f"First layer: {c.first_layer_height} mm")
        self._comment(f"Print speed: {c.print_speed} mm/s   "
                      f"First layer: {c.first_layer_speed} mm/s")
        self._comment(f"Hotend: {c.hotend_temp} °C   Bed: {c.bed_temp} °C")
        self._comment(f"Retraction: {c.retract_dist} mm @ {c.retract_speed} mm/s   "
                      f"Z-hop: {c.zhop} mm")
        self._comment(f"Pattern area: {total_w:.1f}×{total_h:.1f} mm  "
                      f"centred on {c.bed_x}×{c.bed_y} mm bed")
        self._blank()

        # ── start G-code (from template) ───────────────────────────────────────
        self._comment("─── START ───────────────────────────────────────────────")
        for line in _render(self._start_tmpl, tmpl_vars).splitlines():
            self._emit(line)
        self._blank()

        # ── calibration layers ─────────────────────────────────────────────────
        self._comment("─── CALIBRATION LAYERS ──────────────────────────────────")

        # First-layer fan (start template ends with fan off; set our preference here)
        fan0 = int(c.first_layer_fan / 100.0 * 255)
        self._emit(f"M106 S{fan0}  ; part-cooling fan — first layer ({c.first_layer_fan} %)")

        # ── layer 1 ────────────────────────────────────────────────────────────
        self._comment("─── LAYER 1 (first layer) ───────────────────────────────")
        z1 = c.first_layer_height
        self._emit(f"G0 Z{_r(z1, _Z)} F{int(c.travel_speed * 60)}")
        st.z = _r(z1, _Z)

        self._set_la(c.la_start)

        if c.anchor == "frame":
            self._comment("Anchor frame")
            self._anchor_frame(
                orig_x, orig_y, total_w, total_h,
                c.first_layer_height, lw_a, c.anchor_perimeters,
                c.first_layer_speed,
            )
        elif c.anchor == "layer":
            self._comment("Anchor layer (filled)")
            self._anchor_layer(
                orig_x, orig_y, total_w, total_h,
                c.first_layer_height, lw_a,
                c.first_layer_speed,
            )

        if c.number_tab:
            self._comment("Number labels")
            for i in range(self._n_patterns):
                if i % 2 != 0:
                    continue
                la_val = _r(c.la_start + i * c.la_step, _PA)
                nx = pat_start_x + i * (pat_w + c.pattern_spacing)
                self._draw_number(nx, num_y, la_val,
                                  c.first_layer_height, lw_n, c.first_layer_speed)

        self._comment("First layer patterns")
        for i in range(self._n_patterns):
            la_val = _r(c.la_start + i * c.la_step, _PA)
            self._set_la(la_val)
            px = pat_start_x + i * (pat_w + c.pattern_spacing)
            self._pattern(px, pat_start_y, c.first_layer_height, lw_n, c.first_layer_speed)

        # ── layers 2+ ──────────────────────────────────────────────────────────
        for layer in range(1, c.layer_count):
            z = _r(c.first_layer_height + layer * c.layer_height, _Z)
            self._comment(f"─── LAYER {layer + 1}  Z = {z} ──────────────────────────────")
            self._retract()
            self._emit(f"G0 Z{z} F{int(c.travel_speed * 60)}")
            st.z = z
            st.hopped = False
            self._unretract()

            if layer == 1:
                fan = int(c.fan_speed / 100.0 * 255)
                self._emit(f"M106 S{fan}  ; part-cooling fan from layer 2 ({c.fan_speed} %)")

            for i in range(self._n_patterns):
                la_val = _r(c.la_start + i * c.la_step, _PA)
                self._set_la(la_val)
                px = pat_start_x + i * (pat_w + c.pattern_spacing)
                self._pattern(px, pat_start_y, c.layer_height, lw_n, c.print_speed)

        # ── reset K, hand off to end template ──────────────────────────────────
        self._blank()
        self._comment("─── PATTERNS DONE ────────────────────────────────────────")
        self._set_la(c.la_start)   # reset K to start value

        # ── end G-code (from template) ─────────────────────────────────────────
        self._comment("─── END ─────────────────────────────────────────────────")
        for line in _render(self._end_tmpl, tmpl_vars).splitlines():
            self._emit(line)
        self._blank()
        self._comment("Done! Pick the corner with the sharpest finish.")
        self._comment("Bulge at corner tip = K too high.")
        self._comment("Gap / underextrusion before corner = K too low.")

        return "\n".join(self._buf) + "\n"


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate Linear Advance calibration G-code for Prusa Core One",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Workflow:\n"
            "  1. Coarse scan (default): pa_cal.py -o coarse.gcode\n"
            "     → prints K=0,1,2,3,4; pick the best-looking column\n"
            "  2. Fine scan around winner (e.g. best was K=2):\n"
            "     pa_cal.py --la-start 1.5 --la-end 2.5 --la-step 0.1"
            " --side-length 11 -o fine.gcode\n"
            "  3. PETG on MK4S:\n"
            "     pa_cal.py --printer MK4S --filament PETG -o petg.gcode"
        ),
    )

    p.add_argument(
        "--printer",
        type=str.upper,
        choices=list(PRINTER_PRESETS),
        default=_DEFAULT_PRINTER,
        metavar="MODEL",
        help=(
            "Printer model — sets bed size and max Z height. "
            f"Choices: {', '.join(PRINTER_PRESETS)}  "
            f"(default: {_DEFAULT_PRINTER}). "
            "Non-Core-One printers should also supply --start-gcode / --end-gcode."
        ),
    )
    p.add_argument(
        "--filament",
        type=str.upper,
        choices=list(FILAMENT_PRESETS),
        metavar="TYPE",
        help=(
            "Filament preset — sets hotend/bed temps, fan speed, and retraction. "
            "Explicit flags override the preset. "
            f"Choices: {', '.join(FILAMENT_PRESETS)}"
        ),
    )

    g = p.add_argument_group("Linear Advance")
    g.add_argument("--la-start", type=float, default=0.0, metavar="K",
                   help="Start K value")
    g.add_argument("--la-end",   type=float, default=4.0, metavar="K",
                   help="End K value")
    g.add_argument("--la-step",  type=float, default=1.0, metavar="K",
                   help="K increment per pattern (use 0.1 for fine-grained scan)")

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
    g.add_argument("--layer-count",        type=int,   default=4,    metavar="N")

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

    g = p.add_argument_group("Pattern geometry")
    g.add_argument("--wall-count",      type=int,   default=3,    metavar="N")
    g.add_argument("--side-length",     type=float, default=20.0, metavar="mm",
                   help="Length of each diagonal leg (larger = easier to read)")
    g.add_argument("--pattern-spacing", type=float, default=2.0,  metavar="mm")
    g.add_argument("--corner-angle",    type=float, default=90.0, metavar="deg",
                   help="Angle at pattern apex (90 = right angle corner)")

    g = p.add_argument_group("Anchor")
    g.add_argument("--anchor", choices=["frame", "layer", "none"], default="frame",
                   help="Anchor type printed on first layer for adhesion")
    g.add_argument("--anchor-perimeters", type=int, default=4, metavar="N")

    g = p.add_argument_group("Options")
    g.add_argument("--no-number-tab", dest="number_tab", action="store_false",
                   help="Suppress K-value labels on first layer")
    g.add_argument("--no-lcd", dest="show_lcd", action="store_false",
                   help="Suppress M117 display messages")
    g.add_argument("--no-leading-zeros", action="store_true",
                   help='Print "0.4" as ".4" in labels')
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
    p.add_argument("--binary", action="store_true",
                   help=(
                       "Output Prusa binary G-code v1 (.bgcode) instead of ASCII. "
                       "Use -o with a .bgcode extension, e.g. -o la_cal.bgcode. "
                       "Without -o, binary is written to stdout (pipe to a file)."
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
                   help="Filename to store on the printer "
                        "(default: basename of -o, or pa_cal.gcode / pa_cal.bgcode)")
    g.add_argument("--prusalink-print", action="store_true",
                   help="Start printing immediately after upload")

    return p


def main():
    parser = _build_parser()
    args   = parser.parse_args()

    _gcode_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcode")

    def _load(path: str) -> str:
        try:
            with open(path) as f:
                return f.read()
        except OSError as e:
            print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def _resolve_template(arg_path: Optional[str], printer_key: str, role: str) -> Optional[str]:
        """Load template: explicit arg > printer gcode file > None (built-in fallback)."""
        if arg_path is not None:
            return _load(arg_path)
        candidate = os.path.join(_gcode_dir, f"{printer_key.lower()}_{role}.gcode")
        if os.path.exists(candidate):
            return _load(candidate)
        return None  # Generator will use DEFAULT_START_GCODE / DEFAULT_END_GCODE

    # ── resolve printer preset (explicit args always win) ───────────────────────
    ppreset = PRINTER_PRESETS[args.printer]
    print(f"Printer: {args.printer}  "
          f"bed {ppreset['bed_x']:.0f}×{ppreset['bed_y']:.0f} mm  "
          f"max Z {ppreset['max_z']:.0f} mm",
          file=sys.stderr)

    start_tmpl = _resolve_template(args.start_gcode, args.printer, "start")
    end_tmpl   = _resolve_template(args.end_gcode,   args.printer, "end")

    # Warn only when actually falling back to the built-in Core One template
    missing = []
    if start_tmpl is None:
        missing.append("start-gcode")
    if end_tmpl is None:
        missing.append("end-gcode")
    if missing:
        print(
            f"WARNING: no {' or '.join(missing)} found for {args.printer} — "
            f"falling back to built-in Core One template "
            f"(parks at X242 Y211, Core One MBL commands).",
            file=sys.stderr,
        )

    # ── resolve filament preset (explicit args always win) ──────────────────────
    fpreset = FILAMENT_PRESETS.get(args.filament, {}) if args.filament else {}
    if fpreset:
        print(f"Filament: {args.filament}  "
              f"hotend {fpreset['hotend_temp']} °C  "
              f"bed {fpreset['bed_temp']} °C  "
              f"fan {fpreset['fan_speed']} %  "
              f"retract {fpreset['retract_dist']} mm",
              file=sys.stderr)

    def _p(arg_val, key: str, fallback, source: dict = fpreset):
        """Return arg_val if explicitly set, else preset value, else fallback."""
        return arg_val if arg_val is not None else source.get(key, fallback)

    cfg = Config(
        bed_x              = _p(args.bed_x,  "bed_x",  ppreset["bed_x"],  ppreset),
        bed_y              = _p(args.bed_y,  "bed_y",  ppreset["bed_y"],  ppreset),
        max_z              = _p(args.max_z,  "max_z",  ppreset["max_z"],  ppreset),
        nozzle_dia         = args.nozzle_dia,
        filament_dia       = args.filament_dia,
        bed_temp           = _p(args.bed_temp,    "bed_temp",    60),
        hotend_temp        = _p(args.hotend_temp,  "hotend_temp", 215),
        la_start           = args.la_start,
        la_end             = args.la_end,
        la_step            = args.la_step,
        first_layer_height = args.first_layer_height,
        layer_height       = args.layer_height,
        layer_count        = args.layer_count,
        print_speed        = args.print_speed,
        first_layer_speed  = args.first_layer_speed,
        travel_speed       = args.travel_speed,
        line_width_pct     = args.line_width_pct,
        extrusion_multiplier = args.extrusion_multiplier,
        retract_dist       = _p(args.retract_dist,    "retract_dist",    0.6),
        retract_speed      = args.retract_speed,
        unretract_speed    = args.unretract_speed,
        zhop               = args.zhop,
        wall_count         = args.wall_count,
        side_length        = args.side_length,
        pattern_spacing    = args.pattern_spacing,
        corner_angle       = args.corner_angle,
        anchor             = args.anchor,
        anchor_perimeters  = args.anchor_perimeters,
        number_tab         = args.number_tab,
        show_lcd           = args.show_lcd,
        no_leading_zeros   = args.no_leading_zeros,
        fan_speed          = _p(args.fan_speed,       "fan_speed",       100),
        first_layer_fan    = _p(args.first_layer_fan, "first_layer_fan", 0),
        start_gcode_file   = args.start_gcode,
        end_gcode_file     = args.end_gcode,
        output             = args.output,
    )

    gen = Generator(
        cfg,
        start_template = start_tmpl,
        end_template   = end_tmpl,
    )
    gcode = gen.generate()

    if args.binary:
        if args.output:
            n = _write_bgcode(gcode, args.output)
            lines = gcode.count("\n")
            print(f"Wrote {lines} G-code lines as bgcode ({n:,} bytes) → {args.output}",
                  file=sys.stderr)
        else:
            _write_bgcode(gcode, sys.stdout.buffer)
    else:
        if args.output:
            with open(args.output, "w") as f:
                f.write(gcode)
            lines = gcode.count("\n")
            print(f"Wrote {lines} lines ({len(gcode):,} bytes) → {args.output}",
                  file=sys.stderr)
        else:
            sys.stdout.write(gcode)

    # ── upload via PrusaLink (local network) ───────────────────────────────────
    if args.prusalink_url:
        if not args.prusalink_key:
            print("ERROR: --prusalink-key is required when --prusalink-url is set",
                  file=sys.stderr)
            sys.exit(1)

        if args.prusalink_filename:
            remote_name = args.prusalink_filename
        elif args.output:
            remote_name = os.path.basename(args.output)
        else:
            remote_name = "pa_cal.bgcode" if args.binary else "pa_cal.gcode"

        if args.binary:
            buf = io.BytesIO()
            _write_bgcode(gcode, buf)
            upload_data = buf.getvalue()
        else:
            upload_data = gcode.encode("utf-8")

        _upload_prusalink(
            upload_data,
            url=args.prusalink_url,
            key=args.prusalink_key,
            filename=remote_name,
            start_print=args.prusalink_print,
        )


if __name__ == "__main__":
    main()
