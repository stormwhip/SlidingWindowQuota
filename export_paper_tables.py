"""Export paper-ready CSV tables from the trace summaries."""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "results" / "public-trace"


def write(name, header, rows):
    with (ROOT / name).open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(name, len(rows), "rows")


def main():
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    sensitivity, latency, margins = [], [], []
    for g in summary:
        sensitivity.append([g["dataset"], g["quota_percent"], g["exact_reject_rate"],
                            g["misjudge_rate"], g["false_accept"], g["false_reject"],
                            g["both_accept"], g["both_integral"], g["both_instant"]])
        for outcome, series in g["latency"].items():
            latency.append([g["dataset"], g["quota_percent"], outcome,
                            series["legacy"]["p95_us"], series["exact"]["p95_us"]])
        for outcome, dims in g["margins"].items():
            for name, values in dims.items():
                margins.append([g["dataset"], g["quota_percent"], outcome, name,
                                values["p50"], values["p95"], values["max"]])
    write("paper-sensitivity.csv",
          ["dataset", "quota_percent", "exact_reject_rate", "misjudge_rate",
           "false_accept", "false_reject", "both_accept", "both_integral", "both_instant"],
          sensitivity)
    write("paper-latency.csv",
          ["dataset", "quota_percent", "outcome", "legacy_p95_us", "exact_p95_us"], latency)
    write("paper-margins.csv",
          ["dataset", "quota_percent", "outcome", "measure", "p50", "p95", "max"], margins)

    worlds = json.loads((ROOT / "worlds-summary.json").read_text(encoding="utf-8"))
    rows = [[g["dataset"], g["quota_percent"], g["legacy_accept"], g["legacy_violating_admits"],
             g["legacy_violating_rate"], g["legacy_max_admit_excess_pct"],
             g["legacy_final_over_quota_pct"], g["exact_final_over_quota_pct"], g["divergent"]]
            for g in worlds]
    write("paper-continuous.csv",
          ["dataset", "quota_percent", "legacy_accept", "legacy_violating_admits",
           "legacy_violating_rate", "legacy_max_admit_excess_pct",
           "legacy_final_over_quota_pct", "exact_final_over_quota_pct", "divergent"], rows)


if __name__ == "__main__":
    main()
