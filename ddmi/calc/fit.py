"""Two-point fit used for the TX, RX and VCC calibrations (build map 7.2)."""


def two_point_fit(p1: tuple, p2: tuple) -> tuple[float, float]:
    """Return (slope, offset) of the line through p1 = (x1, y1) and p2 = (x2, y2)."""
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2:
        raise ValueError(f"cannot fit: both points have x = {x1}")
    slope = (y2 - y1) / (x2 - x1)
    return slope, y1 - slope * x1


def apply_fit(slope: float, offset: float, x: float) -> float:
    return slope * x + offset
