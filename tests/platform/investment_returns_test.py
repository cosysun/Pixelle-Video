"""Time-weighted return linking against independently worked cash-flow examples."""

from decimal import Decimal

import pytest

from pixelle_video.investment.returns import seeded_compounded_points, time_weighted_points

BASE = {"occurred_at": "2026-10-21T00:00:00Z", "total": "100000"}


def point(day, total, **extra):
    return {"occurred_at": f"2026-10-{day:02d}T00:00:00Z", "total_assets": total, **extra}


def boundary(day, before, flow, **extra):
    return {"occurred_at": f"2026-10-{day:02d}T00:00:00Z", "before": before, "flow": flow, **extra}


def test_inception_float_profit_is_excluded_and_deposit_does_not_dilute_return():
    points = [point(21, "100000"), point(22, "110000"), point(23, "210000"), point(24, "231000")]
    flow = boundary(23, "110000", "100000")
    enriched = time_weighted_points(points, BASE, [flow])
    assert [row["time_weighted_return"] for row in enriched] == ["0", "10", "10", "21"]
    assert time_weighted_points(points[2:], BASE, [flow]) == enriched[2:]
    assert all("time_weighted_return" not in row for row in points)


def test_daily_gain_and_loss_compound_instead_of_adding_and_flow_preserves_result():
    points = [point(21, "100000"), point(22, "110000"), point(23, "99000"), point(24, "199000")]
    enriched = time_weighted_points(points, BASE, [boundary(24, "99000", "100000")])
    assert [row["time_weighted_return"] for row in enriched] == ["0", "10", "-1", "-1"]
    assert time_weighted_points(points[2:], BASE, [boundary(24, "99000", "100000")]) == enriched[2:]


def test_withdrawal_preserves_return_and_dividend_fee_and_exchange_effects_remain():
    points = [point(22, "110000"), point(23, "60000"), point(24, "66000"), point(25, "65400")]
    enriched = time_weighted_points(points, BASE, [boundary(23, "110000", "-50000")])
    assert [row["time_weighted_return"] for row in enriched] == ["10", "10", "21", "19.9"]


@pytest.mark.parametrize(
    "before,flow", [(None, "100"), ("100000", None), ("0", "100"), ("100000", "-100000")]
)
def test_unknown_boundary_or_zero_asset_base_breaks_chain_until_inputs_are_repaired(before, flow):
    enriched = time_weighted_points(
        [point(22, "110000"), point(24, "200000"), point(25, "210000")],
        BASE,
        [boundary(23, before, flow)],
    )
    assert enriched[0]["time_weighted_return"] == "10"
    assert all(row["time_weighted_return"] is None and row["twr_warning"] for row in enriched[1:])


def test_missing_observation_recovers_and_estimated_boundary_remains_visible():
    points = [point(21, "100000"), point(22, None), point(24, "210000")]
    enriched = time_weighted_points(
        points, BASE, [boundary(23, "110000", "100000", estimated=True)]
    )
    assert [row["time_weighted_return"] for row in enriched] == ["0", None, "10"]
    assert not enriched[0]["twr_estimated"] and enriched[-1]["twr_estimated"]


def test_decimal_precision_and_missing_baseline_do_not_fall_back_to_current_capital():
    value = time_weighted_points([point(22, "3.000000000000000003")], {**BASE, "total": "3"}, [])[
        0
    ]["time_weighted_return"]
    assert Decimal(value) == Decimal("0.0000000000000001")
    assert time_weighted_points([point(22, "110000")], None, [])[0]["time_weighted_return"] is None
    assert time_weighted_points([point(20, "100000")], BASE, [])[0]["time_weighted_return"] is None


@pytest.mark.parametrize(
    "initial,expected", [("-10", ["-10", "-1", "-10.9"]), ("20", ["20", "32", "18.8"])]
)
def test_seeded_return_keeps_opening_gain_or_loss_and_compounds(initial, expected):
    pure = time_weighted_points(
        [point(21, "100000"), point(22, "110000"), point(23, "99000")], BASE, []
    )
    enriched = seeded_compounded_points(pure, initial)
    assert [row["compounded_return"] for row in enriched] == expected
    assert [row["time_weighted_return"] for row in enriched] == ["0", "10", "-1"]
    assert all("compounded_return" not in row for row in pure)


def test_seeded_deposit_and_withdrawal_keep_return_and_missing_boundary_blocks_it():
    pure = time_weighted_points(
        [point(21, "100000"), point(22, "110000"), point(23, "210000"), point(24, "160000")],
        BASE,
        [boundary(23, "110000", "100000"), boundary(24, "210000", "-50000")],
    )
    assert [row["compounded_return"] for row in seeded_compounded_points(pure, "-10")] == [
        "-10", "-1", "-1", "-1"
    ]
    blocked = time_weighted_points([point(24, "160000")], BASE, [boundary(23, None, "100000")])
    linked = seeded_compounded_points(blocked, "-10")[0]
    assert linked["compounded_return"] is None
    assert linked["compounded_warning"] == blocked[0]["twr_warning"]


@pytest.mark.parametrize("initial", [None, "NaN", "-101"])
def test_missing_or_invalid_opening_rate_never_defaults_to_zero(initial):
    pure = time_weighted_points([point(22, "110000")], BASE, [])
    enriched = seeded_compounded_points(pure, initial)[0]
    assert enriched["compounded_return"] is None
    assert "期初收益率" in enriched["compounded_warning"]


def test_seeded_return_preserves_decimal_precision_and_missing_observation():
    pure = time_weighted_points(
        [point(21, "3"), point(22, None), point(23, "3.000000000000000003")],
        {**BASE, "total": "3"}, [],
    )
    enriched = seeded_compounded_points(pure, "-10")
    assert enriched[1]["compounded_return"] is None
    assert Decimal(enriched[2]["compounded_return"]) == Decimal("-9.99999999999999991")


def test_seeded_first_point_exactly_preserves_long_decimal_opening_return():
    initial = "-5." + "1234567890" * 10
    point_value = seeded_compounded_points([{"time_weighted_return": "0"}], initial)[0]
    assert Decimal(point_value["compounded_return"]) == Decimal(initial)
