# CLAUDE.md — pa_cal.py

## Project overview

Single-file Python CLI (`pa_cal.py`) that generates Linear Advance (PA/K) calibration G-code for the Prusa Core One. No external dependencies. Targets Python 3.10+.

## Architecture

```
Config (dataclass)       — all tunable parameters, with defaults
 └─► Generator           — produces G-code string
      ├─ _State          — tracks live printer position + retract/hop state
      ├─ pattern math    — V-shape geometry, wall nesting, spacing formulas
      ├─ anchor methods  — _anchor_frame / _anchor_layer
      └─ 7-segment labels— _draw_digit / _draw_number

_render(template, vars)  — {var} substitution for start/end G-code templates
_write_bgcode(text, dst) — Prusa binary G-code v1 writer (stdlib only)
main() / _build_parser() — CLI entry point
```

## Key conventions

- **Rounding**: use `_r(value, places)` everywhere. Constants: `_PA=4`, `_Z=3`, `_XY=4`, `_E=5`. Never write raw `round()` calls in G-code output paths.
- **Speeds**: internal units are mm/s; G-code requires mm/min, so always multiply by 60 when emitting `F` values.
- **Extrusion**: always compute via `_e_amount(dist, lh, lw)` — it accounts for filament diameter and `extrusion_multiplier`.
- **Wall spacing formula**: `lw - lh * (1 - π/4)` — matches Slic3r/PrusaSlicer. Used in `_pattern`, `_anchor_frame`, `_anchor_layer`, and the geometry helpers.
- **Retraction**: `_retract()` / `_unretract()` are idempotent. Call freely; they no-op if already in the right state. `_travel()` calls them automatically for moves > 2 mm.
- **LA range**: patterns are indexed `0..n_patterns-1`. K value for pattern `i` = `_r(la_start + i * la_step, _PA)`. Always use this formula — do not accumulate K across iterations.

## Geometry

The test pattern is a V-shape (two diagonal legs meeting at an apex pointing left). `corner_angle` is the angle at the apex; `half` = `(180 - corner_angle) / 2` radians is used for all trig.

Successive walls offset inward perpendicular to the left-leg direction:
- perpendicular direction: `(sin α, -cos α)`
- offset per wall: `w * spacing`

Pattern bounding box:
- width: `2 * side_length * cos(half) + (wall_count-1) * spacing * sin(half)`
- height: `side_length * sin(half) + (wall_count-1) * spacing * cos(half)`

## Start / end G-code templates

Built-in templates live in `DEFAULT_START_GCODE` / `DEFAULT_END_GCODE`. They are derived from PrusaSlicer's Core One profile with conditionals resolved. Custom templates are loaded from files passed via `--start-gcode` / `--end-gcode`.

Template variables are substituted by `_render()`. Only `{lowercase_identifier}` markers are replaced; unknown markers and PrusaSlicer `{if ...}` expressions are left untouched.

## Binary G-code (.bgcode)

Implemented in `_write_bgcode()` using only `struct` and `zlib`. Format: file header + PrinterMetadata block + PrintMetadata block + GCode block (DEFLATE-compressed). CRC32 covers `header_bytes + payload_bytes` per block. No third-party bgcode library is used or needed.

## Known edge cases

- **Float range overflow**: `round((la_end - la_start) / la_step) + 1` pattern count can be off by one if step doesn't divide evenly. Not currently clamped.
- **Number label density**: only even-indexed patterns get labels (`if i % 2 != 0: continue`). For very fine steps with many patterns, labels can still be crowded.
- **K reset at end**: `_set_la(la_start)` is emitted before the end template, and the end template also emits `M900 K0`. Intentional redundancy; the end template reset is the authoritative one.

## Testing

No automated test suite. Validate changes by:
1. Running `python pa_cal.py -o test.gcode` and checking it produces valid output without errors.
2. Inspecting the G-code in PrusaSlicer or OrcaSlicer preview to verify geometry.
3. Checking the bed-overflow warning fires correctly with a tight range:
   ```bash
   python pa_cal.py --la-start 0 --la-end 20 --la-step 1 -o /dev/null
   ```

## Common tasks

**Add a new Config parameter:**
1. Add field to `Config` dataclass with a default.
2. Add a CLI argument in `_build_parser()`.
3. Wire it in `main()` when constructing `Config`.

**Add a new template variable:**
1. Add the key/value to `tmpl_vars` dict in `Generator.generate()`.
2. Document it in the module docstring and README.

**Change the pattern shape:**
- Geometry lives in `_pattern()`. The half-angle and spacing formula must stay consistent with `_pattern_width()` and `_pattern_height()` or layout will break.
