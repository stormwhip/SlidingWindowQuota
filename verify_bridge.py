"""Differential checks for the bridge proof and online reservation transactions."""

from fractions import Fraction
from itertools import product
import random

from bridge_index import (add, add_pieces, allowed, apply_reservation, delete, delta_segments, insert,
                          join, leaf, support, value_at)
import event_index as event
from demand_limit import maximum_demand
from event_capacity import scan_capacity


def check_support():
    coordinates = (-5, -3, 0, 1, 6)
    for values in product(range(-1, 2), repeat=len(coordinates)):
        points = list(zip(coordinates, values))
        root = leaf(*points[0])
        for point in points[1:]:
            root = insert(root, *point)
        shifted = add(root, -3, 6, -3, 1)
        expected = [(x, y - 3 * x + 1 if -3 <= x < 6 else y) for x, y in points]
        for num in range(-9, 10):
            for den in (1, 2, 5):
                # Python max retains the first item in a tie.
                want = max(expected, key=lambda xy: den * xy[1] - num * xy[0])
                assert support(shifted, num, den) == want
    return 3 ** len(coordinates) * 19 * 3


def check_avl(node):
    if node.left is None:
        assert node.lo == node.hi and node.height == 1
        return
    assert node.left.hi < node.right.lo
    assert node.lo == node.left.lo and node.hi == node.right.hi
    assert node.height == max(node.left.height, node.right.height) + 1
    assert abs(node.left.height - node.right.height) <= 1
    check_avl(node.left)
    check_avl(node.right)


def slot_totals(reservations, window, lo, hi):
    usage = {s: 0 for s in range(lo - window, hi)}
    for a, b, d in reservations.values():
        for s in range(max(a, lo - window), min(b, hi)):
            usage[s] += d
    return [sum(usage[s] for s in range(t - window, t)) for t in range(lo, hi + 1)]


def coordinates_for(reservations, window, lo, hi):
    return {lo, hi} | {x for a, b, _ in reservations.values()
                       for x in (a, b, a + window, b + window) if lo < x < hi}


def check_reservations():
    rng = random.Random(261108)
    committed = rejected = capacity_checks = 0
    for window in (1, 2, 7, 15):
        lo, hi = 0, 50
        current = {}
        event_root = None
        priority = random.Random(window)
        root = join(leaf(lo, 0), leaf(hi, 0))
        for i in range(600):
            ticket = rng.randrange(12)
            proposed = current.copy()
            old = proposed.pop(ticket, None)
            new = None
            if old is None or rng.randrange(4):
                a = rng.randrange(-15, 61)
                new = a, a + rng.randrange(1, 24), rng.randrange(1, 9)
                baseline = slot_totals(proposed, window, lo, hi)
                weights = slot_totals({ticket: (new[0], new[1], 1)}, window, lo, hi)
                bounds = [(Fraction(12 * window - y, d), t)
                          for t, (y, d) in enumerate(zip(baseline, weights), lo) if d]
                expected_capacity = min(bounds) if bounds else None
                assert scan_capacity(event_root, lo, hi, new[0], new[1], window,
                                     12 * window, old) == expected_capacity, (window, i)
                for peak in (None, support(root)):
                    assert maximum_demand(root, new[0], new[1], window, 12 * window, old, peak) == (
                        expected_capacity
                    ), (window, i)
                capacity_checks += 1
                proposed[ticket] = new

            before = coordinates_for(current, window, lo, hi)
            after = coordinates_for(proposed, window, lo, hi)
            trial = root
            for x in sorted(after - before):
                trial = insert(trial, x, value_at(trial, x))
            fused = add_pieces(trial, tuple(delta_segments(window, old, new)))
            if old:
                trial = apply_reservation(trial, old[0], old[1], window, -old[2])
            if new:
                trial = apply_reservation(trial, new[0], new[1], window, new[2])
            for x in sorted(before - after):
                trial = delete(trial, x)
                fused = delete(fused, x)
            check_avl(trial)

            want = slot_totals(proposed, window, lo, hi)
            assert event.allowed(event_root, lo, hi, delta_segments(window, old, new), 12 * window) == (
                max(want) <= 12 * window
            ), (window, i)
            event_trial = event.commit(event_root, window, old, new, priority)
            assert [event.value_at(event_trial, t) for t in range(lo, hi + 1)] == want
            assert allowed(root, delta_segments(window, old, new), 12 * window) == (
                max(want) <= 12 * window
            ), (window, i)
            assert support(trial) == (lo + want.index(max(want)), max(want)), (window, i)
            assert [value_at(trial, t) for t in range(lo, hi + 1)] == want, (window, i)
            assert [value_at(fused, t) for t in range(lo, hi + 1)] == want, (window, i)
            original = slot_totals(current, window, lo, hi)
            assert [value_at(root, t) for t in range(lo, hi + 1)] == original, (window, i)
            if max(want) <= 12 * window:
                root, current = trial, proposed
                event_root = event_trial
                committed += 1
            else:
                rejected += 1
    assert committed and rejected
    return committed, rejected, capacity_checks


if __name__ == "__main__":
    print(f"support comparisons: {check_support()}")
    empty = join(leaf(0, 0), leaf(50, 0))
    assert maximum_demand(empty, 2, 5, 4, 0) == (Fraction(0), 3)
    assert maximum_demand(empty, 2, 5, 4, 5) == (Fraction(5, 3), 5)
    assert maximum_demand(empty, 60, 61, 4, 5) is None
    committed, rejected, capacity_checks = check_reservations()
    print(f"reservation trials: {committed + rejected}, accepted: {committed}, rejected: {rejected}")
    print(f"capacity comparisons: {capacity_checks + 3}")
