# Prusa PA Calibration

A single-file Python tool that generates **Linear Advance (PA/K) calibration G-code** for the Prusa Core One. It prints a row of V-shaped test patterns, each at a different K value, so you can visually pick the best pressure advance setting.

## Requirements

- Python 3.10+
- No external dependencies (pure stdlib)
- Prusa Core One (220×220 mm bed, Marlin firmware with M900)

## Quick Start

```bash
# Coarse scan: K = 0, 1, 2, 3, 4
python pa_cal.py -o coarse.gcode

# Fine scan around your winner (e.g. K=2 looked best)
python pa_cal.py --la-start 1.5 --la-end 2.5 --la-step 0.1 --side-length 11 -o fine.gcode

# PETG profile
python pa_cal.py --hotend-temp 235 --bed-temp 85 -o petg.gcode

# Binary G-code (.bgcode) for SD card
python pa_cal.py -o la_cal.bgcode --binary
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
python pa_cal.py [options] -o OUTPUT
```

### Linear Advance
| Option | Default | Description |
|---|---|---|
| `--la-start K` | 0.0 | Start K value |
| `--la-end K` | 4.0 | End K value |
| `--la-step K` | 1.0 | K increment per pattern |

### Temperatures
| Option | Default |
|---|---|
| `--hotend-temp °C` | 215 |
| `--bed-temp °C` | 60 |

### Printer / Toolhead
| Option | Default |
|---|---|
| `--nozzle-dia mm` | 0.4 |
| `--filament-dia mm` | 1.75 |
| `--bed-x mm` | 220 |
| `--bed-y mm` | 220 |

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

### Output Options
| Option | Description |
|---|---|
| `--no-number-tab` | Suppress K-value labels on first layer |
| `--no-lcd` | Suppress M117 display messages |
| `--no-leading-zeros` | Print `.4` instead of `0.4` in labels |
| `--fan-speed %` | Part-cooling fan from layer 2 (default 100%) |
| `--first-layer-fan %` | Part-cooling fan on first layer (default 0%) |
| `--binary` | Write Prusa binary G-code v1 (.bgcode) |
| `-o FILE` | Output file (default: stdout) |

### Custom Start / End G-code

The built-in start/end G-code is derived from PrusaSlicer's Core One profile. Override with:

```bash
python pa_cal.py --start-gcode my_start.gcode --end-gcode my_end.gcode -o out.gcode
```

Template variables available in custom files:

| Variable | Description |
|---|---|
| `{bed_temp}` | Bed temperature (°C) |
| `{hotend_temp}` | Hotend temperature (°C) |
| `{mbl_temp}` | Nozzle temp during mesh bed leveling |
| `{nozzle_dia}` | Nozzle diameter (mm) |
| `{filament_dia}` | Filament diameter (mm) |
| `{cool_fan}` | `M106 S70` (PLA) or `M107` (PETG+) |
| `{m555_x}` / `{m555_y}` | M555 print-area origin |
| `{m555_w}` / `{m555_h}` | M555 print-area dimensions |
| `{park_z}` | Safe Z for end-of-print parking |
| `{max_layer_z}` | Highest layer Z in the print |

## Bed Overflow Warning

If your LA range and side length don't fit on the bed, the tool prints a warning and suggests a safe `--side-length`:

```
WARNING: pattern area 130.0×44.0 mm exceeds bed 220×220 mm.
  Fix: use --side-length 9 (or a larger --la-step / narrower range)
```

## License

See [LICENSE](LICENSE).
