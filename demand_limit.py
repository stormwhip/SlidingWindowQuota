"""Exact maximum demand for one proposed reservation."""

from fractions import Fraction

from bridge_index import support, value_at


def better(left, right):
    if left is None:
        return right
    if right is None:
        return left
    difference = left[0] * right[1] - right[0] * left[1]
    if difference < 0 or (difference == 0 and left[2] <= right[2]):
        return left
    return right


def subtree_limit(node, alpha, beta, quota, a, b):
    """Minimize (quota-y-a*x-b)/(alpha*x+beta), with positive weights."""
    if not alpha:
        x, y = support(node, -a)
        return quota - y - a * x - b, beta, x
    while True:
        a += node.a
        b += node.b
        if node.left is None:
            x = node.lo
            return quota - node.value - a * x - b, alpha * x + beta, x
        num, den = -(node.dy + a * node.dx), alpha * node.dx
        if den < 0:
            num, den = -num, -den
        x = node.anchor_x
        y = node.anchor_y + a * x + b
        sign = (quota - y) * den - num * (alpha * x + beta)
        go_left = sign == 0 or (sign < 0 if alpha > 0 else sign > 0)
        node = node.left if go_left else node.right


def range_limit(node, lo, hi, alpha, beta, quota, a, b):
    if hi < node.lo or node.hi < lo or hi < lo:
        return None
    if lo <= node.lo and node.hi <= hi:
        return subtree_limit(node, alpha, beta, quota, a, b)
    a += node.a
    b += node.b
    return better(range_limit(node.left, lo, hi, alpha, beta, quota, a, b),
                  range_limit(node.right, lo, hi, alpha, beta, quota, a, b))


def overlap(t, a, b, window):
    return max(0, min(t, b) - max(t - window, a))


def capacity_pieces(domain_lo, domain_hi, start, end, window, old):
    """Pieces with positive integer overlap and affine new and removed usage."""
    times = {domain_lo, domain_hi}
    for a, b in [(start, end)] + ([old[:2]] if old else []):
        times.update(x for x in (a, b, a + window, b + window) if domain_lo < x < domain_hi)
    cuts = sorted(times)
    for l, r in zip(cuts, cuts[1:]):
        wl, wr = overlap(l, start, end, window), overlap(r, start, end, window)
        if not (wl or wr):
            continue
        alpha = (wr - wl) // (r - l)
        beta = wl - alpha * l
        removed_l = old[2] * overlap(l, old[0], old[1], window) if old else 0
        removed_r = old[2] * overlap(r, old[0], old[1], window) if old else 0
        removed_slope = (removed_r - removed_l) // (r - l)
        removed_bias = removed_l - removed_slope * l
        # Shift a zero-weight boundary to the closest integer with overlap.
        lo, hi = l + (not wl), r - (not wr)
        if lo <= hi:
            yield lo, hi, alpha, beta, removed_slope, removed_bias


def maximum_demand(root, start, end, window, quota, old=None, known_peak=None):
    """Return (exact rational limit, earliest binding time), or None if unbounded.

    The existing state is feasible. If old is supplied, it is an active
    nonnegative reservation removed before the new one is considered.
    known_peak may supply the existing state's (earliest peak time, value).
    """
    if known_peak is not None:
        t, y = known_peak
        weight = overlap(t, start, end, window)
        if (weight and weight == min(window, end - start)
                and (not old or not old[2] * overlap(t, old[0], old[1], window))):
            return Fraction(quota - y, weight), t
    best = None
    for lo, hi, alpha, beta, removed_slope, removed_bias in capacity_pieces(
        root.lo, root.hi, start, end, window, old
    ):
        for x in ((lo,) if lo == hi else (lo, hi)):
            removed = removed_slope * x + removed_bias
            best = better(best, (quota - value_at(root, x) + removed, alpha * x + beta, x))
        best = better(best, range_limit(root, lo + 1, hi - 1, alpha, beta, quota,
                                        -removed_slope, -removed_bias))
    return (Fraction(best[0], best[1]), best[2]) if best is not None else None
