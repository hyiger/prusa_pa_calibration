#!/usr/bin/env python3
"""
temp_tower.py — Temperature tower G-code generator for Prusa printers

Generates a tall rectangular tower split into horizontal segments, each printed
at a different hotend temperature.  Print the tower, then examine the surface
quality and bridging at each segment to find your ideal printing temperature.

Usage:
    # PLA: scan 215 → 185 °C in 5 °C steps (default)
    python3 temp_tower.py -o temp_tower.gcode

    # PETG on Core One: 240 → 210 °C
    python3 temp_tower.py --filament PETG --temp-start 240 --temp-end 210 -o petg_tower.gcode

    # ABS on MK4S with custom range
    python3 temp_tower.py --printer MK4S --filament ABS --temp-start 250 --temp-end 220 -o abs_tower.gcode

    # Finer steps
    python3 temp_tower.py --temp-start 220 --temp-end 200 --temp-step 2 -o fine_tower.gcode

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

    # Tower geometry
    segment_height: float = 5.0   # mm — height of each temperature segment
    tower_width:    float = 20.0  # mm — X footprint of the tower body
    tower_depth:    float = 20.0  # mm — Y footprint of the tower body
    wall_count:     int   = 2     # perimeter walls per layer
    label_tab:      bool  = True  # draw temperature label at each segment transition


# ── Temperature tower generator ────────────────────────────────────────────────

class TowerGenerator(BaseGenerator):
    """Generates a temperature tower calibration print."""

    def __init__(self, cfg: Config,
                 start_template: Optional[str] = None,
                 end_template:   Optional[str] = None):
        super().__init__(cfg, start_template, end_template)

    def generate(self) -> str:
        c  = self.cfg
        st = self._st

        lw   = self._lw    # normal line width
        lw_a = self._alw   # anchor line width

        # ── segment planning ───────────────────────────────────────────────────
        direction     = 1 if c.temp_end >= c.temp_start else -1
        span          = abs(c.temp_end - c.temp_start)
        n_full_steps  = int(span / c.temp_step)   # steps that stay within range
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

        # Layers per segment (first-layer height counts as one layer)
        layers_per_seg = max(1, round(c.segment_height / c.layer_height))
        total_layers   = n_segs * layers_per_seg   # includes first layer (idx 0)

        max_layer_z = _r(
            c.first_layer_height + (total_layers - 1) * c.layer_height, _Z
        )

        # ── layout ────────────────────────────────────────────────────────────
        if c.anchor == "none":
            margin = 2.0
        else:
            spacing_a = lw_a - c.first_layer_height * (1.0 - math.pi / 4.0)
            margin    = c.anchor_perimeters * spacing_a + 1.0  # anchor + 1 mm gap

        tower_area_w = c.tower_width + 2.0 * margin   # anchor footprint (no label)

        # Label tab: 7-segment temperature digits printed to the right of the tower
        if c.label_tab:
            max_chars = len(str(max(abs(c.temp_start), abs(c.temp_end))))
            tab_w     = max_chars * self._digit_width() + self._SEG_GAP
            label_gap = 2.0
        else:
            tab_w = label_gap = 0.0

        full_w = tower_area_w + label_gap + tab_w
        full_h = c.tower_depth + 2.0 * margin

        # Warn if tower exceeds bed
        if full_w > c.bed_x or full_h > c.bed_y:
            print(
                f"WARNING: tower footprint {full_w:.1f}×{full_h:.1f} mm "
                f"exceeds bed {c.bed_x}×{c.bed_y} mm.",
                file=sys.stderr,
            )

        tower_height_mm = _r(
            c.first_layer_height + (total_layers - 1) * c.layer_height, _Z
        )
        if tower_height_mm > c.max_z:
            print(
                f"WARNING: tower height {tower_height_mm} mm exceeds max Z {c.max_z} mm.",
                file=sys.stderr,
            )

        # Centre on bed
        orig_x   = (c.bed_x - full_w) / 2.0
        orig_y   = (c.bed_y - full_h) / 2.0
        tower_x0 = orig_x + margin
        tower_y0 = orig_y + margin
        tower_x1 = tower_x0 + c.tower_width
        tower_y1 = tower_y0 + c.tower_depth

        # Label position: right of tower body, vertically centred in tower depth
        if c.label_tab:
            label_x = tower_x1 + label_gap
            label_y = tower_y0 + (c.tower_depth - self._SEG_LEN * 2.0) / 2.0
        else:
            label_x = label_y = 0.0   # unused

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
            f"Segment height: {c.segment_height} mm  "
            f"({layers_per_seg} layers @ {c.layer_height} mm)"
        )
        self._comment(f"Tower: {c.tower_width}×{c.tower_depth} mm footprint  "
                      f"total height {tower_height_mm} mm")
        self._comment(f"Nozzle: {c.nozzle_dia} mm   Filament: {c.filament_dia} mm")
        self._comment(f"Bed: {c.bed_temp} °C   "
                      f"Retraction: {c.retract_dist} mm @ {c.retract_speed} mm/s")
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
        wall_spacing = lw - c.layer_height * (1.0 - math.pi / 4.0)
        wall_spacing_fl = lw - c.first_layer_height * (1.0 - math.pi / 4.0)

        for layer_idx in range(total_layers):
            seg_idx  = layer_idx // layers_per_seg
            temp     = temps[seg_idx]
            is_first = layer_idx == 0

            if is_first:
                lh    = c.first_layer_height
                speed = c.first_layer_speed
                z     = lh
            else:
                lh    = c.layer_height
                speed = c.print_speed
                z     = _r(c.first_layer_height + layer_idx * c.layer_height, _Z)

            self._comment(
                f"─── LAYER {layer_idx + 1}  Z={_r(z, _Z)}  "
                f"seg {seg_idx}  {temp} °C {'─' * 20}"
            )

            if is_first:
                self._emit(f"G0 Z{_r(z, _Z)} F{int(c.travel_speed * 60)}")
                st.z = _r(z, _Z)
                # Wait for first segment temperature before printing
                self._emit(f"M109 S{temp}  ; wait for {temp} °C (segment 0)")
                if c.show_lcd:
                    self._emit(f"M117 Temp: {temp}C")
            else:
                self._retract()
                self._emit(f"G0 Z{z} F{int(c.travel_speed * 60)}")
                st.z = z
                st.hopped = False
                self._unretract()

                if layer_idx == 1:
                    # Activate part-cooling fan from layer 2
                    fan = int(c.fan_speed / 100.0 * 255)
                    self._emit(f"M106 S{fan}  ; part-cooling fan from layer 2 ({c.fan_speed} %)")

                # Emit temperature change at the first layer of each new segment
                if layer_idx % layers_per_seg == 0:
                    self._emit(f"M104 S{temp}  ; segment {seg_idx}: {temp} °C")
                    if c.show_lcd:
                        self._emit(f"M117 Temp: {temp}C")

            # Anchor on first layer only
            if is_first:
                if c.anchor == "frame":
                    self._comment("Anchor frame")
                    self._anchor_frame(
                        orig_x, orig_y, tower_area_w, full_h,
                        lh, lw_a, c.anchor_perimeters, speed,
                    )
                elif c.anchor == "layer":
                    self._comment("Anchor layer (filled)")
                    self._anchor_layer(orig_x, orig_y, tower_area_w, full_h, lh, lw_a, speed)

            # Tower walls: wall_count concentric rectangles
            ws = wall_spacing_fl if is_first else wall_spacing
            for w in range(c.wall_count):
                off = w * ws
                wx0 = tower_x0 + off
                wy0 = tower_y0 + off
                wx1 = tower_x1 - off
                wy1 = tower_y1 - off
                if wx0 >= wx1 or wy0 >= wy1:
                    break
                self._perimeter(wx0, wy0, wx1, wy1, speed, lh, lw)

            # Temperature label at the first layer of each segment
            if c.label_tab and layer_idx % layers_per_seg == 0:
                self._comment(f"Label: {temp} °C")
                self._draw_number(label_x, label_y, float(temp), lh, lw, speed)

        # ── end G-code ─────────────────────────────────────────────────────────
        self._blank()
        self._comment("─── END ─────────────────────────────────────────────────")
        for line in _render(self._end_tmpl, tmpl_vars).splitlines():
            self._emit(line)
        self._blank()
        self._comment("Done! Examine each segment for surface quality.")
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
        description="Generate temperature tower calibration G-code for Prusa printers",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Workflow:\n"
            "  1. Run a wide scan first (default: 215→185 °C, 5 °C steps).\n"
            "  2. Examine each segment under good lighting.\n"
            "     Look for: stringing, bridging quality, layer adhesion, surface smoothness.\n"
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
    g.add_argument("--segment-height", type=float, default=5.0, metavar="mm",
                   help="Height of each temperature segment")
    g.add_argument("--tower-width",    type=float, default=20.0, metavar="mm",
                   help="Tower footprint width (X)")
    g.add_argument("--tower-depth",    type=float, default=20.0, metavar="mm",
                   help="Tower footprint depth (Y)")
    g.add_argument("--wall-count",     type=int,   default=2,    metavar="N",
                   help="Number of perimeter walls per layer")
    g.add_argument("--no-label-tab", dest="label_tab", action="store_false",
                   help="Disable per-segment temperature labels (default: enabled)")

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
        segment_height     = args.segment_height,
        print_speed        = args.print_speed,
        first_layer_speed  = args.first_layer_speed,
        travel_speed       = args.travel_speed,
        line_width_pct     = args.line_width_pct,
        extrusion_multiplier = args.extrusion_multiplier,
        retract_dist       = _p(args.retract_dist,    "retract_dist",    0.6),
        retract_speed      = args.retract_speed,
        unretract_speed    = args.unretract_speed,
        zhop               = args.zhop,
        tower_width        = args.tower_width,
        tower_depth        = args.tower_depth,
        wall_count         = args.wall_count,
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
