"""Verify the signed peak-error bounds for held-prefix quota sampling."""

import argparse
from bisect import bisect_right
import csv
from itertools import accumulate, product
from pathlib import Path


def analyze(profile, window):
    end = len(profile)
    breaks = [0, *(i for i in range(1, end) if profile[i - 1] != profile[i]), end]
    samples = sorted({t for a, b in zip(breaks, breaks[1:]) for t in range(a, b, window)} | {end})
    prefix = list(accumulate(profile, initial=0))
    sample_set = set(samples)

    def prior(t):
        return samples[max(0, bisect_right(samples, t) - 1)]

    maximum = max(profile, default=0)
    rounded = lambda p: (2 * p + window) // (2 * window)
    exact = []
    held = []
    quantized = []
    for t in range(end + 1):
        left, right = max(0, t - window), t
        a, b = prior(left), prior(right)
        true_value = prefix[right] - prefix[left]
        held_value = prefix[b] - prefix[a]
        round_value = window * (rounded(prefix[b]) - rounded(prefix[a]))
        left_residual = (left - a) * (profile[a] if a < end else 0)
        right_residual = (right - b) * (profile[b] if b < end else 0)
        assert held_value - true_value == left_residual - right_residual
        assert abs(round_value - held_value) < window
        if t in sample_set:
            assert held_value >= true_value
        exact.append(true_value)
        held.append(held_value)
        quantized.append(round_value)
    peak, approximate, rounded_peak = max(exact), max(held), max(quantized)
    assert approximate == max(held[t] for t in samples)
    assert rounded_peak == max(quantized[t] for t in samples)
    max_left_lag = max(max(0, t - window) - prior(max(0, t - window)) for t in samples)
    assert approximate - peak <= maximum * max_left_lag
    for a, b in zip(samples, samples[1:]):
        rate = profile[a]
        for t in range(a, b + 1):
            assert exact[t] <= max(held[a], held[b]) + min(rate * (t - a),
                                                        (maximum - rate) * (b - t))
        assert 4 * (max(exact[a:b + 1]) - approximate) <= maximum * (b - a)
    return peak, approximate, rounded_peak


def exhaustive():
    for w in range(2, 7):
        under = equal = over = rounded_under = rounded_over = 0
        largest_deficit = largest_excess = 0
        for profile in product(range(3), repeat=8):
            peak, held, rounded = analyze(profile, w)
            under += held < peak
            equal += held == peak
            over += held > peak
            rounded_under += rounded < peak
            rounded_over += rounded > peak
            largest_deficit = max(largest_deficit, peak - held)
            largest_excess = max(largest_excess, held - peak)
        yield (w, under + equal + over, under, equal, over, rounded_under, rounded_over,
               largest_deficit, largest_excess)


def sharp_families():
    for w in range(2, 130, 2):
        # Exact peak at 3w/2, halfway between the two sampled endpoints.
        profile = (0,) * (w // 2) + (20,) * (w // 2) + (10,) * w
        assert analyze(profile, w) == (15 * w, 10 * w, 10 * w)
        # A nearly w-long left lag at the end of an otherwise flat profile.
        profile = (20,) * (2 * w - 1)
        peak, held, _ = analyze(profile, w)
        assert (peak, held) == (20 * w, 20 * (2 * w - 1))


def check_nonmonotone():
    smaller = (20,) * 7
    larger = (20,) * 3 + (24,) + (20,) * 3
    assert all(a <= b for a, b in zip(smaller, larger))
    assert analyze(smaller, 4) == (80, 140, 140)
    assert analyze(larger, 4) == (84, 84, 84)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sharp_families()
    check_nonmonotone()
    rows = list(exhaustive())
    print(f"Checked {sum(row[1] for row in rows)} profile/window pairs and 128 sharp examples.")
    print(f"Before rounding: {sum(row[2] for row in rows)} lower peaks, "
          f"{sum(row[4] for row in rows)} higher peaks.")
    if args.out:
        with args.out.open("w", encoding="ascii", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(("window", "profiles", "held_under", "held_equal", "held_over",
                             "rounded_under", "rounded_over", "max_deficit", "max_excess"))
            writer.writerows(rows)
