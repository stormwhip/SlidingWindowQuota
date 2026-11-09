"""One exact scan over active slope changes, with no search over demand."""

from fractions import Fraction

from demand_limit import better, capacity_pieces
from event_index import events, prefix


def scan_capacity(root, domain_lo, domain_hi, start, end, window, quota, old=None):
    best = None
    for lo, hi, alpha, beta, removed_slope, removed_bias in capacity_pieces(
        domain_lo, domain_hi, start, end, window, old
    ):
        slope, moment = prefix(root, lo)
        headroom = quota + moment + (removed_slope - slope) * lo + removed_bias
        weight = alpha * lo + beta
        best = better(best, (headroom, weight, lo))
        prev = lo
        slope = removed_slope - slope
        for t, delta in events(root, lo, hi):
            gap = t - prev
            headroom += slope * gap
            weight += alpha * gap
            if headroom * best[1] < best[0] * weight:
                best = headroom, weight, t
            slope -= delta
            prev = t
        if hi > lo:
            gap = hi - prev
            headroom += slope * gap
            weight += alpha * gap
            if headroom * best[1] < best[0] * weight:
                best = headroom, weight, hi
    return (Fraction(best[0], best[1]), best[2]) if best is not None else None
