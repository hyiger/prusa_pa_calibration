# Prusa Calibration Tools

Python tools that generate calibration G-code for Prusa printers. No external dependencies — pure stdlib, Python 3.10+.

| Script | Purpose |
|---|---|
| `pa_calibration.py` | Linear Advance (PA/K) calibration — V-shaped corner patterns |
| `temperature_tower.py` | Temperature tower — rectangular tower with per-segment temperatures |

Both scripts share the same printer/filament presets, retraction options, and upload flags.

Supports: Mini, MK4S, Core One (default), Core One L, XL.

## Requirements

- Python 3.10+
- No external dependencies (pure stdlib)
- Prusa printer with Marlin firmware

---

## pa_calibration.py — Linear Advance Calibration

Prints a row of V-shaped corner patterns, each at a different K value. The pattern with the sharpest corner is your ideal pressure-advance setting.

### Quick Start

```bash
# Coarse scan: K = 0, 1, 2, 3, 4 (Core One, PLA defaults)
python3 pa_calibration.py -o coarse.bgcode

# Fine scan around your winner (e.g. K=2 looked best)
python3 pa_calibration.py --la-start 1.5 --la-end 2.5 --la-step 0.1 --side-length 11 -o fine.bgcode

# PETG on a Core One
python3 pa_calibration.py --filament PETG -o petg.bgcode

# PETG on an MK4S
python3 pa_calibration.py --printer MK4S --filament PETG -o mk4s_petg.bgcode

# Plain ASCII G-code (e.g. for older firmware or inspection)
python3 pa_calibration.py --ascii -o la_cal.gcode
```

### How to Read the Results

Print the file, then examine the V-shaped corners under good lighting:

| What you see | Meaning |
|---|---|
| Bulge / blob at corner apex | K too **high** |
| Gap / underextrusion before corner | K too **low** |
| Sharp, clean corner | K is **correct** |

Run a coarse scan first (default K=0–4, step=1), identify the best-looking column, then run a fine scan (step=0.1) centred on that value.

### pa_calibration.py Options

#### Linear Advance
| Option | Default | Description |
|---|---|---|
| `--la-start K` | 0.0 | Start K value |
| `--la-end K` | 4.0 | End K value |
| `--la-step K` | 1.0 | K increment per pattern |

#### Pattern Geometry
| Option | Default | Notes |
|---|---|---|
| `--layer-count N` | 4 | Layers per pattern |
| `--side-length mm` | 20.0 | Leg length; decrease for fine scans with many patterns |
| `--wall-count N` | 3 | Nested walls per pattern |
| `--corner-angle deg` | 90.0 | Angle at apex |
| `--pattern-spacing mm` | 2.0 | Gap between patterns |

#### Labels
| Option | Description |
|---|---|
| `--no-number-tab` | Suppress K-value labels on first layer |
| `--no-leading-zeros` | Print `.4` instead of `0.4` in labels |

---

## temperature_tower.py — Temperature Tower

Prints a rectangular tower split into horizontal segments, each at a different hotend temperature. Examine surface quality, bridging, and stringing at each band to find your ideal temperature.

### Quick Start

```bash
# PLA: 215 → 185 °C in 5 °C steps (default)
python3 temperature_tower.py -o temp_tower.bgcode

# PETG on Core One
python3 temperature_tower.py --filament PETG -o petg_tower.bgcode

# PETG with explicit range
python3 temperature_tower.py --filament PETG --temp-start 240 --temp-end 210 -o petg_tower.bgcode

# ABS on MK4S
python3 temperature_tower.py --printer MK4S --filament ABS -o abs_tower.bgcode

# Fine scan around 230 °C
python3 temperature_tower.py --temp-start 235 --temp-end 225 --temp-step 2 -o fine_tower.bgcode

# Plain ASCII G-code
python3 temperature_tower.py --ascii -o temp_tower.gcode
```

### How to Read the Results

Print the tower and examine each horizontal band:

| What to look for | Meaning |
|---|---|
| Stringing between features | Temperature too **high** |
| Poor layer adhesion / brittle | Temperature too **low** |
| Clean surface, good bridging | Temperature is **correct** |

The bottom segment is `--temp-start`, the top segment is `--temp-end`. Work from bottom (hotter) to top (cooler) by default.

### temperature_tower.py Options

| Option | Default | Description |
|---|---|---|
| `--temp-start °C` | preset or 215 | Bottom segment temperature |
| `--temp-end °C` | temp-start − 30 | Top segment temperature |
| `--temp-step °C` | 5.0 | Temperature change per segment (always positive) |
| `--module-height mm` | 10.0 | Height of each temperature segment |
| `--module-depth mm` | 10.0 | Depth (Y footprint) of each segment |
| `--bridge-length mm` | 30.0 | Length of bridge / stringing-test area between walls |
| `--bridge-thick mm` | 1.0 | Thickness of bridge slab at top of each segment |
| `--short-angle deg` | 45.0 | Overhang angle of short-side wall |
| `--long-angle deg` | 35.0 | Overhang angle of long-side wall |
| `--n-cones N` | 2 | Number of stringing-test cones in bridge gap |
| `--base-thick mm` | 1.0 | Thickness of solid base slab |
| `--no-label-tab` | — | Disable temperature labels on bridge face (default: enabled) |
| `--grid-infill` | — | Use crosshatch diamond infill for overhang walls instead of solid |
| `--infill-density %` | 50 | Infill density for `--grid-infill` |

---

## Shared Options

Both scripts accept the following options.

### Printer & Filament Presets

These two flags set sensible defaults for the most common combinations. Explicit flags always override preset values.

#### `--printer MODEL`

Sets bed dimensions and max build height. Default: `COREONE`.

| Model | Bed | Max Z | Notes |
|---|---|---|---|
| `MINI` | 180×180 mm | 180 mm | Bowden — increase `--retract-dist` |
| `MK4S` | 250×210 mm | 220 mm | |
| `COREONE` | 250×220 mm | 270 mm | **Default** |
| `COREONEL` | 300×300 mm | 330 mm | |
| `XL` | 360×360 mm | 360 mm | |

> **Note:** The built-in start/end G-code is tuned for the Core One. For all other printers, provide `--start-gcode` / `--end-gcode` with your printer's correct sequence.

#### `--filament TYPE`

Sets hotend/bed temperatures, fan speeds, and retraction distance.

| Type | Hotend | Bed | Fan | Retract |
|---|---|---|---|---|
| `PLA` | 215 °C | 60 °C | 100 % | 0.6 mm |
| `PETG` | 235 °C | 85 °C | 50 % | 0.8 mm |
| `ABS` | 245 °C | 100 °C | 0 % | 1.0 mm |
| `ASA` | 255 °C | 100 °C | 20 % | 1.0 mm |
| `PA` | 260 °C | 90 °C | 0 % | 1.0 mm |
| `TPU` | 230 °C | 60 °C | 50 % | 0.0 mm |
| `PC` | 275 °C | 110 °C | 0 % | 1.0 mm |

### Temperatures
| Option | Default | Description |
|---|---|---|
| `--hotend-temp °C` | preset or 215 | Overrides `--filament` preset |
| `--bed-temp °C` | preset or 60 | Overrides `--filament` preset |

### Printer / Toolhead
| Option | Default | Description |
|---|---|---|
| `--nozzle-dia mm` | 0.4 | |
| `--filament-dia mm` | 1.75 | |
| `--bed-x mm` | from `--printer` | Overrides printer preset |
| `--bed-y mm` | from `--printer` | Overrides printer preset |
| `--max-z mm` | from `--printer` | Overrides printer preset |

### Layer Settings
| Option | Default |
|---|---|
| `--first-layer-height mm` | 0.25 |
| `--layer-height mm` | 0.20 |

### Speeds (mm/s)
| Option | Default |
|---|---|
| `--print-speed` | 100.0 |
| `--first-layer-speed` | 30.0 |
| `--travel-speed` | 150.0 |

### Anchor (first layer adhesion)
| Option | Default | Description |
|---|---|---|
| `--anchor` | frame | `frame` = concentric perimeters, `layer` = filled rectangle, `none` = skip |
| `--anchor-perimeters N` | 4 | Number of anchor perimeter loops |

### Retraction
| Option | Default | Description |
|---|---|---|
| `--retract-dist mm` | preset or 0.6 | Overrides `--filament` preset |
| `--retract-speed mm/s` | 45.0 | |
| `--unretract-speed mm/s` | 45.0 | |
| `--zhop mm` | 0.1 | Z-hop height; 0 to disable |

### Output Options
| Option | Description |
|---|---|
| `--no-lcd` | Suppress M117 display messages |
| `--fan-speed %` | Part-cooling fan from layer 2 (default: preset or 100%) |
| `--first-layer-fan %` | Part-cooling fan on first layer (default: preset or 0%) |
| `--ascii` | Write plain ASCII G-code instead of binary (default output is `.bgcode`) |
| `-o FILE` | Output file (default: stdout) |

### PrusaLink Upload (local network)

Upload the generated file directly to a printer running PrusaLink (the embedded web server on Prusa MK4S, Core One, XL, and Mini with firmware 5.x+). The printer must be reachable on your local network.

| Option | Description |
|---|---|
| `--prusalink-url URL` | Base URL of the printer's web interface (e.g. `http://192.168.1.100`) |
| `--prusalink-key KEY` | API key from the printer (Settings → API Key) |
| `--prusalink-filename NAME` | Remote filename (default: basename of `-o`, or script default) |
| `--prusalink-print` | Start printing immediately after upload |

```bash
# Generate PA calibration and upload
python3 pa_calibration.py --prusalink-url http://192.168.1.100 --prusalink-key abc123 --prusalink-print

# Generate temperature tower and upload
python3 temperature_tower.py --filament PETG --prusalink-url http://192.168.1.100 --prusalink-key abc123
```

> **Finding your API key:** Open the printer's web UI, go to **Settings → API Key**, and copy the key shown there.

### PrusaConnect Upload (cloud)

Upload directly to [connect.prusa3d.com](https://connect.prusa3d.com) so you can trigger a print from anywhere without needing to be on the same network as the printer.

Authentication uses OAuth2 — run `prusa_login.py` once to store a token, then pass `--prusaconnect` on every upload:

```bash
# Authenticate once (opens browser for Prusa Account login)
python3 prusa_login.py

# Generate and upload
python3 pa_calibration.py --prusaconnect --prusaconnect-print
python3 temperature_tower.py --filament PETG --prusaconnect
```

| Option | Description |
|---|---|
| `--prusaconnect` | Upload to PrusaConnect using the stored OAuth token |
| `--prusaconnect-filename NAME` | Remote filename (default: basename of `-o`, or script default) |
| `--prusaconnect-print` | Start printing immediately after upload |

### Custom Start / End G-code

The built-in start/end G-code is derived from PrusaSlicer's Core One profile. Override with:

```bash
python3 pa_calibration.py --printer MK4S --start-gcode mk4s_start.gcode --end-gcode mk4s_end.gcode -o out.bgcode
python3 temperature_tower.py --printer MK4S --start-gcode mk4s_start.gcode --end-gcode mk4s_end.gcode -o out.bgcode
```

Template variables available in custom files:

| Variable | Description |
|---|---|
| `{bed_temp}` | Bed temperature (°C) |
| `{hotend_temp}` | Hotend temperature (°C) |
| `{mbl_temp}` | Nozzle temp during mesh bed leveling |
| `{nozzle_dia}` | Nozzle diameter (mm) |
| `{filament_dia}` | Filament diameter (mm) |
| `{cool_fan}` | `M106 S70` (bed ≤ 60 °C) or `M107` |
| `{m555_x}` / `{m555_y}` | M555 print-area origin |
| `{m555_w}` / `{m555_h}` | M555 print-area dimensions |
| `{park_z}` | Safe Z for end-of-print parking |
| `{max_layer_z}` | Highest layer Z in the print |

## Startup Output

On every run the tools print a summary to stderr:

```
Printer: MK4S  bed 250×210 mm  max Z 220 mm
WARNING: no start-gcode or end-gcode found for MK4S — falling back to built-in Core One template ...
Filament: PETG  hotend 235 °C  bed 85 °C  fan 50 %  retract 0.8 mm
```

## License

See [LICENSE](LICENSE).
