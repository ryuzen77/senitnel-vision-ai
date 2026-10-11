"""Inside side left/right, operator configuration and template conversion (plan §6,
acceptance 7 geometry part). Pure model tests: no frames, decoder or runtime."""

from __future__ import annotations

import json

import pytest

import app.models.private_parking as private_parking
from app.models.private_parking import (
    AccessLine,
    InsideSide,
    InvalidGeometry,
    OperatorConfiguration,
    PrivateParkingConfig,
    build_operator_configuration,
    make_thresholds,
    operator_template,
    parse_private_parking_config,
)

ZONES = [{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}]


def build(**overrides):
    values = dict(frame_width=640, frame_height=480, line={"p1": [0.2, 0.5], "p2": [0.8, 0.5]},
                  inside="left", zones=ZONES)
    values.update(overrides)
    return build_operator_configuration(**values)


def field_of(**overrides) -> str:
    with pytest.raises(InvalidGeometry) as info:
        build(**overrides)
    return info.value.field


def test_horizontal_line_left_is_above_on_screen():
    line = AccessLine(0.0, 0.0, 1.0, 0.0, inside="left")
    assert line.raw_side(0.5, -0.0) == 0.0
    left = AccessLine(0.2, 0.5, 0.8, 0.5, inside=InsideSide.LEFT)
    assert left.raw_side(0.5, 0.2) < 0                              # above on screen -> raw_side < 0
    assert left.signed_distance(0.5, 0.2) > 0 > left.signed_distance(0.5, 0.8)
    right = AccessLine(0.2, 0.5, 0.8, 0.5, inside=InsideSide.RIGHT)
    assert right.signed_distance(0.5, 0.8) > 0 > right.signed_distance(0.5, 0.2)


@pytest.mark.parametrize("p1, p2, left_point", [
    ((0.2, 0.5), (0.8, 0.5), (0.5, 0.2)),        # horizontal, towards +x: left is up
    ((0.5, 0.1), (0.5, 0.9), (0.8, 0.5)),        # vertical, towards +y (down): left is +x
    ((0.1, 0.1), (0.9, 0.9), (0.8, 0.2)),        # diagonal
])
def test_sides_match_the_trigger_signed_distance_and_reverse_flips(p1, p2, left_point):
    left = AccessLine(*p1, *p2, inside="left")
    right = AccessLine(*p1, *p2, inside="right")
    assert left.signed_distance(*left_point) > 0 and right.signed_distance(*left_point) < 0
    # Same side expressed by a startup inside_point gives the same signed distance.
    point_line = AccessLine(*p1, *p2, *left_point)
    for q in ((0.3, 0.7), (0.6, 0.1), (0.95, 0.4)):
        assert point_line.signed_distance(*q) == pytest.approx(left.signed_distance(*q))
    reversed_left = AccessLine(*p2, *p1, inside="left")
    assert reversed_left.signed_distance(*left_point) < 0           # reversing P1/P2 flips sides


def test_exactly_one_inside_definition():
    with pytest.raises(ValueError):
        AccessLine(0.2, 0.5, 0.8, 0.5)
    with pytest.raises(ValueError):
        AccessLine(0.2, 0.5, 0.8, 0.5, 0.5, 0.8, inside="left")
    with pytest.raises(ValueError):
        AccessLine(0.2, 0.5, 0.8, 0.5, inside="up")
    with pytest.raises(ValueError):
        AccessLine(0.2, 0.5, 0.8, 0.5, inside=1)


def test_operator_configuration_has_no_point_distance_rule():
    config = build(line={"p1": [0.0, 0.0], "p2": [1.0, 0.0]}, inside="right",
                   zones=[{"name": "all", "rect": [0.0, 0.0, 1.0, 1.0]}])
    assert isinstance(config, OperatorConfiguration)
    assert config.config.line.inside is InsideSide.RIGHT and config.config.line.inside_x is None
    assert config.config.thresholds == make_thresholds()


def test_canonical_round_trip():
    config = build(zones=[{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]},
                          {"name": "z-2", "rect": [0.5, 0.5, 0.9, 0.9]}])
    canonical = config.canonical()
    assert canonical["inside"] == "left" and canonical["line"] == {"p1": [0.2, 0.5], "p2": [0.8, 0.5]}
    again = build_operator_configuration(**canonical)
    assert again == config


@pytest.mark.parametrize("overrides, field", [
    ({"frame_width": 0}, "frame_width"),
    ({"frame_width": True}, "frame_width"),
    ({"frame_height": 480.0}, "frame_height"),
    ({"frame_height": 16385}, "frame_height"),
    ({"line": [0.2, 0.5, 0.8, 0.5]}, "line"),
    ({"line": {"p1": [0.2, 0.5], "p2": [0.8, 0.5], "extra": 1}}, "line"),
    ({"line": {"p1": [0.2, float("nan")], "p2": [0.8, 0.5]}}, "line.p1"),
    ({"line": {"p1": [0.2, 0.5], "p2": [float("inf"), 0.5]}}, "line.p2"),
    ({"line": {"p1": [-0.1, 0.5], "p2": [0.8, 0.5]}}, "line.p1"),
    ({"line": {"p1": [0.2, 0.5], "p2": [1.1, 0.5]}}, "line.p2"),
    ({"line": {"p1": [True, 0.5], "p2": [0.8, 0.5]}}, "line.p1"),
    ({"line": {"p1": ["0.2", 0.5], "p2": [0.8, 0.5]}}, "line.p1"),
    ({"line": {"p1": [0.5, 0.5], "p2": [0.52, 0.5]}}, "line"),        # degenerate (< 0.05)
    ({"inside": "up"}, "inside"),
    ({"inside": None}, "inside"),
    ({"zones": []}, "zones"),
    ({"zones": [{"name": f"z{n}", "rect": [0.0, 0.0, 0.5, 0.5]} for n in range(9)]}, "zones"),
    ({"zones": [{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]},
                {"name": "z1", "rect": [0.5, 0.5, 0.9, 0.9]}]}, "zones[1].name"),
    ({"zones": [{"name": "Zone 1", "rect": [0.1, 0.1, 0.5, 0.5]}]}, "zones[0].name"),
    ({"zones": [{"name": "z1", "rect": [0.1, 0.1, 0.15, 0.15]}]}, "zones[0].rect"),   # too small
    ({"zones": [{"name": "z1", "rect": [0.5, 0.5, 0.1, 0.1]}]}, "zones[0].rect"),     # reversed
    ({"zones": [{"name": "z1", "rect": [0.1, 0.1, 0.5]}]}, "zones[0].rect"),
    ({"zones": [{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5], "x": 1}]}, "zones[0]"),
])
def test_invalid_operator_configuration_names_the_field(overrides, field):
    assert field_of(**overrides) == field


def test_eight_zones_and_zero_one_edges_are_accepted():
    zones = [{"name": f"z{n}", "rect": [0.0, n / 8, 1.0, (n + 1) / 8]} for n in range(8)]
    assert len(build(zones=zones).config.zones) == 8


def test_configuration_size_limit(monkeypatch):
    monkeypatch.setattr(private_parking, "MAX_CONFIG_BYTES", 50)
    assert field_of() == "configuration"


def test_invalid_geometry_message_carries_no_values():
    with pytest.raises(InvalidGeometry) as info:
        build(line={"p1": [0.123456, 0.5], "p2": [7.5, 0.5]})
    assert "0.123456" not in str(info.value) and "7.5" not in str(info.value)


# --------------------------------------------------------------------------- template


def _startup(inside_point, **thresholds) -> PrivateParkingConfig | None:
    raw = {"line": [0.2, 0.5, 0.8, 0.5], "inside_point": inside_point,
           "zones": [{"name": "z1", "rect": [0.1, 0.1, 0.5, 0.5]}]}
    if thresholds:
        raw["thresholds"] = thresholds
    return parse_private_parking_config(json.dumps(raw))


def test_startup_parser_is_unchanged():
    config = _startup([0.5, 0.8])
    assert config.line.inside is None and (config.line.inside_x, config.line.inside_y) == (0.5, 0.8)
    assert config.line.inside_side is InsideSide.RIGHT


@pytest.mark.parametrize("inside_point, side", [([0.5, 0.8], InsideSide.RIGHT), ([0.5, 0.2], InsideSide.LEFT)])
def test_template_conversion_by_sign_keeps_zones_and_thresholds(inside_point, side):
    startup = _startup(inside_point, line_hysteresis=0.02, approach_observations=4)
    template = operator_template(startup)
    assert template.line.inside is side and template.line.inside_x is None
    assert template.zones == startup.zones and template.thresholds == startup.thresholds
    for q in ((0.5, 0.9), (0.1, 0.1), (0.7, 0.45)):
        assert template.line.signed_distance(*q) == pytest.approx(startup.line.signed_distance(*q))


def test_template_rejects_points_too_close_with_non_default_hysteresis():
    assert _startup([0.5, 0.55], line_hysteresis=0.01) is not None      # 0.05 > 2 x 0.01
    assert _startup([0.5, 0.58], line_hysteresis=0.05) is None          # 0.08 <= 2 x 0.05
    assert _startup([0.5, 0.515]) is None                               # < 0.02


# --------------------------------------------------------------------------- extreme numbers

HUGE = (10 ** 400, -10 ** 400, 10 ** 20, -10 ** 20)


@pytest.mark.parametrize("value", HUGE)
@pytest.mark.parametrize("overrides, field", [
    (lambda v: {"line": {"p1": [v, 0.5], "p2": [0.8, 0.5]}}, "line.p1"),
    (lambda v: {"line": {"p1": [0.2, 0.5], "p2": [0.8, v]}}, "line.p2"),
    (lambda v: {"zones": [{"name": "z1", "rect": [0.1, 0.1, v, 0.5]}]}, "zones[0].rect"),
    (lambda v: {"frame_width": v}, "frame_width"),
    (lambda v: {"frame_height": v}, "frame_height"),
])
def test_extreme_integers_fail_validation_with_a_field(value, overrides, field):
    assert field_of(**overrides(value)) == field


@pytest.mark.parametrize("value", HUGE)
def test_extreme_integers_in_models_and_thresholds_fail_closed(value):
    with pytest.raises(ValueError):
        AccessLine(value, 0.5, 0.8, 0.5, inside="left")
    with pytest.raises(ValueError):
        AccessLine(0.2, 0.5, 0.8, 0.5, 0.5, value)
    with pytest.raises(ValueError):
        make_thresholds(line_hysteresis=value)
    with pytest.raises(ValueError):
        make_thresholds(approach_observations=value)
    raw = ('{"line": [0.2, 0.5, 0.8, 0.5], "inside_point": [0.5, %d], '
           '"zones": [{"name": "z1", "rect": [0, 0, 0.5, 0.5]}], "thresholds": {"max_tracks": %d}}' % (value, value))
    assert parse_private_parking_config(raw) is None


def test_unrelated_construction_errors_are_not_turned_into_geometry_errors():
    with pytest.raises(ValueError) as info:
        build(thresholds="not thresholds")
    assert not isinstance(info.value, InvalidGeometry)
