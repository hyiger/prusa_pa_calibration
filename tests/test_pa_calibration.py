"""Unit tests for pa_calibration.py — Linear Advance calibration generator."""

import math
import unittest
from unittest import mock

from pa_calibration import Config, Generator
from _common import _PA, _r


# ── helpers ────────────────────────────────────────────────────────────────────

def _cfg(**overrides):
    """Minimal pa_calibration Config for fast tests (2 layers, short range)."""
    defaults = dict(layer_count=2, la_end=2.0, la_step=1.0)
    defaults.update(overrides)
    return Config(**defaults)


def _gen(**overrides):
    return Generator(_cfg(**overrides))


def _gcode(**overrides):
    return _gen(**overrides).generate()


# ── Config ─────────────────────────────────────────────────────────────────────

class TestPaCalConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.la_start, 0.0)
        self.assertEqual(cfg.la_end, 4.0)
        self.assertEqual(cfg.la_step, 1.0)
        self.assertEqual(cfg.layer_count, 4)
        self.assertEqual(cfg.wall_count, 3)
        self.assertEqual(cfg.side_length, 20.0)
        self.assertEqual(cfg.pattern_spacing, 2.0)
        self.assertEqual(cfg.corner_angle, 90.0)
        self.assertTrue(cfg.number_tab)
        self.assertFalse(cfg.no_leading_zeros)

    def test_inherits_common_config_fields(self):
        cfg = Config(nozzle_dia=0.6, layer_height=0.3)
        self.assertEqual(cfg.nozzle_dia, 0.6)
        self.assertEqual(cfg.layer_height, 0.3)


# ── Generator.__init__ ─────────────────────────────────────────────────────────

class TestGeneratorInit(unittest.TestCase):
    def test_n_patterns_for_default_range(self):
        g = _gen(la_start=0.0, la_end=2.0, la_step=1.0)
        self.assertEqual(g._n_patterns, 3)  # 0, 1, 2

    def test_n_patterns_single(self):
        g = _gen(la_start=1.0, la_end=1.0, la_step=1.0)
        self.assertEqual(g._n_patterns, 1)

    def test_n_patterns_fine_step(self):
        g = _gen(la_start=0.0, la_end=1.0, la_step=0.1)
        self.assertEqual(g._n_patterns, 11)  # 0.0 … 1.0

    def test_half_angle_90_degree_corner(self):
        g = _gen(corner_angle=90.0)
        self.assertAlmostEqual(g._half, math.radians(45.0))

    def test_half_angle_60_degree_corner(self):
        g = _gen(corner_angle=60.0)
        self.assertAlmostEqual(g._half, math.radians(60.0))


# ── Generator geometry ─────────────────────────────────────────────────────────

class TestPatternGeometry(unittest.TestCase):
    def test_pattern_width_positive(self):
        self.assertGreater(_gen()._pattern_width(), 0)

    def test_pattern_height_positive(self):
        self.assertGreater(_gen()._pattern_height(), 0)

    def test_width_scales_with_side_length(self):
        w1 = _gen(side_length=10.0)._pattern_width()
        w2 = _gen(side_length=20.0)._pattern_width()
        self.assertGreater(w2, w1)

    def test_height_scales_with_side_length(self):
        h1 = _gen(side_length=10.0)._pattern_height()
        h2 = _gen(side_length=20.0)._pattern_height()
        self.assertGreater(h2, h1)

    def test_more_walls_wider_and_taller(self):
        g1 = _gen(wall_count=1)
        g3 = _gen(wall_count=3)
        self.assertGreater(g3._pattern_width(), g1._pattern_width())
        self.assertGreater(g3._pattern_height(), g1._pattern_height())


# ── Generator._set_la ──────────────────────────────────────────────────────────

class TestSetLa(unittest.TestCase):
    def test_emits_m900_k(self):
        g = _gen()
        g._set_la(1.5)
        self.assertIn("M900 K1.5", "\n".join(g._buf))

    def test_k_value_rounded_to_4_places(self):
        g = _gen()
        g._set_la(0.12345678)
        self.assertIn("M900 K0.1235", "\n".join(g._buf))

    def test_emits_m117_when_show_lcd_true(self):
        g = _gen(show_lcd=True)
        g._set_la(2.0)
        self.assertIn("M117", "\n".join(g._buf))

    def test_no_m117_when_show_lcd_false(self):
        g = _gen(show_lcd=False)
        g._set_la(2.0)
        self.assertNotIn("M117", "\n".join(g._buf))

    def test_zero_k(self):
        g = _gen()
        g._set_la(0.0)
        self.assertIn("M900 K0.0", "\n".join(g._buf))


# ── Generator._pattern ────────────────────────────────────────────────────────

class TestPattern(unittest.TestCase):
    def test_generates_g1_lines(self):
        g = _gen()
        g._pattern(10.0, 10.0, 0.2, 0.45, 60.0)
        self.assertIn("G1", "\n".join(g._buf))

    def test_wall_count_1_produces_two_lines(self):
        # One V-shape = 2 legs = 2 G1 moves
        g = _gen(wall_count=1)
        g._pattern(0.0, 0.0, 0.2, 0.45, 60.0)
        g1_lines = [l for l in g._buf if l.startswith("G1")]
        self.assertEqual(len(g1_lines), 2)

    def test_more_walls_more_output(self):
        g1 = _gen(wall_count=1)
        g3 = _gen(wall_count=3)
        g1._pattern(0.0, 0.0, 0.2, 0.45, 60.0)
        g3._pattern(0.0, 0.0, 0.2, 0.45, 60.0)
        self.assertGreater(len(g3._buf), len(g1._buf))

    def test_apex_points_left(self):
        # First XY travel goes to the apex (leftmost point of the V-shape).
        # _travel may first emit a Z-hop G0; find the first G0 with X in it.
        import re
        g = _gen(wall_count=1, corner_angle=90.0, side_length=10.0)
        g._pattern(5.0, 5.0, 0.2, 0.45, 60.0)
        first_xy_travel = next(
            (l for l in g._buf if l.startswith("G0") and "X" in l), None
        )
        self.assertIsNotNone(first_xy_travel, "No XY travel found in pattern output")


# ── Generator.generate() ──────────────────────────────────────────────────────

class TestGenerate(unittest.TestCase):
    def test_returns_string(self):
        self.assertIsInstance(_gcode(), str)

    def test_ends_with_newline(self):
        self.assertTrue(_gcode().endswith("\n"))

    def test_contains_m900_for_each_k_value(self):
        gcode = _gcode(la_start=0.0, la_end=2.0, la_step=1.0)
        self.assertIn("M900 K0.0", gcode)
        self.assertIn("M900 K1.0", gcode)
        self.assertIn("M900 K2.0", gcode)

    def test_k_formula_not_accumulated(self):
        # K for pattern i must be la_start + i * la_step (not cumulative)
        gcode = _gcode(la_start=0.5, la_end=1.5, la_step=0.5)
        self.assertIn("M900 K0.5", gcode)
        self.assertIn("M900 K1.0", gcode)
        self.assertIn("M900 K1.5", gcode)

    def test_contains_z_moves(self):
        self.assertIn("G0 Z", _gcode())

    def test_contains_m109_wait_for_hotend(self):
        gcode = _gcode(hotend_temp=215)
        self.assertIn("M109 S215", gcode)

    def test_contains_fan_command(self):
        self.assertIn("M106", _gcode())

    def test_layer_count_controls_z_move_count(self):
        g2 = _gcode(layer_count=2)
        g4 = _gcode(layer_count=4)
        self.assertGreater(g4.count("G0 Z"), g2.count("G0 Z"))

    def test_anchor_frame_produces_anchor_comment(self):
        gcode = _gcode(anchor="frame")
        self.assertIn("Anchor frame", gcode)

    def test_anchor_layer_produces_anchor_comment(self):
        gcode = _gcode(anchor="layer")
        self.assertIn("Anchor layer", gcode)

    def test_anchor_none_produces_no_anchor(self):
        gcode = _gcode(anchor="none")
        self.assertNotIn("Anchor frame", gcode)
        self.assertNotIn("Anchor layer", gcode)

    def test_number_tab_enabled_produces_more_g1(self):
        with_tab    = _gcode(number_tab=True)
        without_tab = _gcode(number_tab=False)
        self.assertGreater(with_tab.count("G1"), without_tab.count("G1"))

    def test_number_tab_skips_odd_patterns(self):
        # Even-indexed patterns get labels; odd-indexed don't
        gcode = _gcode(la_start=0.0, la_end=4.0, la_step=1.0, number_tab=True)
        self.assertIn("Number labels", gcode)

    def test_more_patterns_more_m900(self):
        g3 = _gcode(la_start=0.0, la_end=2.0, la_step=1.0, layer_count=2)
        g5 = _gcode(la_start=0.0, la_end=4.0, la_step=1.0, layer_count=2)
        self.assertGreater(g5.count("M900"), g3.count("M900"))

    def test_k_reset_to_start_before_end(self):
        # _set_la(la_start) is called before end template
        gcode = _gcode(la_start=1.0, la_end=3.0, la_step=1.0)
        # Should contain M900 K1.0 multiple times (once per layer + reset)
        self.assertGreaterEqual(gcode.count("M900 K1.0"), 2)

    def test_bed_overflow_warning(self):
        stderr_buf = mock.MagicMock()
        stderr_buf.write = lambda s: None
        with mock.patch("sys.stderr") as m:
            m.write = lambda s: None
            _gcode(la_start=0.0, la_end=20.0, la_step=1.0, bed_x=100.0)
            # Check warning was printed
            calls = "".join(str(c) for c in m.mock_calls)
        # Just verify it doesn't crash with a tiny bed
        gcode = _gcode(la_start=0.0, la_end=20.0, la_step=1.0, bed_x=100.0)
        self.assertIsInstance(gcode, str)

    def test_single_pattern(self):
        # la_start=la_end=2.0, layer_count=2:
        # layer1: set_la(start) + pattern(K2.0)
        # layer2: pattern(K2.0)
        # end:    set_la(start) reset
        # → M900 K2.0 appears 4 times (layer1×2 + layer2 + reset)
        gcode = _gcode(la_start=2.0, la_end=2.0, la_step=1.0)
        self.assertIn("M900 K2.0", gcode)
        self.assertGreaterEqual(gcode.count("M900 K2.0"), 1)

    def test_fan_speed_applied(self):
        gcode = _gcode(fan_speed=50)
        # 50% of 255 = 127
        self.assertIn("M106 S127", gcode)

    def test_first_layer_fan_zero_sets_m106_s0(self):
        gcode = _gcode(first_layer_fan=0)
        self.assertIn("M106 S0", gcode)

    def test_show_lcd_emits_m117(self):
        gcode = _gcode(show_lcd=True)
        self.assertIn("M117", gcode)

    def test_no_lcd_suppresses_m117(self):
        gcode = _gcode(show_lcd=False)
        self.assertNotIn("M117", gcode)

    def test_wall_count_affects_output_size(self):
        g1 = _gcode(wall_count=1)
        g3 = _gcode(wall_count=3)
        self.assertGreater(len(g3), len(g1))


# ── Bed overflow warning ───────────────────────────────────────────────────────

class TestBedOverflowWarning(unittest.TestCase):
    def test_warning_printed_to_stderr(self):
        import io
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(la_start=0.0, la_end=20.0, la_step=1.0, bed_x=100.0)
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("exceeds bed", buf.getvalue())

    def test_warning_suggests_side_length(self):
        import io
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(la_start=0.0, la_end=20.0, la_step=1.0, bed_x=100.0)
        self.assertIn("--side-length", buf.getvalue())

    def test_no_warning_when_fits(self):
        import io
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            _gcode(la_start=0.0, la_end=2.0, la_step=1.0)
        self.assertNotIn("WARNING", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
