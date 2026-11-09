"""Check the bridge-tree replacement for the geometric stage of arXiv:2510.16454v2."""

from fractions import Fraction
from itertools import product
import random

from bridge_index import add, delete, insert, leaf, value_at


def ratio_leader(node, numerator=(0, 0), denominator=(1, 0)):
    """Maximize (y + numerator(x)) / denominator(x), with positive denominator."""
    a, b = numerator
    den_a, den_b = denominator
    while True:
        a += node.a
        b += node.b
        if node.left is None:
            return Fraction(node.value + a * node.lo + b, den_a * node.lo + den_b), node.lo
        intercept = (node.anchor_y + b) * node.dx - node.dy * node.anchor_x
        rise = node.dy + a * node.dx
        trend = rise * den_b - den_a * intercept
        node = node.right if trend > 0 else node.left


def range_leader(node, lo, hi, numerator, denominator):
    if node.hi < lo or hi < node.lo:
        return None
    if lo <= node.lo and node.hi <= hi:
        return ratio_leader(node, numerator, denominator)
    a, b = numerator
    adjusted = a + node.a, b + node.b
    left = range_leader(node.left, lo, hi, adjusted, denominator)
    right = range_leader(node.right, lo, hi, adjusted, denominator)
    if left is None:
        return right
    if right is None or left[0] >= right[0]:
        return left
    return right


def check_general(rng):
    points = {0: 0}
    root = leaf(0, 0)
    for i in range(4000):
        x = rng.randrange(-45, 46)
        if i % 3 == 0 and x not in points:
            points[x] = rng.randrange(-200, 201)
            root = insert(root, x, points[x])
        elif i % 3 == 1 and x in points and len(points) > 1:
            del points[x]
            root = delete(root, x)
        else:
            left, right = sorted((x, rng.randrange(-45, 46)))
            slope, intercept = rng.randrange(-20, 21), rng.randrange(-200, 201)
            root = add(root, left, right, slope, intercept)
            for t in points:
                if left <= t < right:
                    points[t] += slope * t + intercept
        den_a = rng.randrange(-20, 21)
        den_b = rng.randrange(1, 21) - min(den_a * t for t in points)
        a, b = rng.randrange(-20, 21), rng.randrange(-200, 201)
        expected = max((Fraction(y + a * t + b, den_a * t + den_b), -t)
                       for t, y in points.items())
        assert ratio_leader(root, (a, b), (den_a, den_b)) == (expected[0], -expected[1])
        lo, hi = sorted(rng.choices(list(points), k=2))
        candidates = {t: y for t, y in points.items() if lo <= t <= hi}
        den_b = rng.randrange(1, 21) - min(den_a * t for t in candidates)
        expected = max((Fraction(y + a * t + b, den_a * t + den_b), -t)
                       for t, y in candidates.items())
        assert range_leader(root, lo, hi, (a, b), (den_a, den_b)) == (expected[0], -expected[1])


def check(word):
    root = leaf(1, 1)
    distinct = [set() for _ in word]
    for length in range(1, len(word) + 1):
        prefix = word[:length]
        for k in range(1, length + 1):
            distinct[k - 1].add(prefix[-k:])
        if length > 1:
            # Brute-force alpha is deliberately independent of the maintained hull.
            alpha = next(k for k in range(1, length + 1)
                         if prefix.find(prefix[-k:]) == length - k)
            right = root.hi
            if alpha == right + 1:
                root = insert(root, right + 1, value_at(root, right))
            else:
                assert alpha <= right
                root = add(root, alpha, right + 1, 0, 1)
        for k in range(1, root.hi + 1):
            assert value_at(root, k) == len(distinct[k - 1]), (prefix, k)
        oracle = max((Fraction(len(distinct[k - 1]), k), -k)
                     for k in range(1, length + 1))
        assert ratio_leader(root) == (oracle[0], -oracle[1]), prefix
    return len(word)


if __name__ == "__main__":
    binary = ("".join(chars) for n in range(1, 11) for chars in product("ab", repeat=n))
    rng = random.Random(261109)
    random_words = ("".join(rng.choices("abcde"[:alphabet], k=n))
                    for alphabet in range(1, 6) for n in range(12, 48))
    words = [*binary, *random_words]
    prefixes = sum(map(check, words))
    print(f"Checked {len(words)} words and {prefixes} prefixes: exact ratio and smallest leader agree.")
    check_general(random.Random(83027))
    print("Checked 4000 dynamic affine-ratio cases against exact fractions.")
