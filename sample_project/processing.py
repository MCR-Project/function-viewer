"""Cleaning and aggregation of raw readings."""


def clean(readings: list[float]) -> list[float]:
    """Drop obviously bad readings.

    Negative values are sensor glitches; huge values are spikes that
    get smoothed instead of dropped.
    """
    result = []
    for value in readings:
        if value < 0:
            continue
        if value > 1000:
            value = smooth(value)
        result.append(value)
    return result


def smooth(value: float, factor: float = 0.5) -> float:
    """Dampen a spiked *value* by *factor*, recursively until sane."""
    damped = value * factor
    if damped > 1000:
        return smooth(damped, factor)
    return damped


def aggregate(readings: list[float]) -> dict:
    """Compute summary statistics over cleaned readings."""
    if not readings:
        return {"count": 0, "mean": 0.0, "peak": 0.0}
    return {
        "count": len(readings),
        "mean": mean(readings),
        "peak": max(readings),
    }


def mean(values: list[float]) -> float:
    """Arithmetic mean of *values* (assumes non-empty)."""
    return sum(values) / len(values)


def smooth_mean(values: list[float], factor: float = 0.5):
    """Arithmetic mean of smoothed *values* (assumes non-empty)."""
    return mean([smooth(val, factor=factor) for val in values])
