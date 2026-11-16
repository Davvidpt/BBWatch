"""Pure-Python Bollinger Band squeeze calculations (no network access)."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean, pstdev


@dataclass(frozen=True)
class Candle:
    open_ms: int
    close_ms: int
    close: float


@dataclass(frozen=True)
class Snapshot:
    open_ms: int
    close_ms: int
    price: float
    middle: float | None
    upper: float | None
    lower: float | None
    bandwidth_pct: float | None
    cutoff_pct: float | None
    is_squeeze: bool | None


def percentile(values: list[float], percentile_number: float) -> float:
    """Linear-interpolated percentile; percentile_number ranges from 0 to 100."""
    if not values or not 0 <= percentile_number <= 100:
        raise ValueError("Percentile requires nonempty values and a number in [0, 100]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_number / 100
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    fraction = position - lo
    return ordered[lo] + fraction * (ordered[hi] - ordered[lo])


def snapshots(
    candles: list[Candle],
    *,
    period: int = 20,
    deviation: float = 2.0,
    lookback: int = 120,
    squeeze_percentile: float = 20.0,
) -> list[Snapshot]:
    """A squeeze occurs when the current bandwidth <= historical p20 of the PREVIOUS 120 widths.

    The current width is excluded from the reference window to avoid look-ahead.
    Uses population stddev (ddof=0), like a conventional BB implementation.
    Only closed candles should be passed to this function.
    """
    if period < 2 or lookback < 2 or deviation <= 0 or not (0 < squeeze_percentile < 100):
        raise ValueError("Invalid Bollinger Bands / squeeze configuration")

    widths: list[float | None] = []
    result: list[Snapshot] = []

    for i, candle in enumerate(candles):
        if not isfinite(candle.close) or candle.close <= 0:
            raise ValueError("Candle close must be a positive finite value")
        mid = up = low = width = cutoff = None
        squeezed = None
        if i >= period - 1:
            prices = [c.close for c in candles[i - period + 1 : i + 1]]
            mid = mean(prices)
            sigma = pstdev(prices)
            up, low = mid + deviation * sigma, mid - deviation * sigma
            width = (up - low) / abs(mid) * 100.0

            history = widths[max(0, i - lookback) : i]
            if len(history) == lookback and all(value is not None for value in history):
                cutoff = percentile([float(value) for value in history if value is not None], squeeze_percentile)
                # When all historical widths tie at the cutoff, <= alone would
                # classify even a consistently wide band as a squeeze.
                # Treat positive ties as inconclusive; exact zero width is special.
                squeezed = width <= cutoff and (width < cutoff or width == 0.0)

        widths.append(width)
        result.append(Snapshot(
            open_ms=candle.open_ms,
            close_ms=candle.close_ms,
            price=candle.close,
            middle=mid,
            upper=up,
            lower=low,
            bandwidth_pct=width,
            cutoff_pct=cutoff,
            is_squeeze=squeezed,
        ))
    return result


def squeeze_started(previous: Snapshot | None, current: Snapshot) -> bool:
    """Only alert on False -> True, not every candle that stays squeezed."""
    return previous is not None and previous.is_squeeze is False and current.is_squeeze is True
