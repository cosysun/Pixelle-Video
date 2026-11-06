"""Link time-weighted returns from a market-value baseline and external flows.

All inputs are cached valuations at or before their observation time. A flow
boundary carries a pre-flow value and gross external capital in CNY. Fees stay
inside performance. Missing flow valuations permanently break the cumulative
chain; missing observations alone can recover without inventing samples.
"""

from decimal import Decimal, localcontext

from .ledger import ONE, ZERO, decimal_string, parse_time


def seeded_compounded_points(points, initial_return):
    """Link the recorded opening capital return to subsequent flow-neutral returns.

    Keep the pure TWR field intact. A missing opening return cannot be replaced
    by zero, and a missing flow boundary cannot be bridged by the opening value.
    """
    result = []
    with localcontext() as context:
        context.prec = 100
        initial = Decimal(initial_return) if initial_return is not None else None
        if initial is not None and (not initial.is_finite() or initial < -100):
            initial = None
        for original in points:
            point = dict(original)
            twr = point.get("time_weighted_return")
            value = (
                ((ONE + initial / 100) * (ONE + Decimal(twr) / 100) - ONE) * 100
                if initial is not None and twr is not None
                else None
            )
            if initial is not None and twr is not None and Decimal(twr) == ZERO:
                value = initial
            point["compounded_return"] = decimal_string(value)
            point["compounded_warning"] = (
                "缺少起点累计盈亏或大于零的累计净投入，无法确定期初收益率"
                if initial is None
                else point.get("twr_warning")
            )
            result.append(point)
    return result


def time_weighted_points(points, baseline, boundaries):
    """Return enriched copies, preserving the tracking baseline across windows."""
    result = []
    cursor, factor = 0, ONE
    baseline_at = parse_time(baseline["occurred_at"]) if baseline else None
    anchor = Decimal(baseline["total"]) if baseline and baseline.get("total") is not None else None
    blocked = None if anchor is not None and anchor > ZERO else "缺少完整且大于零的期初资产市值"
    estimated = bool(baseline and baseline.get("estimated"))
    boundaries = sorted(boundaries, key=lambda row: parse_time(row["occurred_at"]))
    with localcontext() as context:
        context.prec = 100
        for original in points:
            point = dict(original)
            at = parse_time(point["occurred_at"])
            while cursor < len(boundaries) and parse_time(boundaries[cursor]["occurred_at"]) <= at:
                boundary = boundaries[cursor]
                cursor += 1
                if baseline_at is None or parse_time(boundary["occurred_at"]) <= baseline_at:
                    continue
                if blocked:
                    continue
                before, flow = boundary.get("before"), boundary.get("flow")
                if boundary.get("warning") or before is None or flow is None:
                    blocked = (
                        boundary.get("warning")
                        or "出入金时缺少资产估值或历史汇率，累计收益无法衔接"
                    )
                    continue
                before, flow = Decimal(before), Decimal(flow)
                if before <= ZERO or before + flow <= ZERO:
                    blocked = "出入金前后资产市值不大于零，累计收益无法衔接"
                    continue
                factor *= before / anchor
                anchor = before + flow
                estimated |= bool(boundary.get("estimated"))
            value = point.get("total_assets", point.get("total"))
            warning = blocked
            rate = None
            if baseline_at is not None and at < baseline_at:
                warning = "早于收益率跟踪起点"
            elif not blocked and value is not None and Decimal(value) >= ZERO:
                rate = (factor * Decimal(value) / anchor - ONE) * 100
            elif not blocked:
                warning = "该时间点资产估值不完整，无法计算收益率"
            point.update(
                time_weighted_return=decimal_string(rate),
                twr_estimated=rate is not None and (estimated or bool(point.get("estimated"))),
                twr_warning=warning,
            )
            result.append(point)
    return result
