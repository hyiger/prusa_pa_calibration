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
import math
import os
import sys
from dataclasses import dataclass
from typing import Optional

from _common import (
    CommonConfig, BaseGenerator,
    FILAMENT_PRESETS, PRINTER_PRESETS, _DEFAULT_PRINTER,
    _r, _PA, _Z, _XY, _E, _render,
    add_common_args, resolve_presets, handle_output,
)


# ── PA-specific configuration ──────────────────────────────────────────────────

@dataclass
class Config(CommonConfig):
    """PA calibration configuration — extends CommonConfig with LA sweep params."""

    # Linear Advance range.
    # Default = coarse scan K 0–4 in steps of 1 (5 patterns, fits 220 mm bed).
    # After picking a winner, re-run with a fine step, e.g.:
    #   --la-start 1.5 --la-end 2.5 --la-step 0.1 --side-length 11
    la_start: float = 0.0
    la_end:   float = 4.0
    la_step:  float = 1.0

    # Number of layers to repeat each pattern
    layer_count: int = 4

    # Pattern geometry.
    # side_length=20 fits 5 patterns on a 220 mm bed.
    # Decrease for fine-step scans with more patterns.
    wall_count:      int   = 3
    side_length:     float = 20.0
    pattern_spacing: float = 2.0
    corner_angle:    float = 90.0  # degrees at apex

    # First-layer label features
    number_tab:       bool = True
    no_leading_zeros: bool = False


# ── PA-specific generator ──────────────────────────────────────────────────────

class Generator(BaseGenerator):
    """Translates a Config into a G-code string ready for a Prusa Core One."""

    # 7-segment digit definitions.
    # Segment order: top, top-right, bottom-right, bottom, bottom-left, top-left, middle
    def __init__(self, cfg: Config,
                 start_template: Optional[str] = None,
                 end_template:   Optional[str] = None):
        super().__init__(cfg, start_template, end_template)
        c = cfg
        # Half-angle of the corner (radians from horizontal)
        self._half = math.radians((180.0 - c.corner_angle) / 2.0)
        # Total number of patterns
        n = round((c.la_end - c.la_start) / c.la_step)
        self._n_patterns = n + 1

    # ── PA-specific motion ─────────────────────────────────────────────────────

    def _set_la(self, value: float):
        v = _r(value, _PA)
        self._emit(f"M900 K{v}")
        if self.cfg.show_lcd:
            self._emit(f"M117 LA {v}")

    # ── PA-specific drawing ────────────────────────────────────────────────────

    def _pattern(self, px: float, py: float, lh: float, lw: float, speed: float):
        """
        Draw one LA test pattern: wall_count nested V-shaped walls.

        The pattern opens to the right; the apex points left.  Each successive
        wall is shifted slightly inward (perpendicular to the leg direction) so
        the walls are nested.  Corners are where pressure-advance artifacts show.
        """
        c = self.cfg
        cos_a = math.cos(self._half)
        sin_a = math.sin(self._half)
        spacing = lw - lh * (1.0 - math.pi / 4.0)

        for w in range(c.wall_count):
            off    = w * spacing
            perp_x =  sin_a * off
            perp_y = -cos_a * off

            p0x = px + perp_x
            p0y = py + perp_y

            p1x = p0x + c.side_length * cos_a
            p1y = p0y + c.side_length * sin_a

            p2x = p1x + c.side_length * cos_a
            p2y = p1y - c.side_length * sin_a

            self._travel(p0x, p0y)
            self._line(p1x, p1y, speed, lh, lw)
            self._line(p2x, p2y, speed, lh, lw)

    # ── PA-specific geometry ───────────────────────────────────────────────────

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

        # Template variables
        max_layer_z = _r(c.first_layer_height + (c.layer_count - 1) * c.layer_height, _Z)
        tmpl_vars   = self._base_tmpl_vars(max_layer_z, orig_x, orig_y, total_w, total_h)

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
                                  c.first_layer_height, lw_n, c.first_layer_speed,
                                  no_leading_zeros=c.no_leading_zeros)

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

    add_common_args(p, "pa_cal")

    g = p.add_argument_group("Linear Advance")
    g.add_argument("--la-start", type=float, default=0.0, metavar="K",
                   help="Start K value")
    g.add_argument("--la-end",   type=float, default=4.0, metavar="K",
                   help="End K value")
    g.add_argument("--la-step",  type=float, default=1.0, metavar="K",
                   help="K increment per pattern (use 0.1 for fine-grained scan)")

    g = p.add_argument_group("Pattern geometry")
    g.add_argument("--layer-count",     type=int,   default=4,    metavar="N",
                   help="Number of layers to print each pattern")
    g.add_argument("--wall-count",      type=int,   default=3,    metavar="N")
    g.add_argument("--side-length",     type=float, default=20.0, metavar="mm",
                   help="Length of each diagonal leg (larger = easier to read)")
    g.add_argument("--pattern-spacing", type=float, default=2.0,  metavar="mm")
    g.add_argument("--corner-angle",    type=float, default=90.0, metavar="deg",
                   help="Angle at pattern apex (90 = right angle corner)")

    g = p.add_argument_group("Labels")
    g.add_argument("--no-number-tab", dest="number_tab", action="store_false",
                   help="Suppress K-value labels on first layer")
    g.add_argument("--no-leading-zeros", action="store_true",
                   help='Print "0.4" as ".4" in labels')

    return p


def main():
    parser = _build_parser()
    args   = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    ppreset, fpreset, start_tmpl, end_tmpl = resolve_presets(args, script_dir)

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

    gen   = Generator(cfg, start_template=start_tmpl, end_template=end_tmpl)
    gcode = gen.generate()

    handle_output(gcode, args, "pa_cal")


if __name__ == "__main__":
    main()
