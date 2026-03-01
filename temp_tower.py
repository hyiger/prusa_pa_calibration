#!/usr/bin/env python3
"""
temp_tower.py — Temperature tower G-code generator for Prusa printers

Generates a multi-segment temperature tower inspired by the "Advanced temp tower"
design by Tronnic.  Each temperature segment features:

  - Short overhang wall  (overhang quality test, default 45°)
  - Long overhang wall   (overhang quality test, default 35°), with the
    temperature number rendered on its front face via layer cross-sections
  - Stringing-test cones in the open bridge gap
  - Bridging slab spanning the gap at the top of each segment

The temperature label is inset into the long-side wall's front face using
7-segment digit geometry: at each layer the front-face perimeter is printed
with gaps (unprinted segments) at digit bar positions, creating recessed
grooves ~2 line-widths deep that spell out the temperature number.

Usage:
    # PLA: scan 215 → 185 °C in 5 °C steps (default)
    python3 temp_tower.py -o temp_tower.gcode

    # PETG on Core One: 240 → 210 °C
    python3 temp_tower.py --filament PETG --temp-start 240 --temp-end 210 -o petg_tower.gcode

    # ABS on MK4S with custom range
    python3 temp_tower.py --printer MK4S --filament ABS --temp-start 250 --temp-end 220 -o abs_tower.gcode

    # Custom geometry
    python3 temp_tower.py --module-height 8 --bridge-length 25 -o custom_tower.gcode

Template variables available in --start-gcode / --end-gcode files:
    {bed_temp}      Bed temperature (°C)
    {hotend_temp}   Hotend temperature of the first segment (°C)
    {mbl_temp}      Nozzle temp used during mesh bed leveling (lower, no-ooze)
    {nozzle_dia}    Nozzle diameter (mm)
    {filament_dia}  Filament diameter (mm)
    {cool_fan}      "M106 S70" when bed_temp<=60 (enclosure cool), else "M107"
    {m555_x}        M555 print-area origin X
    {m555_y}        M555 print-area origin Y
    {m555_w}        M555 print-area width
    {m555_h}        M555 print-area height
    {park_z}        Safe Z height for end-of-print parking
    {max_layer_z}   Highest layer Z in the print
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
    _r, _Z, _XY, _E, _render,
    add_common_args, resolve_presets, handle_output,
)


# ── Temperature tower configuration ───────────────────────────────────────────

@dataclass
class Config(CommonConfig):
    """Temperature tower configuration — extends CommonConfig with tower params."""

    # Temperature sweep.  temp_start is printed at the bottom; the temperature
    # steps toward temp_end with each new segment.  Either direction works.
    temp_start: int = 215    # °C — bottom segment temperature
    temp_end:   int = 185    # °C — top segment temperature (may be higher or lower)
    temp_step:  float = 5.0  # °C per segment (always positive)

    # Module geometry (one module = one temperature segment)
    module_height: float = 10.0   # mm — height of each temperature segment
    module_depth:  float = 10.0   # mm — Y footprint (depth into the page)
    bridge_length: float = 30.0   # mm — bridge/stringing-test gap between walls
    bridge_thick:  float = 1.0    # mm — thickness of the bridge slab at segment top
    short_angle:   float = 45.0   # degrees — short-side overhang angle
    long_angle:    float = 35.0   # degrees — long-side overhang angle
    n_cones:       int   = 2      # stringing-test cones in the bridge gap
    base_thick:    float = 1.0    # mm — solid base slab thickness

    # Face text: 7-segment temperature number on the front face of the long wall
    label_tab:     bool  = True   # embed temperature label in the long-wall face


# ── Temperature tower generator ────────────────────────────────────────────────

class TowerGenerator(BaseGenerator):
    """Generates an advanced temperature tower calibration print."""

    def __init__(self, cfg: Config,
                 start_template: Optional[str] = None,
                 end_template:   Optional[str] = None):
        super().__init__(cfg, start_template, end_template)

    # ── inset face-text rendering ──────────────────────────────────────────────

    def _digit_gaps(self, temp: int, seg_len: float, z_base: float,
                    local_z: float, char_x0: float, char_adv: float,
                    lh: float, lw: float) -> list:
        """
        Return sorted, merged [(x0, x1)] gap intervals for this layer.

        Gaps are the X positions where the front-face perimeter should NOT be
        printed — i.e. the positions of each active 7-segment bar at local_z.
        Leaving these positions open creates inset (engraved) digit strokes.

        Horizontal bars are active for ±lh around their Z level (2 layers).
        Vertical bar notches are active between adjacent horizontal bars.
        """
        SL    = seg_len
        h_tol = lh * 3.0          # horizontal bar: active over ±3 layers (~0.6 mm)
        v_tol = lh * 3.0          # exclusion zone near horizontal bar levels
        nub   = lw * 4.0          # notch width for vertical bars (must be < SL/2)

        gaps: list = []
        cx = char_x0
        for ch in str(temp):
            if ch not in self._SEGS:
                cx += char_adv
                continue
            t, tr, br, bot, bl, tl, mid = self._SEGS[ch]
            z_bot = z_base
            z_mid = z_base + SL
            z_top = z_base + 2 * SL

            # Horizontal bars (full-width gap at their Z level)
            if bot and abs(local_z - z_bot) < h_tol:
                gaps.append((cx, cx + SL))
            if mid and abs(local_z - z_mid) < h_tol:
                gaps.append((cx, cx + SL))
            if t   and abs(local_z - z_top) < h_tol:
                gaps.append((cx, cx + SL))

            # Vertical bar notches (narrow gap on the appropriate X edge)
            if br and z_bot + v_tol < local_z < z_mid - v_tol:
                gaps.append((_r(cx + SL - nub, _XY), cx + SL))
            if tr and z_mid + v_tol < local_z < z_top - v_tol:
                gaps.append((_r(cx + SL - nub, _XY), cx + SL))
            if bl and z_bot + v_tol < local_z < z_mid - v_tol:
                gaps.append((cx, _r(cx + nub, _XY)))
            if tl and z_mid + v_tol < local_z < z_top - v_tol:
                gaps.append((cx, _r(cx + nub, _XY)))

            cx += char_adv

        # Sort and merge overlapping intervals
        gaps.sort()
        merged: list = []
        for g0, g1 in gaps:
            if merged and g0 < merged[-1][1] + 1e-4:
                merged[-1] = (merged[-1][0], max(merged[-1][1], g1))
            else:
                merged.append((g0, g1))
        return merged

    def _wall_layer_inset(self, x0: float, y0: float, width: float, depth: float,
                          gaps: list, lh: float, lw: float, speed: float,
                          n_perims: int = 2):
        """
        Solid rectangular wall layer with inset gaps on the front face (Y = y0+depth).

        Identical to _anchor_layer when gaps is empty.  When gaps are supplied
        all n_perims perimeter passes leave those X intervals unprinted on the
        front face, creating ~n_perims*spacing-deep grooves visible from the
        front.

        gaps:     sorted list of (x_start, x_end) intervals to omit.
        n_perims: number of perimeter loops (default 2; use 5 for deep inset text).
        """
        x1 = x0 + width
        y1 = y0 + depth
        spacing = lw - lh * (1.0 - math.pi / 4.0)

        for i in range(n_perims):
            off  = i * spacing
            bx0  = x0 + off
            by0  = y0 + off
            bx1  = x1 - off
            by1  = y1 - off

            # back + right faces (always solid)
            self._travel(bx0, by0)
            self._line(bx1, by0, speed, lh, lw)
            self._line(bx1, by1, speed, lh, lw)

            # front face right-to-left, with gaps clipped to [bx0, bx1]
            clipped = sorted(
                [(max(bx0, g0), min(bx1, g1))
                 for g0, g1 in gaps
                 if g0 < bx1 - 1e-4 and g1 > bx0 + 1e-4
                 and min(bx1, g1) - max(bx0, g0) > 1e-4],
                reverse=True,
            )
            cx = bx1
            for g0, g1 in clipped:
                if cx > g1 + 1e-4:
                    self._line(g1, by1, speed, lh, lw)
                cx = g0
                if cx > bx0 + 1e-4:
                    self._travel(cx, by1)
            if cx > bx0 + 1e-4:
                self._line(bx0, by1, speed, lh, lw)

            # left face
            self._line(bx0, by0, speed, lh, lw)

        # solid horizontal fill (spacing increment = full coverage)
        ix0 = x0 + n_perims * spacing
        iy0 = y0 + n_perims * spacing
        ix1 = x1 - n_perims * spacing
        iy1 = y1 - n_perims * spacing
        y   = iy0
        lr  = True
        while y <= iy1 + 1e-6:
            if lr:
                self._travel(ix0, y)
                self._line(ix1, y, speed, lh, lw)
            else:
                self._travel(ix1, y)
                self._line(ix0, y, speed, lh, lw)
            y  += spacing
            lr  = not lr

    def generate(self) -> str:
        c  = self.cfg
        st = self._st

        lw   = self._lw    # normal line width
        lw_a = self._alw   # anchor line width

        # ── segment planning ───────────────────────────────────────────────────
        direction     = 1 if c.temp_end >= c.temp_start else -1
        span          = abs(c.temp_end - c.temp_start)
        n_full_steps  = int(span / c.temp_step)
        temps         = [round(c.temp_start + i * direction * c.temp_step)
                         for i in range(n_full_steps + 1)]
        # Always land exactly on temp_end; warn when span is not a clean multiple
        if temps[-1] != c.temp_end:
            last_step = abs(c.temp_end - temps[-1])
            print(
                f"WARNING: span {span} °C is not a multiple of step "
                f"{c.temp_step} °C; last segment step is {last_step} °C "
                f"(ends at {c.temp_end} °C).",
                file=sys.stderr,
            )
            temps.append(c.temp_end)
        n_segs = len(temps)

        # ── layer counts ───────────────────────────────────────────────────────
        n_base_layers  = max(1, round(c.base_thick   / c.layer_height))
        layers_per_seg = max(1, round(c.module_height / c.layer_height))
        total_layers   = n_base_layers + n_segs * layers_per_seg
        max_layer_z    = _r(c.first_layer_height + (total_layers - 1) * c.layer_height, _Z)

        # ── geometry constants ─────────────────────────────────────────────────
        tan_short = math.tan(math.radians(c.short_angle))
        tan_long  = math.tan(math.radians(c.long_angle))

        # Maximum cross-section widths (at the top of each module)
        max_short_w = 5.0 + c.module_height / tan_short
        max_long_w  = 20.0 + c.module_height / tan_long

        # Cone geometry (matches SCAD defaults scaled to module_depth)
        cone_h      = c.module_height * 0.5
        start_d     = c.module_depth * 0.3   # base diameter of first cone
        end_d       = c.module_depth * 0.5   # base diameter of last cone
        if c.n_cones > 1:
            d_step      = (end_d - start_d) / (c.n_cones - 1)
            x_step_cone = (c.bridge_length - 10.0) / (c.n_cones - 1)
        else:
            d_step      = 0.0
            x_step_cone = 0.0

        # Face-text geometry: digits span 90 % of module_height, centred vertically.
        # seg_len = half the digit height (bar length); char_adv = char width + gap.
        # nub < seg_len/2 is enforced in _digit_gaps so left/right bars don't merge.
        seg_len    = c.module_height * 0.45         # bar length (digit height = 2*seg_len)
        z_base_txt = (c.module_height - 2.0 * seg_len) / 2.0   # bottom of glyph column
        char_adv   = seg_len * 1.2                 # character advance (bar + 20 % gap)

        # ── layout ────────────────────────────────────────────────────────────
        if c.anchor == "none":
            margin = 2.0
        else:
            spacing_a = lw_a - c.first_layer_height * (1.0 - math.pi / 4.0)
            margin    = c.anchor_perimeters * spacing_a + 1.0

        # Tower body spans: short_side (max_short_w) + bridge_gap + long_side (max_long_w)
        tower_w  = max_short_w + c.bridge_length + max_long_w
        anchor_w = tower_w + 2.0 * margin
        full_w   = anchor_w                        # no separate label pillar
        full_h   = c.module_depth + 2.0 * margin

        # Overflow warnings
        if full_w > c.bed_x or full_h > c.bed_y:
            print(
                f"WARNING: footprint {full_w:.1f}×{full_h:.1f} mm "
                f"exceeds bed {c.bed_x}×{c.bed_y} mm.",
                file=sys.stderr,
            )
        if max_layer_z > c.max_z:
            print(
                f"WARNING: tower height {max_layer_z} mm exceeds max Z {c.max_z} mm.",
                file=sys.stderr,
            )

        # Centre on bed
        orig_x = (c.bed_x - full_w) / 2.0
        orig_y = (c.bed_y - full_h) / 2.0

        # Absolute Y span
        y0      = orig_y + margin
        y1      = y0 + c.module_depth
        cy_cone = (y0 + y1) / 2.0   # cone Y centre

        # Absolute X anchors (constant throughout print)
        short_x1 = orig_x + margin + max_short_w
        long_x0  = short_x1 + c.bridge_length   # left edge of long-side wall

        tmpl_vars = self._base_tmpl_vars(max_layer_z, orig_x, orig_y, full_w, full_h)

        # ── header comments ────────────────────────────────────────────────────
        self._comment("=" * 60)
        self._comment("Temperature Tower Calibration")
        self._comment("=" * 60)
        self._comment(
            f"Temp range: {c.temp_start} → {c.temp_end} °C  "
            f"step {c.temp_step} °C  ({n_segs} segments)"
        )
        self._comment(
            f"Module: {c.module_height} mm tall  {c.module_depth} mm deep  "
            f"bridge {c.bridge_length} mm  ({layers_per_seg} layers/seg @ {c.layer_height} mm)"
        )
        self._comment(
            f"Walls: short {c.short_angle}°  long {c.long_angle}°  "
            f"{c.n_cones} stringing cone(s)  bridge slab {c.bridge_thick} mm"
        )
        self._comment(f"Nozzle: {c.nozzle_dia} mm   Filament: {c.filament_dia} mm")
        self._comment(
            f"Bed: {c.bed_temp} °C   "
            f"Retraction: {c.retract_dist} mm @ {c.retract_speed} mm/s"
        )
        self._blank()

        # ── start G-code ───────────────────────────────────────────────────────
        self._comment("─── START ───────────────────────────────────────────────")
        for line in _render(self._start_tmpl, tmpl_vars).splitlines():
            self._emit(line)
        self._blank()

        # First-layer fan
        fan0 = int(c.first_layer_fan / 100.0 * 255)
        self._emit(f"M106 S{fan0}  ; part-cooling fan — first layer ({c.first_layer_fan} %)")

        # ── layer loop ─────────────────────────────────────────────────────────
        for layer_idx in range(total_layers):
            is_first = layer_idx == 0
            is_base  = layer_idx < n_base_layers

            if is_first:
                lh    = c.first_layer_height
                speed = c.first_layer_speed
                z     = lh
            else:
                lh    = c.layer_height
                speed = c.print_speed
                z     = _r(c.first_layer_height + layer_idx * c.layer_height, _Z)

            # Determine segment index and local Z within segment
            if is_base:
                temp         = temps[0]
                seg_idx      = 0
                layer_in_seg = 0
                local_z      = 0.0
            else:
                seg_layer    = layer_idx - n_base_layers
                seg_idx      = min(seg_layer // layers_per_seg, n_segs - 1)
                layer_in_seg = seg_layer % layers_per_seg
                local_z      = _r(layer_in_seg * c.layer_height, _Z)
                temp         = temps[seg_idx]

            self._comment(
                f"─── LAYER {layer_idx + 1}  Z={_r(z, _Z)}  "
                f"{'BASE' if is_base else f'seg {seg_idx}  {temp} °C'}  {'─' * 20}"
            )

            # Z movement and temperature
            if is_first:
                self._emit(f"G0 Z{_r(z, _Z)} F{int(c.travel_speed * 60)}")
                st.z = _r(z, _Z)
                self._emit(f"M109 S{temp}  ; wait for {temp} °C")
                if c.show_lcd:
                    self._emit(f"M117 Temp: {temp}C")
            else:
                self._retract()
                self._emit(f"G0 Z{z} F{int(c.travel_speed * 60)}")
                st.z = z
                st.hopped = False
                self._unretract()

                if layer_idx == 1:
                    fan = int(c.fan_speed / 100.0 * 255)
                    self._emit(
                        f"M106 S{fan}  ; part-cooling fan from layer 2 ({c.fan_speed} %)"
                    )

                # Temperature change at first layer of each new segment
                if not is_base and layer_in_seg == 0 and seg_idx > 0:
                    self._emit(f"M104 S{temp}  ; segment {seg_idx}: {temp} °C")
                    if c.show_lcd:
                        self._emit(f"M117 Temp: {temp}C")

            # Anchor: first overall layer only, around tower body footprint
            if is_first:
                if c.anchor == "frame":
                    self._comment("Anchor frame")
                    self._anchor_frame(
                        orig_x, orig_y, anchor_w, full_h,
                        lh, lw_a, c.anchor_perimeters, speed,
                    )
                elif c.anchor == "layer":
                    self._comment("Anchor layer (filled)")
                    self._anchor_layer(orig_x, orig_y, anchor_w, full_h, lh, lw_a, speed)

            # ── shapes ────────────────────────────────────────────────────────
            if is_base:
                # Solid base slab spanning the full X extent at local_z=0
                base_x0 = short_x1 - 5.0
                base_w  = 5.0 + c.bridge_length + 20.0
                self._anchor_layer(base_x0, y0, base_w, c.module_depth, lh, lw, speed)

            else:
                # Per-layer widths (walls grow outward as local_z increases)
                sx0 = _r(short_x1 - (5.0 + local_z / tan_short), _XY)
                lx1 = _r(long_x0  + 20.0 + local_z / tan_long,   _XY)

                # Short overhang wall — solid filled rectangle, growing leftward
                short_w = short_x1 - sx0
                if short_w > lw:
                    self._anchor_layer(sx0, y0, short_w, c.module_depth, lh, lw, speed)

                # Long overhang wall — growing rightward, with optional inset label
                long_w = lx1 - long_x0
                if long_w > lw:
                    if c.label_tab:
                        n_chars = len(str(temp))
                        text_w  = n_chars * char_adv
                        char_x0 = _r(long_x0 + (20.0 - text_w) / 2.0, _XY)
                        gaps    = self._digit_gaps(
                            temp, seg_len, z_base_txt, local_z,
                            char_x0, char_adv, lh, lw,
                        )
                        self._wall_layer_inset(
                            long_x0, y0, long_w, c.module_depth,
                            gaps, lh, lw, speed, n_perims=5,
                        )
                    else:
                        self._anchor_layer(
                            long_x0, y0, long_w, c.module_depth, lh, lw, speed
                        )

                # Stringing-test cones (in bridge gap, lower half of each segment)
                if local_z < cone_h:
                    for cone_c in range(c.n_cones):
                        r_base  = (start_d + cone_c * d_step) / 2.0
                        r       = r_base * (1.0 - local_z / cone_h)
                        cx_cone = _r(short_x1 + 5.0 + cone_c * x_step_cone, _XY)
                        self._circle(cx_cone, cy_cone, r, speed, lh, lw)

                # Bridge slab — solid fill spanning the gap at the top of each segment
                if local_z >= c.module_height - c.bridge_thick - 1e-6:
                    self._anchor_layer(
                        short_x1, y0, c.bridge_length, c.module_depth, lh, lw, speed
                    )

        # ── end G-code ─────────────────────────────────────────────────────────
        self._blank()
        self._comment("─── END ─────────────────────────────────────────────────")
        for line in _render(self._end_tmpl, tmpl_vars).splitlines():
            self._emit(line)
        self._blank()
        self._comment("Done!  Examine each segment for overhang / stringing / bridging quality.")
        self._comment(f"Bottom segment = {temps[0]} °C, top = {temps[-1]} °C.")

        return "\n".join(self._buf) + "\n"


# ── CLI ────────────────────────────────────────────────────────────────────────

def _positive_float(value: str) -> float:
    """argparse type that accepts only strictly positive floats."""
    try:
        v = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {value!r}")
    if v <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive number, got {value!r}"
        )
    return v


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate advanced temperature tower G-code for Prusa printers",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Workflow:\n"
            "  1. Run a wide scan first (default: 215→185 °C, 5 °C steps).\n"
            "  2. Examine each segment: overhang quality, stringing, bridging.\n"
            "  3. Identify the best-looking segment and use that temperature.\n"
            "  4. Optionally run a fine scan (--temp-step 2) centred on your winner.\n"
            "\n"
            "Examples:\n"
            "  PETG on Core One:  temp_tower.py --filament PETG -o petg_tower.gcode\n"
            "  ABS on MK4S:       temp_tower.py --printer MK4S --filament ABS -o abs_tower.gcode\n"
            "  Fine scan around 230:  temp_tower.py --temp-start 235 --temp-end 225 "
            "--temp-step 2 -o fine.gcode"
        ),
    )

    add_common_args(p, "temp_tower")

    g = p.add_argument_group("Temperature tower")
    g.add_argument("--temp-start", type=int, default=None, metavar="°C",
                   help="Bottom segment temperature "
                        "(default: from --filament preset, or 215)")
    g.add_argument("--temp-end",   type=int, default=None, metavar="°C",
                   help="Top segment temperature "
                        "(default: temp-start − 30)")
    g.add_argument("--temp-step",  type=_positive_float, default=5.0, metavar="°C",
                   help="Temperature change per segment (always positive)")
    g.add_argument("--module-height", type=float, default=10.0, metavar="mm",
                   help="Height of each temperature segment")
    g.add_argument("--module-depth",  type=float, default=10.0, metavar="mm",
                   help="Depth (Y footprint) of each segment")
    g.add_argument("--bridge-length", type=float, default=30.0, metavar="mm",
                   help="Length of bridge / stringing-test area between walls")
    g.add_argument("--bridge-thick",  type=float, default=1.0,  metavar="mm",
                   help="Thickness of bridge slab printed at top of each segment")
    g.add_argument("--short-angle",   type=float, default=45.0, metavar="deg",
                   help="Overhang angle of short-side wall")
    g.add_argument("--long-angle",    type=float, default=35.0, metavar="deg",
                   help="Overhang angle of long-side wall")
    g.add_argument("--n-cones",       type=int,   default=2,    metavar="N",
                   help="Number of stringing-test cones in bridge gap")
    g.add_argument("--base-thick",    type=float, default=1.0,  metavar="mm",
                   help="Thickness of solid base slab")
    g.add_argument("--no-label-tab", dest="label_tab", action="store_false",
                   help="Disable temperature labels on long-wall face (default: enabled)")

    return p


def main():
    parser = _build_parser()
    args   = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    ppreset, fpreset, start_tmpl, end_tmpl = resolve_presets(args, script_dir)

    def _p(arg_val, key: str, fallback, source: dict = fpreset):
        """Return arg_val if explicitly set, else preset value, else fallback."""
        return arg_val if arg_val is not None else source.get(key, fallback)

    # Resolve temperature range.
    # Priority: --temp-start > --hotend-temp > filament preset > 215
    temp_start = (args.temp_start
                  if args.temp_start is not None
                  else _p(args.hotend_temp, "hotend_temp", 215))
    temp_end   = args.temp_end if args.temp_end is not None else temp_start - 30

    cfg = Config(
        bed_x              = _p(args.bed_x, "bed_x", ppreset["bed_x"], ppreset),
        bed_y              = _p(args.bed_y, "bed_y", ppreset["bed_y"], ppreset),
        max_z              = _p(args.max_z, "max_z", ppreset["max_z"], ppreset),
        nozzle_dia         = args.nozzle_dia,
        filament_dia       = args.filament_dia,
        bed_temp           = _p(args.bed_temp,   "bed_temp",   60),
        hotend_temp        = temp_start,
        temp_start         = temp_start,
        temp_end           = temp_end,
        temp_step          = args.temp_step,
        first_layer_height = args.first_layer_height,
        layer_height       = args.layer_height,
        module_height      = args.module_height,
        module_depth       = args.module_depth,
        bridge_length      = args.bridge_length,
        bridge_thick       = args.bridge_thick,
        short_angle        = args.short_angle,
        long_angle         = args.long_angle,
        n_cones            = args.n_cones,
        base_thick         = args.base_thick,
        print_speed        = args.print_speed,
        first_layer_speed  = args.first_layer_speed,
        travel_speed       = args.travel_speed,
        line_width_pct     = args.line_width_pct,
        extrusion_multiplier = args.extrusion_multiplier,
        retract_dist       = _p(args.retract_dist,    "retract_dist",    0.6),
        retract_speed      = args.retract_speed,
        unretract_speed    = args.unretract_speed,
        zhop               = args.zhop,
        label_tab          = args.label_tab,
        anchor             = args.anchor,
        anchor_perimeters  = args.anchor_perimeters,
        show_lcd           = args.show_lcd,
        fan_speed          = _p(args.fan_speed,       "fan_speed",       100),
        first_layer_fan    = _p(args.first_layer_fan, "first_layer_fan", 0),
        start_gcode_file   = args.start_gcode,
        end_gcode_file     = args.end_gcode,
        output             = args.output,
    )

    gen   = TowerGenerator(cfg, start_template=start_tmpl, end_template=end_tmpl)
    gcode = gen.generate()

    handle_output(gcode, args, "temp_tower")


if __name__ == "__main__":
    main()
