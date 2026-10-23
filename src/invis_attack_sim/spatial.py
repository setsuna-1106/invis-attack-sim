"""Piecewise-linear motion on a star graph (one route has unit length)."""

EPS = 1e-10


def distance(a, b):
    ra, xa = a
    rb, xb = b
    return abs(xa - xb) if ra == rb else xa + xb


def threshold_time(a, va, b, vb, radius, duration):
    """First entry within radius while these two route segments remain valid.

    Endpoint/turn events re-evaluate the geometry; this never extrapolates beyond
    a segment or guesses the target's next action.
    """
    ra, xa = a
    rb, xb = b
    if ra == rb:
        delta = xa - xb
        rate = va - vb
        separation = abs(delta)
        closing = -(1 if delta >= 0 else -1) * rate
    else:
        separation = xa + xb
        closing = -(va + vb)
    if separation <= radius + EPS or closing <= EPS:
        return None
    dt = (separation - radius) / closing
    if EPS < dt <= duration + EPS:
        return min(dt, duration)
    return None
