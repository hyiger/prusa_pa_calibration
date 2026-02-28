# Prusa PA Calibration

A single-file Python tool that generates **Linear Advance (PA/K) calibration G-code** for Prusa printers. It prints a row of V-shaped test patterns, each at a different K value, so you can visually pick the best pressure advance setting.

Supports: Mini, MK4S, Core One (default), Core One L, XL.

## Requirements

- Python 3.10+
- No external dependencies (pure stdlib)
- Prusa printer with Marlin firmware and M900 Linear Advance support

## Quick Start

```bash
# Coarse scan: K = 0, 1, 2, 3, 4 (Core One, PLA defaults)
python3 pa_cal.py -o coarse.gcode

# Fine scan around your winner (e.g. K=2 looked best)
python3 pa_cal.py --la-start 1.5 --la-end 2.5 --la-step 0.1 --side-length 11 -o fine.gcode

# PETG on a Core One
python3 pa_cal.py --filament PETG -o petg.gcode

# PETG on an MK4S
python3 pa_cal.py --printer MK4S --filament PETG -o mk4s_petg.gcode

# PA (Nylon) with a manual hotend override
python3 pa_cal.py --filament PA --hotend-temp 265 -o pa.gcode

# Binary G-code (.bgcode) for SD card
python3 pa_cal.py -o la_cal.bgcode --binary
```

## How to Read the Results

Print the file, then examine the V-shaped corners under good lighting:

| What you see | Meaning |
|---|---|
| Bulge / blob at corner apex | K too **high** |
| Gap / underextrusion before corner | K too **low** |
| Sharp, clean corner | K is **correct** |

Run a coarse scan first (default K=0–4, step=1), identify the best-looking column, then run a fine scan (step=0.1) centred on that value.

## CLI Reference

```
python3 pa_cal.py [options] -o OUTPUT
```

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

### Linear Advance
| Option | Default | Description |
|---|---|---|
| `--la-start K` | 0.0 | Start K value |
| `--la-end K` | 4.0 | End K value |
| `--la-step K` | 1.0 | K increment per pattern |

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
| `--layer-count N` | 4 |

### Speeds (mm/s)
| Option | Default |
|---|---|
| `--print-speed` | 100.0 |
| `--first-layer-speed` | 30.0 |
| `--travel-speed` | 150.0 |

### Pattern Geometry
| Option | Default | Notes |
|---|---|---|
| `--side-length mm` | 20.0 | Leg length; decrease for fine scans with many patterns |
| `--wall-count N` | 3 | Nested walls per pattern |
| `--corner-angle deg` | 90.0 | Angle at apex |
| `--pattern-spacing mm` | 2.0 | Gap between patterns |

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
| `--no-number-tab` | Suppress K-value labels on first layer |
| `--no-lcd` | Suppress M117 display messages |
| `--no-leading-zeros` | Print `.4` instead of `0.4` in labels |
| `--fan-speed %` | Part-cooling fan from layer 2 (default: preset or 100%) |
| `--first-layer-fan %` | Part-cooling fan on first layer (default: preset or 0%) |
| `--binary` | Write Prusa binary G-code v1 (.bgcode) |
| `-o FILE` | Output file (default: stdout) |

### Custom Start / End G-code

The built-in start/end G-code is derived from PrusaSlicer's Core One profile. Override with:

```bash
python3 pa_cal.py --printer MK4S --start-gcode mk4s_start.gcode --end-gcode mk4s_end.gcode -o out.gcode
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

On every run the tool prints a summary to stderr so you can confirm active settings:

```
Printer: MK4S  bed 250×210 mm  max Z 220 mm
WARNING: built-in start/end G-code is tuned for the Core One. Use --start-gcode / --end-gcode for accurate MK4S output.
Filament: PETG  hotend 235 °C  bed 85 °C  fan 50 %  retract 0.8 mm
```

## Bed Overflow Warning

If your LA range and side length don't fit on the bed, the tool prints a warning and suggests a safe `--side-length`:

```
WARNING: pattern area 130.0×44.0 mm exceeds bed 220×220 mm.
  Fix: use --side-length 9 (or a larger --la-step / narrower range)
```

## License

See [LICENSE](LICENSE).
