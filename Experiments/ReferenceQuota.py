"""Reference model of YARN's sampled quota policy and of an exact window scan.

The sampled path is a line-by-line port of CapacityOverTimePolicy.validate in
Hadoop 3.5.0 (YARN-12022 baseline): cumulative change points plus intermediate
points every window, prefix normalisation with Math.round, and the integral
computed as the difference of an up-front and a W-shifted down-front map.

The exact path checks the window total at every change point of the merged
profile and at every change point shifted by W, using integer arithmetic.

Times are milliseconds, rates are whole cores and MiB, matching the replay in
PublicTraceQuotaReplayTest (seconds scaled by 1000).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Sequence

DAY_MS = 86_400_000
MEM_PER_CORE = 1024
BASE_MS = 1_600_000_000_000  # keeps start - window positive for every trace row


def float32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


def quota_limits(capacity_cores: int, percent: int) -> tuple[int, int]:
    """Replicate Resources.multiply(Resource, float): truncation toward zero."""
    factor = float32(percent / 100.0)
    cores = int(capacity_cores * factor)
    memory = int(MEM_PER_CORE * capacity_cores * factor)
    return cores, memory


@dataclass(frozen=True)
class Allocation:
    start: int
    end: int
    cores: int
    memory: int | None = None

    @property
    def mem(self) -> int:
        return MEM_PER_CORE * self.cores if self.memory is None else self.memory


def _clip_events(allocs: Iterable[Allocation], lo: int, hi: int) -> dict[int, tuple[int, int]]:
    events: dict[int, list[int]] = {}
    for a in allocs:
        left, right = max(a.start, lo), min(a.end, hi)
        if left >= right:
            continue
        e = events.setdefault(left, [0, 0])
        e[0] += a.cores
        e[1] += a.mem
        e = events.setdefault(right, [0, 0])
        e[0] -= a.cores
        e[1] -= a.mem
    return {t: (v[0], v[1]) for t, v in events.items()}


def cumulative_at(allocs: Sequence[Allocation], lo: int, hi: int) -> tuple[list[int], list[int], list[int]]:
    """Return sorted times and cumulative (cores, memory) resource-ms from lo."""
    events = _clip_events(allocs, lo, hi)
    times = sorted(events)
    if not times or times[0] != lo:
        # every clipped allocation start at lo, so an event exists unless empty
        times = [lo] + times
    cores, mem = events.get(times[0], (0, 0))
    csum = [0]
    msum = [0]
    prev = times[0]
    for t in times[1:]:
        d = t - prev
        csum.append(csum[-1] + cores * d)
        msum.append(msum[-1] + mem * d)
        cores += events.get(t, (0, 0))[0]
        mem += events.get(t, (0, 0))[1]
        prev = t
    return times, csum, msum


def _value_at(times: Sequence[int], values: Sequence[int], t: int, default: int = 0) -> int:
    """Last value with key <= t (RLE 'hold last value until next key')."""
    lo, hi = 0, len(times) - 1
    if not times or t < times[0]:
        return default
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid - 1
    return values[lo]


def _round_div(value: int, window: int) -> int:
    """Java Math.round(double value / window): floor(x + 0.5)."""
    return (2 * value + window) // (2 * window)


def sampled_accept(
    history: Sequence[Allocation],
    candidate: Allocation,
    old: Allocation | None,
    *,
    window: int = DAY_MS,
    limit_cores: int,
    limit_mem: int,
    density: int = 1,
    rounding: bool = True,
    dimensions: str = "both",
) -> bool:
    """Validate the candidate under the sampled policy.

    history includes the reservation being replaced, as in YARN's plan.
    density=k samples intermediate points every window/k instead of every
    window. rounding=False removes the Math.round normalisation, which
    isolates sampling error from prefix rounding.
    """
    import bisect

    lo, hi = candidate.start - window, candidate.end + window
    allocs = list(history)
    if old is not None:
        allocs.append(Allocation(old.start, old.end, -old.cores, -old.mem))
    allocs.append(candidate)

    # RLE change points; keep the entry in force at lo and drop keys past hi,
    # mirroring RLESparseResourceAllocation.getRangeOverlapping(lo, hi).
    delta: dict[int, list[int]] = {}
    for a in allocs:
        if a.end <= 0 or a.start >= hi:
            continue
        d = delta.setdefault(a.start, [0, 0])
        d[0] += a.cores
        d[1] += a.mem
        d = delta.setdefault(a.end, [0, 0])
        d[0] -= a.cores
        d[1] -= a.mem
    keys = sorted(k for k in delta if k <= hi)
    if not keys:
        return True
    pos = bisect.bisect_right(keys, lo) - 1
    first = keys[pos] if pos >= 0 else keys[0]
    keys = keys[max(pos, 0):]
    rate = [0, 0]
    for k in sorted(delta):
        if k > first:
            break
        rate[0] += delta[k][0]
        rate[1] += delta[k][1]
    cum = {first: (0, 0)}
    prev = first
    for k in keys[1:]:
        pc, pm = cum[prev]
        cum[k] = (pc + rate[0] * (k - prev), pm + rate[1] * (k - prev))
        rate[0] += delta[k][0]
        rate[1] += delta[k][1]
        prev = k

    step = max(1, window // density)
    samples = set(keys)
    prev = keys[0]
    for k in keys[1:]:
        i = 1
        while prev + i * step < k:
            samples.add(prev + i * step)
            i += 1
        prev = k
    sample_times = sorted(samples)

    def value_at(s: int) -> tuple[int, int]:
        j = bisect.bisect_right(keys, s) - 1
        if j < 0:
            return (0, 0)
        if keys[j] == s or j + 1 >= len(keys):
            return cum[keys[j]]
        span = keys[j + 1] - keys[j]
        c0, m0 = cum[keys[j]]
        c1, m1 = cum[keys[j + 1]]
        return (c0 + (c1 - c0) // span * (s - keys[j]),
                m0 + (m1 - m0) // span * (s - keys[j]))

    sample_values = [value_at(s) for s in sample_times]
    limits = {"cores": limit_cores, "memory": limit_mem, "both": None}[dimensions]
    for t in sorted(set(sample_times) | {s + window for s in sample_times}):
        if t < lo or t > hi:
            continue
        j = bisect.bisect_right(sample_times, t) - 1
        up = sample_values[j] if j >= 0 else (0, 0)
        j = bisect.bisect_right(sample_times, t - window) - 1
        down = sample_values[j] if j >= 0 else (0, 0)
        if rounding:
            total = (_round_div(up[0], window) - _round_div(down[0], window),
                     _round_div(up[1], window) - _round_div(down[1], window))
            lim = (limit_cores, limit_mem)
        else:
            total = (up[0] - down[0], up[1] - down[1])
            lim = (limit_cores * window, limit_mem * window)
        if dimensions == "cores":
            if total[0] > lim[0]:
                return False
        elif dimensions == "memory":
            if total[1] > lim[1]:
                return False
        elif total[0] > lim[0] or total[1] > lim[1]:
            return False
    return True


def true_cumulative(times: Sequence[int], csum: Sequence[int], t: int) -> int:
    """Cumulative resource-ms P(t) from the event list (P is piecewise linear)."""
    if t <= times[0]:
        return 0
    if t >= times[-1]:
        return csum[-1]
    lo, hi = 0, len(times) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid - 1
    span = times[lo + 1] - times[lo]
    rate = (csum[lo + 1] - csum[lo]) // span if span else 0
    return csum[lo] + rate * (t - times[lo])


def exact_peak(history: Sequence[Allocation], candidate: Allocation, old: Allocation | None,
               window: int = DAY_MS) -> tuple[int, int]:
    """Exact maximum window total (cores, memory) over windows the candidate can touch."""
    profile = list(history)
    if old is not None:
        profile.append(Allocation(old.start, old.end, -old.cores, -old.mem))
    profile.append(candidate)
    lo = max(0, candidate.start - window)
    hi = candidate.end + window
    times, csum, msum = cumulative_at(profile, lo, hi)
    shifted = sorted({t + window for t in times} | set(times))
    peak_c = peak_m = 0
    for t in shifted:
        if t < lo or t > hi:
            continue
        c = true_cumulative(times, csum, t) - true_cumulative(times, csum, t - window)
        m = true_cumulative(times, msum, t) - true_cumulative(times, msum, t - window)
        peak_c = max(peak_c, c)
        peak_m = max(peak_m, m)
    return peak_c, peak_m


def exact_accept(history, candidate, old, *, window=DAY_MS, limit_cores, limit_mem) -> bool:
    c, m = exact_peak(history, candidate, old, window)
    return c <= limit_cores * window and m <= limit_mem * window


# ---------------------------------------------------------------- instantaneous

def instant_ok(allocs: Sequence[Allocation], candidate: Allocation, capacity_cores: int,
               capacity_mem: int) -> bool:
    """NoOverCommitPolicy at maxInst=100%: cluster-wide usage never exceeds capacity."""
    lo, hi = candidate.start, candidate.end
    events: dict[int, list[int]] = {}
    for a in list(allocs) + [candidate]:
        left, right = max(a.start, lo), min(a.end, hi)
        if left >= right:
            continue
        e = events.setdefault(left, [0, 0])
        e[0] += a.cores
        e[1] += a.mem
        e = events.setdefault(right, [0, 0])
        e[0] -= a.cores
        e[1] -= a.mem
    cores = mem = 0
    for t in sorted(events):
        cores += events[t][0]
        mem += events[t][1]
        if cores > capacity_cores or mem > capacity_mem:
            return False
    return True
