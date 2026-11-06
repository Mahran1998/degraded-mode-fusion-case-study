#!/usr/bin/env python3
"""E0 tool test checker (stdlib only). Judges the publisher run and the logger run of the tool test.

Usage:
  e0_check_tools.py publisher <prefix> --max-gap-ms 150 --offset-tol-ms 3
  e0_check_tools.py logger <publisher_prefix> <logger_csv> --min-receipt 0.95
Each check prints NAME=PASS|FAIL plus the numbers behind it (INFO lines are reported, not judged);
the exit code is 0 only if all judged checks pass. PUB_MAX_GAP guards Q-B: the publisher itself must
never create a silence close to T (0.3 s) while an input is scheduled to be active.
"""
import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict


def quantile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))] if values else float("nan")


def load_gt(prefix):
    with open(f"{prefix}_gt.csv") as handle:
        rows = [{k: int(v) for k, v in row.items()} for row in csv.DictReader(handle)]
    with open(f"{prefix}_meta.json") as handle:
        meta = json.load(handle)
    with open(f"{prefix}_events.csv") as handle:
        events = [(float(r["t_rel_s"]), r["action"], int(r["input"]), int(r["abs_wall_ns"])) for r in csv.DictReader(handle)]
    return rows, meta, events


def verdict(name, ok, detail):
    print(f"{name}={'PASS' if ok else 'FAIL'} {detail}")
    return ok


def check_publisher(args):
    rows, meta, events = load_gt(args.prefix)
    inputs = len(meta["topics"])
    period_ns = int(round(1e9 / meta["rate_hz"]))
    offsets = [float(v) for v in meta["offsets_ms"].split(",")]
    results = []
    print(f"rows={len(rows)} published={meta['published']} skipped={meta['skipped']} go_seen={meta['go_wall_ns'] is not None}")

    by_seq = defaultdict(set)
    for r in rows:
        by_seq[r["seq"]].add(r["stamp_ns"])
    spread_bad = sum(1 for stamps in by_seq.values() if len(stamps) > 1)
    grid_bad = sum(1 for r in rows if (r["stamp_ns"] - meta["base_wall_ns"]) != r["seq"] * period_ns)
    results.append(verdict("PUB_STAMPS", spread_bad == 0 and grid_bad == 0,
                           f"frames_with_stamp_spread={spread_bad} off_grid={grid_bad}"))

    phase_ok, details = True, []
    for i in range(inputs):
        phase = [(r["actual_wall_ns"] - r["stamp_ns"]) / 1e6 for r in rows if r["input"] == i]
        if not phase:
            details.append(f"in{i}=none")
            continue
        median = statistics.median(phase)
        phase_ok &= abs(median - offsets[i]) <= args.offset_tol_ms
        details.append(f"in{i}_median_ms={median:.2f}(want {offsets[i]:.0f})")
    results.append(verdict("PUB_OFFSETS", phase_ok, " ".join(details)))

    late = [(r["actual_mono_ns"] - r["planned_mono_ns"]) / 1e6 for r in rows]
    p50, p99, worst = quantile(late, 0.5), quantile(late, 0.99), max(late) if late else float("nan")
    print(f"PUB_LATENESS=INFO p50_ms={p50:.3f} p99_ms={p99:.3f} max_ms={worst:.3f} rows_over_5ms={sum(1 for x in late if x > 5)}")

    gap_ok, gap_notes = True, []
    for i in range(inputs):
        mine = {r["seq"]: r["actual_wall_ns"] for r in rows if r["input"] == i}
        gaps = [(mine[k + 1] - mine[k]) / 1e6 for k in mine if k + 1 in mine]
        worst_gap = max(gaps) if gaps else float("nan")
        gap_ok &= bool(gaps) and worst_gap <= args.max_gap_ms
        gap_notes.append(f"in{i}_max_ms={worst_gap:.1f}")
    results.append(verdict("PUB_MAX_GAP", gap_ok, " ".join(gap_notes) + f" limit_ms={args.max_gap_ms}"))

    timeline_ok, notes = True, []
    margin_ns = int((max(offsets) + 10) * 1e6)
    for t_rel, action, i, at_ns in events:
        if action != "stop":
            continue
        restart = min((e[3] for e in events if e[1] == "start" and e[2] == i and e[3] > at_ns), default=None)
        end_ns = restart if restart is not None else meta["go_wall_ns"] + int(meta["end_s"] * 1e9)
        inside = [r for r in rows if r["input"] == i and at_ns + margin_ns <= r["stamp_ns"] < end_ns - margin_ns]
        before = [r["actual_wall_ns"] for r in rows if r["input"] == i and r["actual_wall_ns"] < at_ns + margin_ns]
        last_gap_ms = (at_ns - max(before)) / 1e6 if before else float("nan")
        timeline_ok &= not inside
        notes.append(f"stop(in{i}@{t_rel}s): rows_inside={len(inside)} last_publish_before_ms={last_gap_ms:.1f}")
        if restart is not None:
            after = [r["actual_wall_ns"] for r in rows if r["input"] == i and r["actual_wall_ns"] >= restart]
            first_ms = (min(after) - restart) / 1e6 if after else float("nan")
            timeline_ok &= bool(after) and first_ms <= (period_ns / 1e6 + max(offsets) + 10)
            notes.append(f"start(in{i}): first_publish_after_ms={first_ms:.1f}")
    results.append(verdict("PUB_TIMELINE", timeline_ok, "; ".join(notes) or "no stop events"))

    rate_ok, rates = True, []
    for i in range(inputs):
        stamps = sorted(r["stamp_ns"] for r in rows if r["input"] == i)
        gaps = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
        regular = [g for g in gaps if g <= 1.5 * period_ns / 1e6]
        share = len(regular) / len(gaps) if gaps else 0.0
        rate_ok &= bool(gaps)
        rates.append(f"in{i}_regular_gaps={len(regular)}/{len(gaps)}({share:.0%})")
    print(f"PUB_RATE=INFO {' '.join(rates)}")
    return all(results)


def check_logger(args):
    rows, meta, _ = load_gt(args.prefix)
    with open(args.logger_csv) as handle:
        got = [row for row in csv.DictReader(handle)]
    sent = {r["stamp_ns"]: r["actual_wall_ns"] for r in rows}
    received = [int(r["stamp_ns"]) for r in got]
    unknown = [s for s in received if s not in sent]
    receipt = len(set(received) & set(sent)) / len(sent) if sent else 0.0
    latency = [(int(r["recv_wall_ns"]) - sent[int(r["stamp_ns"])]) / 1e6 for r in got if int(r["stamp_ns"]) in sent]
    ok1 = verdict("LOGGER_RECEIPT", receipt >= args.min_receipt,
                  f"sent={len(sent)} received={len(received)} unique_matched={len(set(received) & set(sent))} receipt={receipt:.1%}")
    ok2 = verdict("LOGGER_STAMPS", not unknown and len(received) == len(set(received)),
                  f"unknown_stamps={len(unknown)} duplicates={len(received) - len(set(received))}")
    print(f"logger_latency_ms p50={quantile(latency, 0.5):.3f} p99={quantile(latency, 0.99):.3f} max={max(latency) if latency else float('nan'):.3f}")
    return ok1 and ok2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("publisher")
    p.add_argument("prefix")
    p.add_argument("--max-gap-ms", type=float, default=150.0, help="max gap between consecutive frames of one input")
    p.add_argument("--offset-tol-ms", type=float, default=3.0)
    q = sub.add_parser("logger")
    q.add_argument("prefix")
    q.add_argument("logger_csv")
    q.add_argument("--min-receipt", type=float, default=0.95)
    args = parser.parse_args()
    ok = check_publisher(args) if args.mode == "publisher" else check_logger(args)
    print(f"CHECK_{args.mode.upper()}={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
