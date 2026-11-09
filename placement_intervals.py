"""Exact integer start intervals for a fixed-duration, constant-rate reservation."""

from collections import Counter
from heapq import merge
from itertools import groupby


def changes(segments):
    edges = Counter()
    for start, end, rate in segments:
        edges[start] += rate
        edges[end] -= rate
    return sorted((t, delta) for t, delta in edges.items() if delta)


def _push(intervals, first, last):
    # The incoming interval contains a point no earlier than the representative
    # of the last interval. It can extend left only by merging with the suffix.
    while intervals and first <= intervals[-1][1] + 1:
        old_first, old_last = intervals.pop()
        assert last >= old_first - 1
        first, last = min(first, old_first), max(last, old_last)
    intervals.append((first, last))


def window_pieces(edges, window, first, last):
    shifted = ((t + window, -delta) for t, delta in edges)
    timeline = merge(edges, shifted, ((first, 0), (last, 0)))
    previous = slope = value = 0
    for t, group in groupby(timeline, key=lambda item: item[0]):
        if t > last:
            break
        if first <= previous < t:
            yield previous, t, value, slope
        value += (t - previous) * slope
        slope += sum(delta for _, delta in group)
        previous = t


def quota_forbidden(edges, first, last, duration, demand, window, quota):
    """Project over-quota windows to integer starts. The history must be feasible."""
    overlap_limit = min(window, duration)
    if demand * overlap_limit > quota:
        return [(first, last)]
    threshold = quota - demand * overlap_limit
    forbidden = []
    for left, right, value, slope in window_pieces(edges, window, first, last + duration + window):
        right_value = value + (right - left) * slope
        assert max(value, right_value) <= quota, "The historical quota is already exceeded"
        if max(value, right_value) <= threshold:
            continue
        begin, end = left, right
        if slope > 0:
            begin = max(begin, left + (threshold - value) // slope + 1)
        elif slope < 0:
            end = min(end, left + (value - threshold - 1) // -slope)
        k_begin = (quota - value - (begin - left) * slope) // demand + 1
        k_end = (quota - value - (end - left) * slope) // demand + 1
        lower = min(begin + k_begin, end + k_end) - window - duration
        upper = max(begin - k_begin, end - k_end)
        _push(forbidden, lower, upper)
    return [(max(first, a), min(last, b)) for a, b in forbidden if a <= last and b >= first]


def capacity_forbidden(edges, first, last, duration, demand, capacity):
    blocked = []
    used = 0
    previous = first
    for t, delta in merge(edges, ((first, 0), (last + duration, 0))):
        if t > last + duration:
            break
        if t > previous >= first and used + demand > capacity:
            _push(blocked, max(first, previous - duration + 1), min(last, t - 1))
        used += delta
        previous = t
    return blocked


def allowed_starts(quota_blocks, capacity_blocks, first, last):
    """Return sorted maximal closed integer ranges outside both forbidden lists."""
    allowed = []
    cursor = first
    for start, end in merge(quota_blocks, capacity_blocks):
        if cursor < start:
            allowed.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= last:
        allowed.append((cursor, last))
    return allowed
