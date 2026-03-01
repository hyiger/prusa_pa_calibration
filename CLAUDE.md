# CLAUDE.md — Prusa Calibration Tools

## Project overview

Python CLI tools that generate calibration G-code for Prusa printers (Mini, MK4S, Core One, Core One L, XL). No external dependencies. Targets Python 3.10+.

| File | Role |
|---|---|
| `_common.py` | Shared infrastructure (presets, base classes, CLI helpers, upload) |
| `pa_calibration.py` | Linear Advance calibration — imports from `_common` |
| `temperature_tower.py` | Temperature tower calibration — imports from `_common` |

## Architecture

```
_common.py
├─ FILAMENT_PRESETS / PRINTER_PRESETS / DEFAULT_START_GCODE / DEFAULT_END_GCODE
├─ _r / _render / _write_bgcode / _upload_prusalink   — utilities
├─ CommonConfig (dataclass)   — shared config fields
├─ _State                     — tracks live printer position + retract/hop state
├─ BaseGenerator              — shared G-code generation methods
│    ├─ buffer: _emit / _comment / _blank
│    ├─ motion: _retract / _unretract / _travel / _line / _e_amount
│    ├─ drawing: _perimeter / _anchor_frame / _anchor_layer / _circle
│    ├─ labels: _draw_digit / _draw_number / _digit_width / _num_tab_height
│    └─ helpers: _m555 / _base_tmpl_vars
├─ add_common_args(p, stem)   — shared argparse groups (called first in _build_parser)
├─ resolve_presets(args, dir) — printer/filament lookup, template loading
└─ handle_output(gcode, args, stem) — file write + upload

pa_calibration.py
├─ Config(CommonConfig)       — adds la_start/end/step, layer_count, pattern geometry, labels
├─ Generator(BaseGenerator)   — adds _set_la, _pattern, generate()
└─ main()                     — resolve_presets → Config → Generator → handle_output

temperature_tower.py
├─ Config(CommonConfig)       — adds temp_start/end/step, module_height/depth, bridge_length/thick,
│                                short/long_angle, n_cones, base_thick, label_tab, grid_infill, infill_density
├─ TowerGenerator(BaseGenerator) — adds _grid_layer, generate() with per-segment M104/M109 commands
└─ main()                     — resolve_presets → Config → TowerGenerator → handle_output
```

## Key conventions

- **Rounding**: use `_r(value, places)` everywhere. Constants: `_PA=4`, `_Z=3`, `_XY=4`, `_E=5`. Never write raw `round()` calls in G-code output paths.
- **Speeds**: internal units are mm/s; G-code requires mm/min, so always multiply by 60 when emitting `F` values.
- **Extrusion**: always compute via `_e_amount(dist, lh, lw)` — it accounts for filament diameter and `extrusion_multiplier`.
- **Wall spacing formula**: `lw - lh * (1 - π/4)` — matches Slic3r/PrusaSlicer. Used in `_pattern`, `_anchor_frame`, `_anchor_layer`, tower walls, and geometry helpers.
- **Retraction**: `_retract()` / `_unretract()` are idempotent. Call freely; they no-op if already in the right state. `_travel()` calls them automatically for moves > 2 mm.
- **LA range**: patterns are indexed `0..n_patterns-1`. K value for pattern `i` = `_r(la_start + i * la_step, _PA)`. Always use this formula — do not accumulate K across iterations.

## Preset resolution

CLI args default to `None` for all preset-affected fields. In `main()`, values are resolved in this priority order (highest to lowest):

1. Explicit CLI flag (non-`None`)
2. `--filament` preset value (for thermal/fan/retraction fields)
3. `--printer` preset value (for bed/max_z fields)
4. Hardcoded fallback (matches `Config` dataclass defaults)

The helper `_p(arg_val, key, fallback, source)` implements this for a single field.

**Filament-controlled fields**: `hotend_temp`, `bed_temp`, `fan_speed`, `first_layer_fan`, `retract_dist`

**Printer-controlled fields**: `bed_x`, `bed_y`, `max_z`

## PA calibration geometry

The test pattern is a V-shape (two diagonal legs meeting at an apex pointing left). `corner_angle` is the angle at the apex; `half` = `(180 - corner_angle) / 2` radians is used for all trig.

Successive walls offset inward perpendicular to the left-leg direction:
- perpendicular direction: `(sin α, -cos α)`
- offset per wall: `w * spacing`

Pattern bounding box:
- width: `2 * side_length * cos(half) + (wall_count-1) * spacing * sin(half)`
- height: `side_length * sin(half) + (wall_count-1) * spacing * cos(half)`

## Temperature tower geometry

Each segment consists of a short overhang wall, a long overhang wall, a `bridge_length` gap (with stringing-test cones), and a solid bridging slab at the top. The segment footprint is `module_depth` deep. Each segment spans `layers_per_seg = max(1, round(module_height / layer_height))` layers. At the start of each segment's first layer, `M104 S{temp}` (no-wait) is emitted. `M109 S{temp}` (wait) is used for segment 0 only.

Total layers = `n_base_layers + n_segs * layers_per_seg`.

Temperature labels are printed as flat 7-segment digit paths on the bridge slab surface (topmost `bridge_thick` mm of each segment), building ~`bridge_thick` mm of raised text visible in slicer preview.

## Start / end G-code templates

Built-in templates live in `DEFAULT_START_GCODE` / `DEFAULT_END_GCODE` in `_common.py`. They are derived from PrusaSlicer's Core One profile with conditionals resolved. Custom templates are loaded from files passed via `--start-gcode` / `--end-gcode`.

Template variables are substituted by `_render()`. Only `{lowercase_identifier}` markers are replaced; unknown markers and PrusaSlicer `{if ...}` expressions are left untouched.

**Important:** the built-in start/end G-code is Core One-specific (M862.3, purge line positions, MBL commands). When `--printer` is not `COREONE` and no `--start-gcode` is provided, a warning is printed to stderr.

`_base_tmpl_vars(max_layer_z, orig_x, orig_y, total_w, total_h)` in `BaseGenerator` builds the standard 12-variable dict used by both scripts.

## Binary G-code (.bgcode)

Implemented in `_write_bgcode()` in `_common.py` using only `struct` and `zlib`. Format: file header + PrinterMetadata block + PrintMetadata block + GCode block (DEFLATE-compressed). CRC32 covers `header_bytes + payload_bytes` per block. No third-party bgcode library is used or needed.

## Upload (PrusaLink / PrusaConnect)

`_upload_prusalink()` in `_common.py` uses `PUT /api/v1/files/local/{filename}` with `X-Api-Key` header and RFC 8941 `Print-After-Upload: ?1`. The filename path segment is URL-encoded with `urllib.parse.quote(filename, safe='')`.

`handle_output()` calls it for both PrusaLink (user-provided URL) and PrusaConnect (hardcoded `https://connect.prusa3d.com`). Both scripts get these flags for free via `add_common_args()`.

## Known edge cases

- **Float range overflow**: `round((la_end - la_start) / la_step) + 1` pattern count can be off by one if step doesn't divide evenly. Not currently clamped.
- **Number label density** (pa_calibration): only even-indexed patterns get labels (`if i % 2 != 0: continue`). For very fine steps with many patterns, labels can still be crowded.
- **K reset at end** (pa_calibration): `_set_la(la_start)` is emitted before the end template, and the end template also emits `M900 K0`. Intentional redundancy; the end template reset is the authoritative one.
- **Mini retraction**: the MINI preset sets bed size and max_z but does not force longer retraction (Bowden typically needs 2–4 mm). Users should add `--retract-dist 2.0` manually when using `--printer MINI`.

## Testing

Run the full test suite (pytest required):
```bash
python3 -m pytest tests/ -v
```

Tests live in `tests/`:
| File | Covers |
|---|---|
| `tests/test_common.py` | `_common.py` — presets, `BaseGenerator`, thumbnails, bgcode writer, upload helpers |
| `tests/test_pa_calibration.py` | `pa_calibration.py` — Config, Generator, generate() |
| `tests/test_temperature_tower.py` | `temperature_tower.py` — Config, TowerGenerator, generate() |

After making changes, also do a quick smoke-test:
1. Running both scripts to verify output:
   ```bash
   python3 pa_calibration.py -o /tmp/pa_test.bgcode
   python3 temperature_tower.py -o /tmp/tt_test.bgcode
   ```
2. Inspecting the G-code in PrusaSlicer or OrcaSlicer preview to verify geometry.
3. Checking preset resolution works correctly:
   ```bash
   python3 pa_calibration.py --printer MK4S --filament PETG 2>&1 | head -3
   python3 temperature_tower.py --printer MK4S --filament PETG 2>&1 | head -3
   ```
4. Checking the bed-overflow warning fires:
   ```bash
   python3 pa_calibration.py --la-start 0 --la-end 20 --la-step 1 -o /dev/null
   ```

## Common tasks

**Add a new filament preset:**
1. Add an entry to `FILAMENT_PRESETS` in `_common.py` with all five fields: `hotend_temp`, `bed_temp`, `fan_speed`, `first_layer_fan`, `retract_dist`.
2. Update the filament table in README.md.

**Add a new printer preset:**
1. Add an entry to `PRINTER_PRESETS` in `_common.py` with: `bed_x`, `bed_y`, `max_z`, `model`.
2. Update the printer table in README.md.
3. If the printer uses a distinct start/end G-code, note it in the README.

**Add a new shared Config parameter:**
1. Add field to `CommonConfig` in `_common.py` with a default.
2. Add the CLI argument in `add_common_args()`.
3. Wire it in both scripts' `main()` when constructing `Config`.

**Add a script-specific Config parameter:**
1. Add field to that script's `Config(CommonConfig)` subclass.
2. Add the CLI argument in that script's `_build_parser()`.
3. Wire it in that script's `main()`.

**Add a new template variable:**
1. Add the key/value to the dict returned by `_base_tmpl_vars()` in `BaseGenerator` (if shared) or in the script's `generate()` (if script-specific).
2. Document it in the script's module docstring and README.

**Change the PA pattern shape:**
- Geometry lives in `_pattern()` in `pa_calibration.py`. The half-angle and spacing formula must stay consistent with `_pattern_width()` and `_pattern_height()` or layout will break.
