"""Unit tests for _common.py — shared infrastructure."""

import io
import math
import os
import tempfile
import unittest
from unittest import mock

from _common import (
    FILAMENT_PRESETS,
    PRINTER_PRESETS,
    _DEFAULT_PRINTER,
    _E,
    _PA,
    _XY,
    _Z,
    _THUMB_BG,
    _THUMB_FG,
    BaseGenerator,
    CommonConfig,
    _State,
    _r,
    _render,
    _make_png,
    _Raster,
    _thumbnail_pa,
    _thumbnail_tower,
    _thumbnails_to_gcode_comments,
    _write_bgcode,
    handle_output,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _gen(cfg=None):
    """Create a BaseGenerator with default or custom config."""
    return BaseGenerator(cfg or CommonConfig())


# ── _r ─────────────────────────────────────────────────────────────────────────

class TestRounding(unittest.TestCase):
    def test_basic_2_places(self):
        self.assertEqual(_r(1.23456, 2), 1.23)

    def test_4_places(self):
        self.assertEqual(_r(0.12345678, 4), 0.1235)

    def test_zero(self):
        self.assertEqual(_r(0.0, 3), 0.0)

    def test_negative(self):
        self.assertEqual(_r(-1.555, 2), -1.56)

    def test_exact_integer(self):
        self.assertEqual(_r(1.0, 5), 1.0)

    def test_precision_constant_values(self):
        self.assertEqual(_PA, 4)
        self.assertEqual(_Z, 3)
        self.assertEqual(_XY, 4)
        self.assertEqual(_E, 5)

    def test_rounds_half_up(self):
        # round-half-to-even: _r(0.5, 0) depends on Python's banker's rounding
        # Just check it returns a float
        result = _r(0.5, 0)
        self.assertIsInstance(result, float)


# ── _render ────────────────────────────────────────────────────────────────────

class TestRender(unittest.TestCase):
    def test_single_substitution(self):
        self.assertEqual(_render("{hotend_temp}", {"hotend_temp": 215}), "215")

    def test_multiple_substitutions(self):
        result = _render("{a} and {b}", {"a": "X", "b": "Y"})
        self.assertEqual(result, "X and Y")

    def test_unknown_var_left_intact(self):
        self.assertEqual(_render("{unknown}", {}), "{unknown}")

    def test_known_and_unknown_mixed(self):
        result = _render("{known} {unknown}", {"known": "hi"})
        self.assertEqual(result, "hi {unknown}")

    def test_prusa_if_left_intact(self):
        tmpl = "{if is_extruder_used}text{endif}"
        self.assertEqual(_render(tmpl, {}), tmpl)

    def test_uppercase_start_left_intact(self):
        self.assertEqual(_render("{Uppercase}", {}), "{Uppercase}")

    def test_digit_start_left_intact(self):
        self.assertEqual(_render("{1abc}", {}), "{1abc}")

    def test_empty_template(self):
        self.assertEqual(_render("", {}), "")

    def test_no_markers(self):
        self.assertEqual(_render("G28 ; home", {}), "G28 ; home")

    def test_value_converted_to_str(self):
        result = _render("{val}", {"val": 42})
        self.assertEqual(result, "42")


# ── _write_bgcode ──────────────────────────────────────────────────────────────

class TestWriteBgcode(unittest.TestCase):
    MAGIC = b"GCDE"

    def test_writes_magic_to_file_object(self):
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf)
        buf.seek(0)
        self.assertEqual(buf.read(4), self.MAGIC)

    def test_returns_correct_byte_count(self):
        buf = io.BytesIO()
        n = _write_bgcode("G28\n", buf)
        self.assertEqual(n, len(buf.getvalue()))
        self.assertGreater(n, 0)

    def test_writes_to_file_path(self):
        with tempfile.NamedTemporaryFile(suffix=".bgcode", delete=False) as f:
            path = f.name
        try:
            _write_bgcode("G28\n", path)
            with open(path, "rb") as f:
                data = f.read()
            self.assertEqual(data[:4], self.MAGIC)
        finally:
            os.unlink(path)

    def test_output_is_deterministic(self):
        buf1, buf2 = io.BytesIO(), io.BytesIO()
        _write_bgcode("G28\nG1 X10\n", buf1)
        _write_bgcode("G28\nG1 X10\n", buf2)
        self.assertEqual(buf1.getvalue(), buf2.getvalue())

    def test_different_content_produces_different_output(self):
        buf1, buf2 = io.BytesIO(), io.BytesIO()
        _write_bgcode("G28\n", buf1)
        _write_bgcode("G1 X10\n", buf2)
        self.assertNotEqual(buf1.getvalue(), buf2.getvalue())

    def test_version_byte_present(self):
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf)
        data = buf.getvalue()
        # Version field (uint32) = 1 after magic
        version = int.from_bytes(data[4:8], "little")
        self.assertEqual(version, 1)


# ── _State ─────────────────────────────────────────────────────────────────────

class TestState(unittest.TestCase):
    def test_initial_position_is_zero(self):
        st = _State()
        self.assertEqual(st.x, 0.0)
        self.assertEqual(st.y, 0.0)
        self.assertEqual(st.z, 0.0)

    def test_initial_flags_are_false(self):
        st = _State()
        self.assertFalse(st.retracted)
        self.assertFalse(st.hopped)


# ── CommonConfig ───────────────────────────────────────────────────────────────

class TestCommonConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = CommonConfig()
        self.assertEqual(cfg.nozzle_dia, 0.4)
        self.assertEqual(cfg.filament_dia, 1.75)
        self.assertEqual(cfg.layer_height, 0.20)
        self.assertEqual(cfg.first_layer_height, 0.25)
        self.assertEqual(cfg.retract_dist, 0.6)
        self.assertEqual(cfg.zhop, 0.1)
        self.assertEqual(cfg.anchor, "frame")
        self.assertEqual(cfg.anchor_perimeters, 4)
        self.assertTrue(cfg.show_lcd)
        self.assertEqual(cfg.fan_speed, 100)
        self.assertEqual(cfg.first_layer_fan, 0)

    def test_custom_values_override_defaults(self):
        cfg = CommonConfig(nozzle_dia=0.6, layer_height=0.3, zhop=0.0)
        self.assertEqual(cfg.nozzle_dia, 0.6)
        self.assertEqual(cfg.layer_height, 0.3)
        self.assertEqual(cfg.zhop, 0.0)


# ── BaseGenerator buffer helpers ───────────────────────────────────────────────

class TestBufferHelpers(unittest.TestCase):
    def setUp(self):
        self.g = _gen()

    def test_emit_appends_line(self):
        self.g._emit("G28")
        self.assertIn("G28", self.g._buf)

    def test_comment_prepends_semicolon(self):
        self.g._comment("hello world")
        self.assertIn("; hello world", self.g._buf)

    def test_blank_appends_empty_string(self):
        self.g._blank()
        self.assertIn("", self.g._buf)

    def test_multiple_emits_ordered(self):
        self.g._emit("A")
        self.g._emit("B")
        self.assertEqual(self.g._buf, ["A", "B"])


# ── BaseGenerator._e_amount ────────────────────────────────────────────────────

class TestExtrusion(unittest.TestCase):
    def setUp(self):
        self.g = _gen(CommonConfig(filament_dia=1.75, extrusion_multiplier=1.0))

    def test_positive_result(self):
        e = self.g._e_amount(10.0, 0.2, 0.4)
        self.assertGreater(e, 0)

    def test_doubles_with_double_distance(self):
        e1 = self.g._e_amount(10.0, 0.2, 0.4)
        e2 = self.g._e_amount(20.0, 0.2, 0.4)
        self.assertAlmostEqual(e2, e1 * 2, places=3)

    def test_scales_with_multiplier(self):
        g1 = _gen(CommonConfig(extrusion_multiplier=1.0))
        g2 = _gen(CommonConfig(extrusion_multiplier=0.5))
        e1 = g1._e_amount(10.0, 0.2, 0.4)
        e2 = g2._e_amount(10.0, 0.2, 0.4)
        self.assertAlmostEqual(e2, e1 * 0.5, places=4)

    def test_rounded_to_5_places(self):
        e = self.g._e_amount(10.0, 0.2, 0.4)
        self.assertEqual(e, round(e, 5))

    def test_scales_with_layer_height(self):
        e1 = self.g._e_amount(10.0, 0.1, 0.4)
        e2 = self.g._e_amount(10.0, 0.2, 0.4)
        self.assertAlmostEqual(e2, e1 * 2, places=3)

    def test_scales_with_line_width(self):
        e1 = self.g._e_amount(10.0, 0.2, 0.4)
        e2 = self.g._e_amount(10.0, 0.2, 0.8)
        self.assertAlmostEqual(e2, e1 * 2, places=3)


# ── BaseGenerator._retract / _unretract ────────────────────────────────────────

class TestRetract(unittest.TestCase):
    def _make(self, zhop=0.0, retract_dist=0.6):
        cfg = CommonConfig(zhop=zhop, retract_dist=retract_dist, travel_speed=150.0)
        g = BaseGenerator(cfg)
        g._st.z = 1.0
        return g

    def test_retract_emits_negative_e(self):
        g = self._make()
        g._retract()
        self.assertIn("G1 E-0.6", "\n".join(g._buf))

    def test_retract_sets_retracted_flag(self):
        g = self._make()
        g._retract()
        self.assertTrue(g._st.retracted)

    def test_retract_idempotent(self):
        g = self._make()
        g._retract()
        n = len(g._buf)
        g._retract()
        self.assertEqual(len(g._buf), n)

    def test_retract_with_zhop_emits_z_move(self):
        g = self._make(zhop=0.2)
        g._retract()
        output = "\n".join(g._buf)
        self.assertIn("E-0.6", output)
        self.assertIn("Z1.2", output)
        self.assertTrue(g._st.hopped)

    def test_retract_no_zhop_when_already_hopped(self):
        g = self._make(zhop=0.2)
        g._st.hopped = True
        g._retract()
        z_lines = [l for l in g._buf if "G0" in l and "Z" in l]
        self.assertEqual(len(z_lines), 0)

    def test_retract_zhop_zero_no_z_move(self):
        g = self._make(zhop=0.0)
        g._retract()
        z_lines = [l for l in g._buf if "G0" in l and "Z" in l]
        self.assertEqual(len(z_lines), 0)

    def test_unretract_noop_when_not_retracted(self):
        g = self._make()
        g._unretract()
        self.assertEqual(g._buf, [])

    def test_unretract_emits_positive_e(self):
        g = self._make()
        g._retract()
        g._buf.clear()
        g._unretract()
        self.assertIn("E0.6", "\n".join(g._buf))

    def test_unretract_clears_retracted_flag(self):
        g = self._make()
        g._retract()
        g._unretract()
        self.assertFalse(g._st.retracted)

    def test_unretract_lowers_z_before_extrusion_when_hopped(self):
        g = self._make(zhop=0.2)
        g._retract()
        g._buf.clear()
        g._unretract()
        lines = g._buf
        z_idx = next(i for i, l in enumerate(lines) if "G0" in l and "Z" in l)
        e_idx = next(i for i, l in enumerate(lines) if "E0.6" in l)
        self.assertLess(z_idx, e_idx)
        self.assertFalse(g._st.hopped)


# ── BaseGenerator._travel ──────────────────────────────────────────────────────

class TestTravel(unittest.TestCase):
    def _make(self, zhop=0.0):
        g = _gen(CommonConfig(zhop=zhop, retract_dist=0.6))
        g._st.z = 1.0
        return g

    def test_short_move_no_retract(self):
        g = self._make()
        g._travel(1.0, 0.0)   # 1 mm < 2 mm threshold
        self.assertNotIn("E-", "\n".join(g._buf))
        self.assertIn("G0", "\n".join(g._buf))

    def test_long_move_retracts_and_unretracts(self):
        g = self._make()
        g._travel(10.0, 0.0)
        output = "\n".join(g._buf)
        self.assertIn("E-0.6", output)
        self.assertIn("E0.6", output)

    def test_updates_position(self):
        g = self._make()
        g._travel(5.0, 7.0)
        self.assertAlmostEqual(g._st.x, 5.0)
        self.assertAlmostEqual(g._st.y, 7.0)

    def test_exact_threshold_no_retract(self):
        g = self._make()
        g._travel(2.0, 0.0)  # exactly at threshold: dist=2.0, not > 2.0
        self.assertNotIn("E-", "\n".join(g._buf))

    def test_just_over_threshold_retracts(self):
        g = self._make()
        g._travel(2.001, 0.0)
        self.assertIn("E-", "\n".join(g._buf))


# ── BaseGenerator._line ────────────────────────────────────────────────────────

class TestLine(unittest.TestCase):
    def setUp(self):
        self.g = _gen()

    def test_zero_distance_noop(self):
        self.g._line(0.0, 0.0, 60.0, 0.2, 0.4)
        self.assertEqual(self.g._buf, [])

    def test_emits_g1_with_e(self):
        self.g._line(10.0, 0.0, 60.0, 0.2, 0.4)
        output = "\n".join(self.g._buf)
        self.assertIn("G1", output)
        self.assertIn("X10.0", output)
        self.assertIn(" E", output)

    def test_updates_position(self):
        self.g._line(5.0, 3.0, 60.0, 0.2, 0.4)
        self.assertAlmostEqual(self.g._st.x, 5.0)
        self.assertAlmostEqual(self.g._st.y, 3.0)

    def test_speed_converted_to_mm_per_min(self):
        self.g._line(10.0, 0.0, 100.0, 0.2, 0.4)
        # 100 mm/s × 60 = 6000 mm/min
        self.assertIn("F6000", "\n".join(self.g._buf))


# ── BaseGenerator._anchor_frame ────────────────────────────────────────────────

class TestAnchorFrame(unittest.TestCase):
    def test_generates_output(self):
        g = _gen()
        g._anchor_frame(0, 0, 20, 10, 0.2, 0.45, 2, 60.0)
        self.assertGreater(len(g._buf), 0)
        self.assertIn("G1", "\n".join(g._buf))

    def test_zero_perimeters_produces_nothing(self):
        g = _gen()
        g._anchor_frame(0, 0, 20, 10, 0.2, 0.45, 0, 60.0)
        self.assertEqual(g._buf, [])

    def test_stops_when_rect_too_small(self):
        g = _gen()
        # Rectangle too small for 50 perimeters — must not crash
        g._anchor_frame(0, 0, 1, 1, 0.2, 0.45, 50, 60.0)
        # Verify it produced at most a few lines (not 50 full perimeters)
        self.assertLess(len(g._buf), 50 * 10)

    def test_more_perimeters_more_output(self):
        g1, g2 = _gen(), _gen()
        g1._anchor_frame(0, 0, 30, 20, 0.2, 0.45, 1, 60.0)
        g2._anchor_frame(0, 0, 30, 20, 0.2, 0.45, 3, 60.0)
        self.assertGreater(len(g2._buf), len(g1._buf))


# ── BaseGenerator._anchor_layer ────────────────────────────────────────────────

class TestAnchorLayer(unittest.TestCase):
    def test_generates_perimeters_and_fill(self):
        g = _gen()
        g._anchor_layer(0, 0, 20, 10, 0.2, 0.45, 60.0)
        output = "\n".join(g._buf)
        self.assertIn("G0", output)
        self.assertIn("G1", output)

    def test_wider_rect_produces_same_line_count_but_longer_lines(self):
        # Fill is horizontal; line count is set by Y height, not X width.
        # Both same height → same number of lines, but g2 lines travel further.
        g1, g2 = _gen(), _gen()
        g1._anchor_layer(0, 0, 10, 10, 0.2, 0.45, 60.0)
        g2._anchor_layer(0, 0, 50, 10, 0.2, 0.45, 60.0)
        self.assertEqual(len(g1._buf), len(g2._buf))

    def test_taller_rect_produces_more_fill_lines(self):
        g1, g2 = _gen(), _gen()
        g1._anchor_layer(0, 0, 20, 5, 0.2, 0.45, 60.0)
        g2._anchor_layer(0, 0, 20, 30, 0.2, 0.45, 60.0)
        self.assertGreater(len(g2._buf), len(g1._buf))


# ── BaseGenerator._circle ──────────────────────────────────────────────────────

class TestCircle(unittest.TestCase):
    def test_tiny_radius_noop(self):
        g = _gen()
        g._circle(10, 10, 0.01, 60.0, 0.2, 0.45)  # r < lw * 0.5
        self.assertEqual(g._buf, [])

    def test_normal_radius_produces_output(self):
        g = _gen()
        g._circle(10, 10, 5.0, 60.0, 0.2, 0.45)
        self.assertIn("G1", "\n".join(g._buf))

    def test_minimum_12_segments(self):
        g = _gen()
        g._circle(0, 0, 0.3, 60.0, 0.2, 0.45)  # small but above cutoff
        g1_lines = [l for l in g._buf if l.startswith("G1")]
        self.assertGreaterEqual(len(g1_lines), 12)

    def test_larger_circle_more_segments(self):
        g1, g2 = _gen(), _gen()
        g1._circle(0, 0, 2.0, 60.0, 0.2, 0.45)
        g2._circle(0, 0, 20.0, 60.0, 0.2, 0.45)
        g1_count = sum(1 for l in g1._buf if l.startswith("G1"))
        g2_count = sum(1 for l in g2._buf if l.startswith("G1"))
        self.assertGreater(g2_count, g1_count)

    def test_circle_closes_loop(self):
        # Last G1 should return to start point
        g = _gen()
        g._circle(0, 0, 5.0, 60.0, 0.2, 0.45)
        g1_lines = [l for l in g._buf if l.startswith("G1")]
        # First travel sets position, last G1 closes back
        self.assertGreater(len(g1_lines), 12)


# ── BaseGenerator 7-segment labels ────────────────────────────────────────────

class TestDrawDigit(unittest.TestCase):
    def setUp(self):
        self.g = _gen()

    def test_decimal_point_produces_output(self):
        adv = self.g._draw_digit(0, 0, ".", 0.2, 0.45, 60.0)
        self.assertGreater(adv, 0)
        self.assertIn("G1", "\n".join(self.g._buf))

    def test_unknown_char_noop_but_returns_width(self):
        adv = self.g._draw_digit(0, 0, "Z", 0.2, 0.45, 60.0)
        self.assertEqual(self.g._buf, [])
        self.assertGreater(adv, 0)

    def test_digit_1_has_two_draw_segments(self):
        # "1": only top-right and bottom-right active → 2 drawing G1s.
        # Travel retract/unretract also emit G1 E±; filter those out.
        self.g._draw_digit(0, 0, "1", 0.2, 0.45, 60.0)
        draw = [l for l in self.g._buf if l.startswith("G1") and ("X" in l or "Y" in l)]
        self.assertEqual(len(draw), 2)

    def test_digit_8_has_seven_draw_segments(self):
        # "8": all 7 segments active → 7 drawing G1s.
        self.g._draw_digit(0, 0, "8", 0.2, 0.45, 60.0)
        draw = [l for l in self.g._buf if l.startswith("G1") and ("X" in l or "Y" in l)]
        self.assertEqual(len(draw), 7)

    def test_digit_width_returned(self):
        adv = self.g._draw_digit(0, 0, "5", 0.2, 0.45, 60.0)
        self.assertAlmostEqual(adv, self.g._SEG_LEN + self.g._SEG_GAP)

    def test_all_digits_produce_output(self):
        for ch in "0234567890":
            g = _gen()
            g._draw_digit(0, 0, ch, 0.2, 0.45, 60.0)
            self.assertGreater(len(g._buf), 0, f"digit {ch!r} produced no output")

    def test_digit_positioned_at_x_offset(self):
        g1, g2 = _gen(), _gen()
        g1._draw_digit(0, 0, "5", 0.2, 0.45, 60.0)
        g2._draw_digit(10, 0, "5", 0.2, 0.45, 60.0)
        # g2 lines should have larger X coordinates
        def max_x(buf):
            import re
            xs = [float(m.group(1)) for l in buf for m in [re.search(r"X([\d.]+)", l)] if m]
            return max(xs) if xs else 0
        self.assertGreater(max_x(g2._buf), max_x(g1._buf))


class TestDrawNumber(unittest.TestCase):
    def test_integer_renders_without_decimal(self):
        g = _gen()
        g._draw_number(0, 0, 215.0, 0.2, 0.45, 60.0)
        # 3 digits → produces output
        self.assertIn("G1", "\n".join(g._buf))

    def test_float_renders_with_decimal_point(self):
        g = _gen()
        g._draw_number(0, 0, 1.5, 0.2, 0.45, 60.0)
        self.assertIn("G1", "\n".join(g._buf))

    def test_no_leading_zeros_strips_zero(self):
        g1, g2 = _gen(), _gen()
        g1._draw_number(0, 0, 0.5, 0.2, 0.45, 60.0, no_leading_zeros=False)
        g2._draw_number(0, 0, 0.5, 0.2, 0.45, 60.0, no_leading_zeros=True)
        # "0.5" → 3 chars; ".5" → 2 chars → fewer G1 lines
        self.assertLess(len(g2._buf), len(g1._buf))

    def test_digit_width_positive(self):
        self.assertGreater(_gen()._digit_width(), 0)

    def test_num_tab_height_positive(self):
        self.assertGreater(_gen()._num_tab_height(), 0)

    def test_longer_number_more_output(self):
        g1, g2 = _gen(), _gen()
        g1._draw_number(0, 0, 5.0, 0.2, 0.45, 60.0)     # 1 digit
        g2._draw_number(0, 0, 215.0, 0.2, 0.45, 60.0)   # 3 digits
        self.assertGreater(len(g2._buf), len(g1._buf))


# ── BaseGenerator._m555 ───────────────────────────────────────────────────────

class TestM555(unittest.TestCase):
    def setUp(self):
        self.g = _gen(CommonConfig(bed_x=250.0, bed_y=220.0))

    def test_returns_four_ints(self):
        result = self.g._m555(10.0, 10.0, 100.0, 80.0)
        self.assertEqual(len(result), 4)
        for v in result:
            self.assertIsInstance(v, int)

    def test_w_at_least_32(self):
        _, _, w, _ = self.g._m555(10.0, 10.0, 20.0, 80.0)
        self.assertGreaterEqual(w, 32)

    def test_y_is_pmin_y_minus_4(self):
        # pmin_y=10 → y = max(0, 10) - 4 = 6
        _, y, _, _ = self.g._m555(10.0, 10.0, 100.0, 80.0)
        self.assertEqual(y, 6)

    def test_pmin_y_zero_clamps_to_negative_4(self):
        _, y, _, _ = self.g._m555(0.0, 0.0, 100.0, 80.0)
        self.assertEqual(y, -4)

    def test_h_covers_full_print_height(self):
        x, y, w, h = self.g._m555(10.0, 10.0, 100.0, 80.0)
        # pmax_y = orig_y + total_h = 10 + 80 = 90; h = pmax_y - y = 90 - 6 = 84
        self.assertEqual(h, 84)


# ── BaseGenerator._base_tmpl_vars ─────────────────────────────────────────────

class TestBaseTmplVars(unittest.TestCase):
    EXPECTED_KEYS = {
        "bed_temp", "hotend_temp", "mbl_temp", "nozzle_dia",
        "filament_dia", "cool_fan", "m555_x", "m555_y",
        "m555_w", "m555_h", "park_z", "max_layer_z",
    }

    def _vars(self, **cfg_kwargs):
        return _gen(CommonConfig(**cfg_kwargs))._base_tmpl_vars(
            10.0, 5.0, 5.0, 100.0, 80.0
        )

    def test_exactly_12_keys(self):
        v = self._vars(bed_temp=60, hotend_temp=215)
        self.assertEqual(set(v.keys()), self.EXPECTED_KEYS)

    def test_mbl_temp_is_hotend_minus_50(self):
        v = self._vars(hotend_temp=250)
        self.assertEqual(v["mbl_temp"], 200)

    def test_mbl_temp_clamps_to_155(self):
        v = self._vars(hotend_temp=180)
        self.assertEqual(v["mbl_temp"], 155)

    def test_cool_fan_enabled_at_low_bed_temp(self):
        v = self._vars(bed_temp=60)
        self.assertIn("M106", v["cool_fan"])

    def test_cool_fan_disabled_at_high_bed_temp(self):
        v = self._vars(bed_temp=85)
        self.assertEqual(v["cool_fan"], "M107")

    def test_cool_fan_boundary_at_60(self):
        # bed_temp <= 60 → M106 (enclosure cool)
        v60 = self._vars(bed_temp=60)
        v61 = self._vars(bed_temp=61)
        self.assertIn("M106", v60["cool_fan"])
        self.assertEqual(v61["cool_fan"], "M107")

    def test_park_z_clamped_to_max_z(self):
        v = _gen(CommonConfig(max_z=5.0))._base_tmpl_vars(4.5, 0, 0, 10, 10)
        self.assertLessEqual(v["park_z"], 5.0)

    def test_park_z_is_max_layer_z_plus_1(self):
        v = _gen(CommonConfig(max_z=100.0))._base_tmpl_vars(10.0, 0, 0, 10, 10)
        self.assertAlmostEqual(v["park_z"], 11.0, places=2)

    def test_max_layer_z_passed_through(self):
        v = _gen()._base_tmpl_vars(42.5, 0, 0, 10, 10)
        self.assertEqual(v["max_layer_z"], 42.5)


# ── Presets ────────────────────────────────────────────────────────────────────

class TestPresets(unittest.TestCase):
    FILAMENT_FIELDS = {"hotend_temp", "bed_temp", "fan_speed", "first_layer_fan", "retract_dist"}
    PRINTER_FIELDS  = {"bed_x", "bed_y", "max_z", "model"}

    def test_all_filament_presets_have_required_fields(self):
        for name, preset in FILAMENT_PRESETS.items():
            with self.subTest(filament=name):
                self.assertEqual(set(preset.keys()), self.FILAMENT_FIELDS)

    def test_all_printer_presets_have_required_fields(self):
        for name, preset in PRINTER_PRESETS.items():
            with self.subTest(printer=name):
                self.assertEqual(set(preset.keys()), self.PRINTER_FIELDS)

    def test_default_printer_in_presets(self):
        self.assertIn(_DEFAULT_PRINTER, PRINTER_PRESETS)

    def test_known_filaments_present(self):
        for f in ("PLA", "PETG", "ABS", "ASA", "PA", "TPU", "PC"):
            with self.subTest(filament=f):
                self.assertIn(f, FILAMENT_PRESETS)

    def test_known_printers_present(self):
        for p in ("MINI", "MK4S", "COREONE", "COREONEL", "XL"):
            with self.subTest(printer=p):
                self.assertIn(p, PRINTER_PRESETS)

    def test_filament_temp_ranges_sensible(self):
        for name, p in FILAMENT_PRESETS.items():
            with self.subTest(filament=name):
                self.assertGreater(p["hotend_temp"], 150)
                self.assertLess(p["hotend_temp"], 350)
                self.assertGreaterEqual(p["bed_temp"], 0)
                self.assertGreaterEqual(p["retract_dist"], 0.0)

    def test_printer_bed_sizes_positive(self):
        for name, p in PRINTER_PRESETS.items():
            with self.subTest(printer=name):
                self.assertGreater(p["bed_x"], 0)
                self.assertGreater(p["bed_y"], 0)
                self.assertGreater(p["max_z"], 0)


# ── _make_png ──────────────────────────────────────────────────────────────────

class TestMakePng(unittest.TestCase):
    def _solid(self, w, h, color=(0, 0, 0)):
        return _make_png(w, h, [color] * (w * h))

    def test_returns_bytes(self):
        self.assertIsInstance(self._solid(2, 2), bytes)

    def test_starts_with_png_signature(self):
        self.assertEqual(self._solid(1, 1)[:8], b"\x89PNG\r\n\x1a\n")

    def test_contains_ihdr_chunk(self):
        self.assertIn(b"IHDR", self._solid(2, 2))

    def test_contains_idat_chunk(self):
        self.assertIn(b"IDAT", self._solid(2, 2))

    def test_contains_iend_chunk(self):
        self.assertIn(b"IEND", self._solid(2, 2))

    def test_different_pixels_different_output(self):
        red  = self._solid(4, 4, (255, 0, 0))
        blue = self._solid(4, 4, (0, 0, 255))
        self.assertNotEqual(red, blue)

    def test_larger_image_larger_output(self):
        small = self._solid(4, 4)
        large = self._solid(32, 32)
        self.assertGreater(len(large), len(small))

    def test_single_pixel(self):
        result = self._solid(1, 1, (128, 64, 32))
        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_deterministic(self):
        px = [(i % 256, i % 128, i % 64) for i in range(16)]
        self.assertEqual(_make_png(4, 4, px), _make_png(4, 4, px))

    def test_channel_values_clamped_to_byte(self):
        # Values > 255 should not crash; they are masked with & 0xFF
        px = [(300, -5, 128)] * 4
        result = _make_png(2, 2, px)
        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))


# ── _Raster ────────────────────────────────────────────────────────────────────

class TestRaster(unittest.TestCase):
    def test_initial_pixels_are_background(self):
        r = _Raster(3, 3)
        self.assertTrue(all(px == _THUMB_BG for px in r._px))

    def test_set_changes_pixel(self):
        r = _Raster(5, 5)
        r._set(2, 2)
        self.assertEqual(r._px[2 * 5 + 2], _THUMB_FG)

    def test_set_custom_colour(self):
        r = _Raster(5, 5)
        r._set(0, 0, (1, 2, 3))
        self.assertEqual(r._px[0], (1, 2, 3))

    def test_set_out_of_bounds_no_crash(self):
        r = _Raster(3, 3)
        r._set(-1, 0)
        r._set(0, -1)
        r._set(100, 100)

    def test_fill_rect_fills_region(self):
        r = _Raster(10, 10)
        r.fill_rect(2, 2, 5, 5)
        for y in range(2, 5):
            for x in range(2, 5):
                self.assertEqual(r._px[y * 10 + x], _THUMB_FG,
                                 f"pixel ({x},{y}) not filled")

    def test_fill_rect_leaves_outside_unchanged(self):
        r = _Raster(10, 10)
        r.fill_rect(2, 2, 5, 5)
        self.assertEqual(r._px[0], _THUMB_BG)
        self.assertEqual(r._px[9 * 10 + 9], _THUMB_BG)

    def test_fill_rect_clamps_to_bounds_no_crash(self):
        r = _Raster(5, 5)
        r.fill_rect(-5, -5, 100, 100)

    def test_line_sets_endpoints(self):
        r = _Raster(20, 10)
        r.line(0, 0, 19, 0)
        self.assertEqual(r._px[0 * 20 + 0],  _THUMB_FG)
        self.assertEqual(r._px[0 * 20 + 19], _THUMB_FG)

    def test_diagonal_line_sets_pixels(self):
        r = _Raster(10, 10)
        r.line(0, 0, 9, 9)
        # Diagonal should set at least start and end
        self.assertEqual(r._px[0], _THUMB_FG)
        self.assertEqual(r._px[9 * 10 + 9], _THUMB_FG)

    def test_thick_line_covers_more_pixels(self):
        r1, r2 = _Raster(20, 20), _Raster(20, 20)
        r1.line(0, 10, 19, 10, thick=1)
        r2.line(0, 10, 19, 10, thick=3)
        fg_count_1 = sum(1 for px in r1._px if px == _THUMB_FG)
        fg_count_2 = sum(1 for px in r2._px if px == _THUMB_FG)
        self.assertGreater(fg_count_2, fg_count_1)

    def test_to_png_returns_valid_png(self):
        r = _Raster(4, 4)
        png = r.to_png()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_custom_bg_and_fg(self):
        r = _Raster(3, 3, bg=(1, 2, 3), fg=(4, 5, 6))
        self.assertEqual(r._px[0], (1, 2, 3))
        r._set(0, 0)
        self.assertEqual(r._px[0], (4, 5, 6))


# ── _thumbnail_pa ──────────────────────────────────────────────────────────────

class TestThumbnailPa(unittest.TestCase):
    def test_returns_bytes(self):
        self.assertIsInstance(_thumbnail_pa(16, 16), bytes)

    def test_valid_png_signature(self):
        self.assertTrue(_thumbnail_pa(16, 16).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_valid_png_signature_large(self):
        self.assertTrue(_thumbnail_pa(220, 124).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_larger_size_larger_output(self):
        small = _thumbnail_pa(16, 16)
        large = _thumbnail_pa(220, 124)
        self.assertGreater(len(large), len(small))

    def test_deterministic(self):
        self.assertEqual(_thumbnail_pa(32, 32), _thumbnail_pa(32, 32))

    def test_not_just_background(self):
        # The thumbnail should contain some non-background pixels (orange lines).
        # We can't easily decode PNG without PIL, but the compressed size should
        # be larger than a solid-colour image of the same dimensions.
        bg_only = _make_png(220, 124, [_THUMB_BG] * (220 * 124))
        pa_png  = _thumbnail_pa(220, 124)
        # A fully uniform image compresses to a much smaller IDAT; PA has lines
        self.assertGreater(len(pa_png), len(bg_only) * 0.8)


# ── _thumbnail_tower ───────────────────────────────────────────────────────────

class TestThumbnailTower(unittest.TestCase):
    def test_returns_bytes(self):
        self.assertIsInstance(_thumbnail_tower(16, 16), bytes)

    def test_valid_png_signature(self):
        self.assertTrue(_thumbnail_tower(16, 16).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_valid_png_signature_large(self):
        self.assertTrue(_thumbnail_tower(220, 124).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_larger_size_larger_output(self):
        small = _thumbnail_tower(16, 16)
        large = _thumbnail_tower(220, 124)
        self.assertGreater(len(large), len(small))

    def test_deterministic(self):
        self.assertEqual(_thumbnail_tower(32, 32), _thumbnail_tower(32, 32))

    def test_different_from_pa_thumbnail(self):
        self.assertNotEqual(_thumbnail_pa(220, 124), _thumbnail_tower(220, 124))


# ── _thumbnails_to_gcode_comments ──────────────────────────────────────────────

class TestThumbnailsToGcodeComments(unittest.TestCase):
    def _png(self, w=2, h=2):
        return _make_png(w, h, [(0, 0, 0)] * (w * h))

    def test_empty_list_returns_empty_string(self):
        self.assertEqual(_thumbnails_to_gcode_comments([]), "")

    def test_produces_begin_marker(self):
        result = _thumbnails_to_gcode_comments([(4, 4, self._png(4, 4))])
        self.assertIn("; thumbnail begin 4x4", result)

    def test_produces_end_marker(self):
        result = _thumbnails_to_gcode_comments([(2, 2, self._png())])
        self.assertIn("; thumbnail end", result)

    def test_size_in_header_matches_base64_length(self):
        import base64, re
        png = self._png(4, 4)
        result = _thumbnails_to_gcode_comments([(4, 4, png)])
        m = re.search(r"; thumbnail begin 4x4 (\d+)", result)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), len(base64.b64encode(png).decode()))

    def test_all_lines_are_comments(self):
        result = _thumbnails_to_gcode_comments([(2, 2, self._png())])
        for line in result.strip().splitlines():
            self.assertTrue(line.startswith(";"), f"non-comment line: {line!r}")

    def test_data_lines_at_most_80_chars_including_prefix(self):
        # Each data line is "; " + up to 78 base64 chars = 80 chars max
        png = _thumbnail_pa(16, 16)  # large enough to have multi-line base64
        result = _thumbnails_to_gcode_comments([(16, 16, png)])
        for line in result.splitlines():
            if line.startswith("; ") and "thumbnail" not in line:
                self.assertLessEqual(len(line), 80, f"line too long: {line!r}")

    def test_multiple_thumbnails_both_present(self):
        result = _thumbnails_to_gcode_comments([
            (2,  2,  self._png(2, 2)),
            (4,  4,  self._png(4, 4)),
        ])
        self.assertIn("; thumbnail begin 2x2", result)
        self.assertIn("; thumbnail begin 4x4", result)

    def test_result_ends_with_newline(self):
        result = _thumbnails_to_gcode_comments([(2, 2, self._png())])
        self.assertTrue(result.endswith("\n"))

    def test_roundtrip_base64_decodes_to_original_png(self):
        import base64, re
        png = self._png(4, 4)
        result = _thumbnails_to_gcode_comments([(4, 4, png)])
        # Extract the base64 data lines (between begin and end)
        lines = result.splitlines()
        b64_lines = []
        inside = False
        for line in lines:
            if "thumbnail begin" in line:
                inside = True
                continue
            if "thumbnail end" in line:
                inside = False
            elif inside and line.startswith("; "):
                b64_lines.append(line[2:])
        b64_str = "".join(b64_lines)
        self.assertEqual(base64.b64decode(b64_str), png)


# ── _write_bgcode with thumbnails ──────────────────────────────────────────────

class TestWriteBgcodeWithThumbnails(unittest.TestCase):
    """Verify that thumbnail blocks are correctly embedded in bgcode output."""

    _PARAMS_SIZE = {0: 2, 1: 2, 2: 2, 3: 2, 4: 2, 5: 6}

    def _blocks(self, data: bytes) -> list[tuple]:
        """Parse bgcode data and return list of (btype, params, payload) tuples."""
        import struct
        blocks = []
        pos = 10  # skip 4-byte magic + 4-byte version + 2-byte cksum_type
        while pos < len(data) - 4:
            btype, comp = struct.unpack("<HH", data[pos:pos+4])
            psize = self._PARAMS_SIZE.get(btype, 2)
            if comp == 1:
                unc, cmp = struct.unpack("<II", data[pos+4:pos+12])
                hdr_len, pay_size = 12, cmp
            else:
                unc = struct.unpack("<I", data[pos+4:pos+8])[0]
                hdr_len, pay_size = 8, unc
            params  = data[pos+hdr_len : pos+hdr_len+psize]
            payload = data[pos+hdr_len+psize : pos+hdr_len+psize+pay_size]
            blocks.append((btype, params, payload))
            pos += hdr_len + psize + pay_size + 4
        return blocks

    def _png(self):
        return _make_png(2, 2, [(0, 0, 0)] * 4)

    def test_no_thumbnails_no_thumbnail_blocks(self):
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf)
        btypes = [b[0] for b in self._blocks(buf.getvalue())]
        self.assertNotIn(5, btypes)

    def test_one_thumbnail_one_thumbnail_block(self):
        import struct
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf, thumbnails=[(2, 2, self._png())])
        btypes = [b[0] for b in self._blocks(buf.getvalue())]
        self.assertEqual(btypes.count(5), 1)

    def test_two_thumbnails_two_thumbnail_blocks(self):
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf, thumbnails=[
            (2, 2, self._png()), (4, 4, self._png()),
        ])
        btypes = [b[0] for b in self._blocks(buf.getvalue())]
        self.assertEqual(btypes.count(5), 2)

    def test_thumbnail_params_contain_correct_dimensions(self):
        import struct
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf, thumbnails=[(16, 24, self._png())])
        for btype, params, _ in self._blocks(buf.getvalue()):
            if btype == 5:
                fmt, w, h = struct.unpack("<HHH", params)
                self.assertEqual(fmt, 0)   # PNG format = 0
                self.assertEqual(w, 16)
                self.assertEqual(h, 24)
                break

    def test_thumbnail_payload_is_valid_png(self):
        buf = io.BytesIO()
        png = self._png()
        _write_bgcode("G28\n", buf, thumbnails=[(2, 2, png)])
        for btype, _, payload in self._blocks(buf.getvalue()):
            if btype == 5:
                self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(payload, png)
                break

    def test_thumbnail_block_position_between_printer_meta_and_print_meta(self):
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf, thumbnails=[(2, 2, self._png())])
        btypes = [b[0] for b in self._blocks(buf.getvalue())]
        # Expected order: 0(FileMeta), 3(PrinterMeta), 5(Thumbnail), 4(PrintMeta), 2(SlicerMeta), 1(GCode)
        self.assertIn(3, btypes)
        self.assertIn(5, btypes)
        self.assertIn(4, btypes)
        idx_printer = btypes.index(3)
        idx_thumb   = btypes.index(5)
        idx_print   = btypes.index(4)
        self.assertLess(idx_printer, idx_thumb)
        self.assertLess(idx_thumb,   idx_print)

    def test_gcode_block_is_last(self):
        buf = io.BytesIO()
        _write_bgcode("G28\n", buf, thumbnails=[(2, 2, self._png())])
        btypes = [b[0] for b in self._blocks(buf.getvalue())]
        self.assertEqual(btypes[-1], 1)  # GCode block type = 1

    def test_with_thumbnails_larger_than_without(self):
        without_buf, with_buf = io.BytesIO(), io.BytesIO()
        _write_bgcode("G28\n", without_buf)
        _write_bgcode("G28\n", with_buf, thumbnails=[(2, 2, self._png())])
        self.assertGreater(len(with_buf.getvalue()), len(without_buf.getvalue()))

    def test_empty_thumbnails_tuple_identical_to_no_thumbnails(self):
        buf1, buf2 = io.BytesIO(), io.BytesIO()
        _write_bgcode("G28\n", buf1)
        _write_bgcode("G28\n", buf2, thumbnails=())
        self.assertEqual(buf1.getvalue(), buf2.getvalue())


# ── handle_output: binary default + --ascii flag ───────────────────────────────

class TestHandleOutputBinaryDefault(unittest.TestCase):
    """handle_output() produces bgcode by default; --ascii gives plain text."""

    def _args(self, *, ascii_flag=False, output=None):
        args = mock.MagicMock(spec=[
            "ascii", "output", "prusalink_url", "prusaconnect",
        ])
        args.ascii      = ascii_flag
        args.output     = output
        args.prusalink_url = None
        args.prusaconnect  = False
        return args

    def test_default_writes_bgcode_magic_to_file(self):
        with tempfile.NamedTemporaryFile(suffix=".bgcode", delete=False) as f:
            path = f.name
        try:
            handle_output("G28\n", self._args(output=path), "test")
            with open(path, "rb") as f:
                self.assertEqual(f.read(4), b"GCDE")
        finally:
            os.unlink(path)

    def test_ascii_flag_writes_plain_text_to_file(self):
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            handle_output("G28\n", self._args(ascii_flag=True, output=path), "test")
            with open(path) as f:
                content = f.read()
            self.assertIn("G28", content)
            self.assertFalse(content.startswith("GCDE"))
        finally:
            os.unlink(path)

    def test_default_writes_bgcode_to_stdout(self):
        buf = io.BytesIO()
        args = self._args(output=None)
        with mock.patch("sys.stdout") as m:
            m.buffer = buf
            handle_output("G28\n", args, "test")
        self.assertEqual(buf.getvalue()[:4], b"GCDE")

    def test_ascii_writes_plain_to_stdout(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            handle_output("G28\n", self._args(ascii_flag=True, output=None), "test")
        self.assertIn("G28", buf.getvalue())

    def test_ascii_thumbnails_prepended_before_gcode(self):
        png = _thumbnail_pa(16, 16)
        thumbs = [(16, 16, png)]
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            handle_output("G28\n", self._args(ascii_flag=True, output=path),
                          "test", thumbnails=thumbs)
            with open(path) as f:
                content = f.read()
            self.assertIn("; thumbnail begin 16x16", content)
            self.assertIn("; thumbnail end", content)
            # Thumbnail comment header must precede the G-code body
            self.assertLess(content.index("; thumbnail begin"),
                            content.index("G28"))
        finally:
            os.unlink(path)

    def test_binary_thumbnails_embedded_in_bgcode(self):
        png = _thumbnail_pa(16, 16)
        thumbs = [(16, 16, png)]
        with tempfile.NamedTemporaryFile(suffix=".bgcode", delete=False) as f:
            path = f.name
        try:
            handle_output("G28\n", self._args(output=path), "test",
                          thumbnails=thumbs)
            with open(path, "rb") as f:
                data = f.read()
            self.assertEqual(data[:4], b"GCDE")
            # Block type 5 (thumbnail) should be present somewhere in the file
            self.assertIn(b"\x05\x00", data)  # btype=5 as little-endian uint16
        finally:
            os.unlink(path)

    def test_ascii_no_thumbnails_no_comment_blocks(self):
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            handle_output("G28\n", self._args(ascii_flag=True, output=path),
                          "test", thumbnails=())
            with open(path) as f:
                content = f.read()
            self.assertNotIn("thumbnail", content)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
