"""Check the single-descent bridge search against exact supporting lines."""

from itertools import product
import random

from bridge_index import add, join, join_reference, leaf, shift
from bridge_search import bridge_points


def build(points):
    if len(points) == 1:
        return leaf(*points[0])
    mid = len(points) // 2
    return join(build(points[:mid]), build(points[mid:]))


def check(left, right, points):
    (x, y), (u, v) = bridge_points(left, right)
    assert (x, y) in points and (u, v) in points
    assert x <= left.hi < right.lo <= u
    assert all((py - y) * (u - x) <= (v - y) * (px - x) for px, py in points)
    ref = join_reference(left, right)
    assert (v - y) * ref.dx == ref.dy * (u - x)


def exhaustive():
    count = 0
    xs = (-8, -2, 0, 1, 9, 17)
    for ys in product((-1, 0, 1), repeat=len(xs)):
        points = list(zip(xs, ys))
        for mid in range(1, len(xs)):
            left, right = build(points[:mid]), build(points[mid:])
            check(left, right, points)
            count += 1
    return count


def with_lazy():
    rng = random.Random(261118)
    for i in range(1600):
        n = rng.randrange(2, 81)
        xs = sorted(rng.sample(range(-1024, 1025), n))
        scale = 2**58 if i % 5 == 0 else 1
        ys = [scale * rng.randrange(-6, 7) for _ in xs]
        mid = rng.randrange(1, n)
        points = list(zip(xs, ys))
        left, right = build(points[:mid]), build(points[mid:])
        for _ in range(4):
            lo, hi = sorted(rng.sample(range(-1200, 1201), 2))
            a, b = (scale * rng.randrange(-8, 9) for _ in range(2))
            left, right = add(left, lo, hi, a, b), add(right, lo, hi, a, b)
            points = [(x, y + a * x + b if lo <= x < hi else y) for x, y in points]
        a, b = rng.randrange(-4, 5), rng.randrange(-4, 5)
        left = shift(left, a, b)
        points = [(x, y + a * x + b if x <= left.hi else y) for x, y in points]
        check(left, right, points)
    return 1600


if __name__ == "__main__":
    print(f"exhaustive bridge checks: {exhaustive()}")
    print(f"lazy bridge checks: {with_lazy()}")
