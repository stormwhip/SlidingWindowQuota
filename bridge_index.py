"""Exact, persistent upper-bridge index for integer time coordinates."""

from dataclasses import dataclass, replace

from bridge_search import bridge_points


@dataclass(frozen=True, slots=True)
class Node:
    lo: int
    hi: int
    height: int
    left: "Node | None" = None
    right: "Node | None" = None
    value: int = 0
    dy: int = 0
    dx: int = 1
    a: int = 0
    b: int = 0
    anchor_x: int = 0
    anchor_y: int = 0


def leaf(x, y):
    return Node(x, x, 1, value=y)


def shift(node, a, b):
    return replace(node, a=node.a + a, b=node.b + b) if a or b else node


def children(node):
    return shift(node.left, node.a, node.b), shift(node.right, node.a, node.b)


def support(node, numerator=0, denominator=1):
    """Return a leftmost maximizer of denominator*y - numerator*x."""
    a = b = 0
    while True:
        a += node.a
        b += node.b
        if node.left is None:
            return node.lo, node.value + a * node.lo + b
        if numerator * node.dx >= (node.dy + a * node.dx) * denominator:
            node = node.left
        else:
            node = node.right


def contact(node, left, right):
    """Find the support point of node at the common bridge's unknown slope."""
    a = b = 0
    while True:
        a += node.a
        b += node.b
        if node.left is None:
            return node.lo, node.value + a * node.lo + b
        p, q = node.dy + a * node.dx, node.dx
        xl, yl = support(left, p, q)
        xr, yr = support(right, p, q)
        difference = (yl - yr) * q - p * (xl - xr)
        # D(s) is increasing. D(p/q) <= 0 means the target slope is >= p/q.
        node = node.left if difference <= 0 else node.right


def joined(left, right, first, second):
    xl, yl = first
    xr, yr = second
    return Node(left.lo, right.hi, 1 + max(left.height, right.height), left, right,
                dy=yr - yl, dx=xr - xl, anchor_x=xl, anchor_y=yl)


def join_reference(left, right):
    """Reference bridge search, using nested support queries."""
    return joined(left, right, contact(left, left, right), contact(right, left, right))


def join(left, right):
    return joined(left, right, *bridge_points(left, right))


def balance(left, right):
    if left.height > right.height + 1:
        ll, lr = children(left)
        if lr.height > ll.height:
            lrl, lrr = children(lr)
            return join(join(ll, lrl), join(lrr, right))
        return join(ll, join(lr, right))
    if right.height > left.height + 1:
        rl, rr = children(right)
        if rl.height > rr.height:
            rll, rlr = children(rl)
            return join(join(left, rll), join(rlr, rr))
        return join(join(left, rl), rr)
    return join(left, right)


def add(node, start, end, a, b):
    """Add a*x+b to coordinates in [start, end)."""
    if start > node.hi or end <= node.lo or start >= end:
        return node
    if start <= node.lo and node.hi < end:
        return shift(node, a, b)
    left, right = children(node)
    new_left = add(left, start, end, a, b)
    new_right = add(right, start, end, a, b)
    return join(new_left, new_right)


def insert(node, x, y):
    if node.left is None:
        if x == node.lo:
            raise ValueError("duplicate coordinate")
        point = leaf(x, y)
        return join(point, node) if x < node.lo else join(node, point)
    left, right = children(node)
    if x <= left.hi:
        return balance(insert(left, x, y), right)
    return balance(left, insert(right, x, y))


def delete(node, x):
    if node.left is None:
        if node.lo != x:
            raise KeyError(x)
        return None
    left, right = children(node)
    if x <= left.hi:
        left = delete(left, x)
    else:
        right = delete(right, x)
    return right if left is None else left if right is None else balance(left, right)


def value_at(node, x):
    """Interpolate the current piecewise-linear function on the tree domain."""
    if not node.lo <= x <= node.hi:
        raise ValueError("time outside the domain")
    a = b = 0
    while True:
        a += node.a
        b += node.b
        if node.left is None:
            return node.value + a * x + b
        left, right = node.left, node.right
        if x <= left.hi:
            node = left
        elif x >= right.lo:
            node = right
        else:
            xl, xr = left.hi, right.lo
            yl, yr = value_at(left, xl), value_at(right, xr)
            value, remainder = divmod(yl * (xr - x) + yr * (x - xl), xr - xl)
            if remainder:
                raise ArithmeticError("expected integer interpolation")
            return value + a * x + b


def reservation_segments(start, end, window, demand):
    """Affine pieces of the signed change in sliding-window load."""
    u, v = sorted((start + window, end))
    return ((start, u, demand, -demand * start),
            (u, v, 0, demand * (u - start)),
            (v, end + window, -demand, demand * (end + window)))


def apply_reservation(node, start, end, window, demand):
    """Apply a signed resource interval. Its breakpoints must already be stored."""
    for l, r, a, b in reservation_segments(start, end, window, demand):
        node = add(node, l, r, a, b)
    return node


def delta_segments(window, old=None, new=None):
    """Combine removal and addition before evaluating the quota."""
    events = {}
    for reservation, sign in ((old, -1), (new, 1)):
        if reservation is None:
            continue
        l, r, d = reservation
        for start, end, a, b in reservation_segments(l, r, window, sign * d):
            for time, factor in ((start, 1), (end, -1)):
                da, db = events.get(time, (0, 0))
                events[time] = da + factor * a, db + factor * b
    cuts = sorted(events)
    a = b = 0
    for l, r in zip(cuts, cuts[1:]):
        da, db = events[l]
        a += da
        b += db
        if a or b:
            yield l, r, a, b


def add_pieces(node, pieces):
    """Apply disjoint affine pieces in one traversal and rebuild each bridge once."""
    active = tuple(piece for piece in pieces if piece[0] <= node.hi and node.lo < piece[1])
    if not active:
        return node
    if len(active) == 1:
        lo, hi, a, b = active[0]
        if lo <= node.lo and node.hi < hi:
            return shift(node, a, b)
    left, right = children(node)
    updated_left = add_pieces(left, active)
    updated_right = add_pieces(right, active)
    if updated_left is left and updated_right is right:
        return node
    return join(updated_left, updated_right)


def range_support(node, lo, hi, slope, a=0, b=0):
    """Return max(y+slope*x) among stored coordinates in [lo, hi]."""
    if hi < node.lo or node.hi < lo:
        return None
    if lo <= node.lo and node.hi <= hi:
        x, y = support(node, -(slope + a))
        return y + (slope + a) * x + b
    a += node.a
    b += node.b
    left = range_support(node.left, lo, hi, slope, a, b)
    right = range_support(node.right, lo, hi, slope, a, b)
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def allowed(node, pieces, quota):
    """Check a change without editing the index. The old state must be feasible."""
    for l, r, a, b in pieces:
        l, r = max(node.lo, l), min(node.hi, r)
        if l > r:
            continue
        if value_at(node, l) + a * l + b > quota or value_at(node, r) + a * r + b > quota:
            return False
        peak = range_support(node, l, r, a)
        if peak is not None and peak + b > quota:
            return False
    return True
