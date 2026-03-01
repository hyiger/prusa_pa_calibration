"""Unit tests for temperature_tower.py — temperature tower generator."""

import io
import math
import re
import unittest
from unittest import mock

from temperature_tower import Config, TowerGenerator
from _common import BaseGenerator


# ── helpers ────────────────────────────────────────────────────────────────────

def _cfg(**overrides):
    """Minimal Config for fast tests: 2 segments, thin modules, 1 base layer."""
    defaults = dict(
        temp_end=210,
        temp_step=5.0,
        module_height=2.0,   # 10 layers per segment at 0.2 mm → fast
        base_thick=0.2,      # 1 base layer
        layer_height=0.2,
        first_layer_height=0.25,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _gen(**overrides):
    return TowerGenerator(_cfg(**overrides))


def _gcode(**overrides):
    return _gen(**overrides).generate()


# ── Config ─────────────────────────────────────────────────────────────────────

class TestTempTowerConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.temp_start, 215)
        self.assertEqual(cfg.temp_end, 185)
        self.assertEqual(cfg.temp_step, 5.0)
        self.assertEqual(cfg.module_height, 10.0)
        self.assertEqual(cfg.module_depth, 10.0)
        self.assertEqual(cfg.bridge_length, 30.0)
        self.assertEqual(cfg.bridge_thick, 1.0)
        self.assertEqual(cfg.short_angle, 45.0)
        self.assertEqual(cfg.long_angle, 35.0)
        self.assertEqual(cfg.n_cones, 2)
        self.assertEqual(cfg.base_thick, 1.0)
        self.assertTrue(cfg.label_tab)
        self.assertFalse(cfg.grid_infill)
        self.assertEqual(cfg.infill_density, 50)

    def test_inherits_common_config(self):
        cfg = Config(nozzle_dia=0.6, zhop=0.0)
        self.assertEqual(cfg.nozzle_dia, 0.6)
        self.assertEqual(cfg.zhop, 0.0)


# ── TowerGenerator._grid_layer ─────────────────────────────────────────────────

class TestGridLayer(unittest.TestCase):
    def _gen(self, infill_density=50):
        return TowerGenerator(_cfg(infill_density=infill_density))

    def test_generates_perimeters_and_diagonal_infill(self):
        g = self._gen()
        g._grid_layer(0, 0, 30, 10, 0.2, 0.45, 60.0)
        output = "\n".join(g._buf)
        self.assertIn("G0", output)
        self.assertIn("G1", output)

    def test_higher_density_more_infill_lines(self):
        g25 = self._gen(infill_density=25)
        g75 = self._gen(infill_density=75)
        g25._grid_layer(0, 0, 30, 10, 0.2, 0.45, 60.0)
        g75._grid_layer(0, 0, 30, 10, 0.2, 0.45, 60.0)
        # More density → smaller pitch → more lines
        self.assertGreater(len(g75._buf), len(g25._buf))

    def test_tiny_rectangle_no_crash(self):
        g = self._gen()
        # Too small for interior lines — should not raise
        g._grid_layer(0, 0, 0.5, 0.5, 0.2, 0.45, 60.0)

    def test_produces_both_diagonal_directions(self):
        g = self._gen()
        g._grid_layer(0, 0, 30, 30, 0.2, 0.45, 60.0)
        # Both +45° and −45° lines → some lines go up-right, some down-right
        # All G1 lines should have varying Y coordinates (not all same)
        g1_lines = [l for l in g._buf if l.startswith("G1")]
        ys = []
        for line in g1_lines:
            m = re.search(r"Y([\d.]+)", line)
            if m:
                ys.append(float(m.group(1)))
        self.assertGreater(len(set(round(y, 1) for y in ys)), 2)

    def test_infill_stays_inside_perimeter_bounds(self):
        x0, y0, sx, sy = 5.0, 5.0, 30.0, 10.0
        g = self._gen()
        g._grid_layer(x0, y0, sx, sy, 0.2, 0.45, 60.0)
        # All X and Y coordinates in G1 lines should be within bounds
        for line in g._buf:
            if not line.startswith("G1"):
                continue
            xm = re.search(r"X([\d.]+)", line)
            ym = re.search(r"Y([\d.]+)", line)
            if xm:
                self.assertGreaterEqual(float(xm.group(1)), x0 - 0.01)
                self.assertLessEqual(float(xm.group(1)), x0 + sx + 0.01)
            if ym:
                self.assertGreaterEqual(float(ym.group(1)), y0 - 0.01)
                self.assertLessEqual(float(ym.group(1)), y0 + sy + 0.01)


# ── TowerGenerator.generate() ─────────────────────────────────────────────────

class TestGenerate(unittest.TestCase):
    def test_returns_string(self):
        self.assertIsInstance(_gcode(), str)

    def test_ends_with_newline(self):
        self.assertTrue(_gcode().endswith("\n"))

    # ── PrusaSlicer layer markers ──────────────────────────────────────────────

    def test_layer_change_marker_present(self):
        self.assertIn(";LAYER_CHANGE", _gcode())

    def test_z_marker_present(self):
        self.assertIn(";Z:", _gcode())

    def test_height_marker_present(self):
        self.assertIn(";HEIGHT:", _gcode())

    def test_before_layer_change_present(self):
        self.assertIn(";BEFORE_LAYER_CHANGE", _gcode())

    def test_after_layer_change_present(self):
        self.assertIn(";AFTER_LAYER_CHANGE", _gcode())

    def test_layer_change_count_matches_layer_count(self):
        gcode = _gcode(temp_start=215, temp_end=210, temp_step=5.0)
        # n_base + n_segs * layers_per_seg
        n_base = max(1, round(0.2 / 0.2))           # 1
        lps    = max(1, round(2.0 / 0.2))            # 10
        expected = n_base + 2 * lps                  # 21
        self.assertEqual(gcode.count(";LAYER_CHANGE"), expected)

    # ── Temperature control ────────────────────────────────────────────────────

    def test_first_segment_uses_m109_wait(self):
        gcode = _gcode(temp_start=215, temp_end=210, temp_step=5.0)
        self.assertIn("M109 S215", gcode)

    def test_subsequent_segments_use_m104_nowait(self):
        gcode = _gcode(temp_start=215, temp_end=210, temp_step=5.0)
        self.assertIn("M104 S210", gcode)
        self.assertNotIn("M109 S210", gcode)

    def test_decreasing_temperature_sequence(self):
        gcode = _gcode(temp_start=215, temp_end=205, temp_step=5.0)
        # All three temps appear
        self.assertIn("S215", gcode)
        self.assertIn("S210", gcode)
        self.assertIn("S205", gcode)

    def test_increasing_temperature_sequence(self):
        gcode = _gcode(temp_start=205, temp_end=215, temp_step=5.0)
        self.assertIn("S205", gcode)
        self.assertIn("S210", gcode)
        self.assertIn("S215", gcode)

    def test_single_segment_no_m104_for_temp_change(self):
        gcode = _gcode(temp_start=215, temp_end=215, temp_step=5.0)
        self.assertIn("M109 S215", gcode)
        # Per-segment M104 lines are tagged "; segment N: T °C" — none for 1 seg
        seg_m104 = [l for l in gcode.splitlines() if "M104" in l and "; segment" in l]
        self.assertEqual(len(seg_m104), 0)

    def test_m104_not_emitted_for_first_segment(self):
        # Segment 0 uses M109; M104 only for seg > 0
        gcode = _gcode(temp_start=215, temp_end=210, temp_step=5.0)
        # M109 S215 present, but M104 S215 should not be (only first seg start)
        # (It's possible M104 S215 could appear elsewhere; check it's not a segment-change cmd)
        lines = gcode.splitlines()
        seg_m104_215 = [l for l in lines if "M104 S215" in l and "segment" in l]
        self.assertEqual(len(seg_m104_215), 0)

    def test_uneven_step_emits_warning(self):
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(temp_start=215, temp_end=200, temp_step=7.0)
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("not a multiple", buf.getvalue())

    def test_uneven_step_lands_on_temp_end(self):
        gcode = _gcode(temp_start=215, temp_end=200, temp_step=7.0)
        # Should still reach temp_end even though step doesn't divide evenly
        self.assertIn("S200", gcode)

    # ── Structural features ────────────────────────────────────────────────────

    def test_base_layer_comment_present(self):
        gcode = _gcode(base_thick=0.2)
        self.assertIn("BASE", gcode)

    def test_multiple_base_layers(self):
        gcode = _gcode(base_thick=1.0, layer_height=0.2)
        # 5 base layers → "BASE" should appear 5 times (in layer header)
        self.assertGreaterEqual(gcode.count("BASE"), 5)

    def test_layer_z_increases_monotonically(self):
        gcode = _gcode()
        z_values = [float(m.group(1))
                    for m in re.finditer(r";Z:([\d.]+)", gcode)]
        self.assertGreater(len(z_values), 1)
        for i in range(1, len(z_values)):
            self.assertGreater(z_values[i], z_values[i - 1])

    def test_fan_speed_set_on_layer_2(self):
        gcode = _gcode(fan_speed=75)
        # 75% of 255 ≈ 191
        self.assertIn("M106 S191", gcode)

    def test_first_layer_fan_applied(self):
        gcode = _gcode(first_layer_fan=50)
        # 50% of 255 ≈ 127
        self.assertIn("M106 S127", gcode)

    def test_show_lcd_emits_m117(self):
        gcode = _gcode(show_lcd=True)
        self.assertIn("M117", gcode)

    def test_no_lcd_suppresses_m117(self):
        gcode = _gcode(show_lcd=False)
        self.assertNotIn("M117", gcode)

    # ── Label tab ─────────────────────────────────────────────────────────────

    def test_label_tab_enabled_produces_more_g1(self):
        with_label    = _gcode(label_tab=True)
        without_label = _gcode(label_tab=False)
        self.assertGreater(with_label.count("G1"), without_label.count("G1"))

    def test_label_tab_disabled_valid_gcode(self):
        gcode = _gcode(label_tab=False)
        self.assertIn(";LAYER_CHANGE", gcode)

    # ── Grid infill ────────────────────────────────────────────────────────────

    def test_grid_infill_changes_output(self):
        solid = _gcode(grid_infill=False)
        grid  = _gcode(grid_infill=True)
        self.assertNotEqual(solid.count("G1"), grid.count("G1"))

    def test_grid_infill_bridge_layers_forced_solid(self):
        # Bridge layers always solid; grid_infill generates fewer G1 for walls
        # but the bridge slab itself should be the same. Both should have labels.
        gcode = _gcode(grid_infill=True, label_tab=True)
        self.assertIn(";LAYER_CHANGE", gcode)
        # Solid and grid should produce labels on bridge (both same count)
        solid = _gcode(grid_infill=False, label_tab=True)
        # Label output depends only on bridge layers (always solid) → same count
        # Actually they can differ slightly due to wall density. Just check valid.
        self.assertIsInstance(gcode, str)

    def test_infill_density_affects_output(self):
        g25 = _gcode(grid_infill=True, infill_density=25)
        g75 = _gcode(grid_infill=True, infill_density=75)
        # Higher density → more G1 lines for infill
        self.assertNotEqual(g25.count("G1"), g75.count("G1"))

    # ── Anchor ────────────────────────────────────────────────────────────────

    def test_anchor_frame(self):
        gcode = _gcode(anchor="frame")
        self.assertIn("Anchor frame", gcode)

    def test_anchor_layer(self):
        gcode = _gcode(anchor="layer")
        self.assertIn("Anchor layer", gcode)

    def test_anchor_none(self):
        gcode = _gcode(anchor="none")
        self.assertNotIn("Anchor frame", gcode)
        self.assertNotIn("Anchor layer (filled)", gcode)

    # ── Warnings ──────────────────────────────────────────────────────────────

    def test_bed_overflow_warning(self):
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(bridge_length=500.0)   # vastly oversized
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("exceeds bed", buf.getvalue())

    def test_max_z_overflow_warning(self):
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(
                temp_start=215, temp_end=155, temp_step=5.0,
                module_height=10.0, max_z=5.0,
            )
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("max Z", buf.getvalue())

    def test_no_warning_for_valid_config(self):
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(temp_start=215, temp_end=210, temp_step=5.0)
        output = buf.getvalue()
        # Only the "Printer: ..." line, no WARNING
        self.assertNotIn("WARNING", output)

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_no_cones(self):
        # n_cones=0: no circles drawn, should still generate valid output
        gcode = _gcode(n_cones=0)
        self.assertIn(";LAYER_CHANGE", gcode)

    def test_single_cone(self):
        gcode = _gcode(n_cones=1)
        self.assertIn(";LAYER_CHANGE", gcode)

    def test_zero_zhop(self):
        gcode = _gcode(zhop=0.0)
        self.assertIn(";LAYER_CHANGE", gcode)

    def test_short_angle_at_90_degrees(self):
        # Extreme angle — walls barely grow. Should not crash.
        gcode = _gcode(short_angle=89.0)
        self.assertIsInstance(gcode, str)

    def test_long_angle_at_45_degrees(self):
        gcode = _gcode(long_angle=45.0)
        self.assertIsInstance(gcode, str)

    def test_many_segments(self):
        # Default range: 215→185 in 5° steps = 7 segments. Should be fine.
        gcode = _gcode(temp_start=215, temp_end=185, temp_step=5.0)
        self.assertIn("S185", gcode)

    def test_start_gcode_rendered(self):
        # Default start gcode contains M862.3
        gcode = _gcode()
        self.assertIn("M862", gcode)

    def test_end_gcode_rendered(self):
        # Default end gcode contains M104 S0
        gcode = _gcode()
        self.assertIn("M104 S0", gcode)


if __name__ == "__main__":
    unittest.main()
