"""Rebuild the manuscript figures from the witness and the paired Java timings."""

import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt


def missed_peak(output):
    rate_breaks = (0, 12, 36)

    def prefix(t):
        return 40 * min(max(t, 0), 12) + 20 * min(max(t - 12, 0), 24)

    def sampled_prefix(t):
        index = bisect_right(rate_breaks, t) - 1
        return prefix(rate_breaks[index]) if index >= 0 else 0

    events = sorted(set(rate_breaks) | {t + 24 for t in rate_breaks})
    fig, (rate, total) = plt.subplots(
        2, 1, figsize=(7.0, 3.25), sharex=True,
        gridspec_kw={"height_ratios": (1, 2)}, layout="constrained",
    )
    rate.step([0, 12, 36, 60], [40, 20, 0, 0], where="post", color="#293e52")
    rate.axvspan(0, 24, color="#0068a0", alpha=0.1)
    rate.text(6.1, 28, "Existing")
    rate.text(25, 25, "Candidate")
    rate.set(ylabel="Rate (MiB)", yticks=[0, 20, 40], ylim=(-2, 48))
    total.plot(events, [prefix(t) - prefix(t - 24) for t in events],
               color="#0068a0", label="Exact window total")
    total.step(events, [sampled_prefix(t) - sampled_prefix(t - 24) for t in events],
               where="post", color="#b65417", linestyle="--", label="Sampled estimate")
    total.axhline(600, color="#4e4e4e", linestyle=":", label="Quota")
    total.scatter(rate_breaks, [sampled_prefix(t) - sampled_prefix(t - 24)
                               for t in rate_breaks], color="#b65417", s=18, zorder=4)
    total.scatter([24], [720], color="#0068a0", s=23, zorder=4)
    total.annotate("720 at hour 24", (24, 720), (28, 815),
                   arrowprops={"arrowstyle": "-", "color": "#0068a0"})
    total.set(xlabel="Window end (hours)", ylabel="Usage (MiB-hours)",
              xticks=[0, 12, 24, 36, 48, 60], ylim=(-25, 900), xlim=(-1, 61))
    total.legend(loc="upper right", frameon=False, fontsize=8)
    fig.savefig(output / "MissedPeak.pdf")
    plt.close(fig)


def scaling(data, output):
    groups = defaultdict(list)
    files = sorted(data.glob("Samples*.csv"))
    if len(files) != 3:
        raise ValueError("Expected three JVM measurement files")
    for path in files:
        with path.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                for policy in ("sampled", "exact"):
                    key = (int(row["history_events"]), int(row["fork"]), policy)
                    groups[key].append(int(row[f"{policy}_ns"]) / 1_000)
    sizes = sorted({events for events, _, _ in groups})
    forks = sorted({fork for _, fork, _ in groups})
    if len(forks) != 3 or len(sizes) != 7 or any(len(values) != 200 for values in groups.values()):
        raise ValueError("Expected seven event counts and 200 pairs per count in each JVM")

    fig, ax = plt.subplots(figsize=(6.5, 2.65), layout="constrained")
    with (output / "EventScalingSummary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("history_events", "policy", "median_us", "min_fork_median_us",
                         "max_fork_median_us"))
        for policy, label, color, marker in (
            ("sampled", "YARN-12022 sampled", "#b65417", "s"),
            ("exact", "Exact", "#0068a0", "o"),
        ):
            fork_medians = [[median(groups[events, fork, policy]) for fork in forks]
                            for events in sizes]
            centers = [median(values) for values in fork_medians]
            lows = [min(values) for values in fork_medians]
            highs = [max(values) for values in fork_medians]
            writer.writerows((n, policy, mid, lo, hi)
                             for n, mid, lo, hi in zip(sizes, centers, lows, highs))
            ax.fill_between(sizes, lows, highs, color=color, alpha=0.15)
            ax.plot(sizes, centers, color=color, marker=marker, markersize=4, label=label)
    ax.set(xscale="log", yscale="log", xlabel="History rate-change events",
           ylabel="Validation latency (µs)")
    ax.set_xticks(sizes, [str(n) for n in sizes])
    ax.grid(axis="y", alpha=0.23)
    ax.legend(frameon=False, loc="upper left")
    fig.savefig(output / "EventScaling.pdf")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("Results/EventScaling"))
    parser.add_argument("--output", type=Path, default=Path("Figures"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "pdf.fonttype": 42, "axes.spines.top": False,
                         "axes.spines.right": False})
    missed_peak(args.output)
    scaling(args.data, args.output)


if __name__ == "__main__":
    main()
