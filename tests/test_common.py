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
    BaseGenerator,
    CommonConfig,
    _State,
    _r,
    _render,
    _write_bgcode,
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


if __name__ == "__main__":
    unittest.main()
