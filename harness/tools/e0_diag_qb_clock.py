#!/usr/bin/env python3
"""E0 Q-B diagnostic (read-only, stdlib only): is the wall clock continuous across one Q-B run?

Usage: python3 -I e0_diag_qb_clock.py <run dir, e.g. .../qb-YYYYMMDD-HHMMSS/run0>

Why: the analyzer compares wall-clock times written by three processes (publisher ground truth,
node log, output logger). That is only valid if CLOCK_REALTIME runs continuously with
CLOCK_MONOTONIC during the run. The publisher and the logger record both clocks for every row,
so a step shows up as a jump in (wall - monotonic). Nothing here judges the monitor; it reports facts.

Sections:
  1. wall-minus-monotonic offset per process (publisher, logger): span and largest step
  2. node log: timestamps that go backwards; largest gap between consecutive lines
  3. monitor counters from every summary: time_jumps, per-input clamped arrivals
  4. per input: where the publisher really paused (sequence gaps), on both clocks, next to
     the recorded timeline and the node's transitions (all relative to go)
  5. fused outputs inside the two outage windows: receive time and header stamp
"""
import csv
import json
import re
import sys
from pathlib import Path

STEP_MS = 5.0  # an offset change above this between consecutive rows counts as a clock step
LINE = re.compile(r"^\[(?P<sev>[A-Z]+)\] \[(?P<t>\d+)\] \[(?P<name>[^\]]+)\]: (?P<msg>.*)$")
IN_T = re.compile(r"^Input availability: input (\d+) \('([^']*)'\) (\w+) -> (\w+)$")
SUM = re.compile(r"^Input availability summary: .*time_jumps=(\d+), dropped_transitions=(\d+)")
SUM_IN = re.compile(r"^Input availability \[(\d+)\] '[^']*': (\w+), arrivals=(\d+) \(before clock (\d+), clamped (\d+)\)")


def rel(ns, go):
    return (ns - go) / 1e9


def offset_report(name, rows, go, go_mono):
    """rows: list of (wall_ns, mono_ns), sorted by mono. Step position is given on both clocks."""
    if len(rows) < 2:
        print(f"{name}: rows={len(rows)} (too few)")
        return False
    offs = [w - m for w, m in rows]
    steps = [(offs[i + 1] - offs[i], i + 1) for i in range(len(offs) - 1)]
    big, at = max(steps, key=lambda s: abs(s[0]))
    print(f"{name}: rows={len(rows)} offset_span_ms={(max(offs) - min(offs)) / 1e6:.3f} "
          f"largest_step_ms={big / 1e6:+.3f} between mono_rel_go_s={(rows[at - 1][1] - go_mono) / 1e9:.3f}"
          f"..{(rows[at][1] - go_mono) / 1e9:.3f} (wall {rel(rows[at - 1][0], go):.3f} -> {rel(rows[at][0], go):.3f}) "
          f"steps_over_{STEP_MS:.0f}ms={sum(1 for s, _ in steps if abs(s) > STEP_MS * 1e6)}")
    return abs(big) > STEP_MS * 1e6


def main():
    run = Path(sys.argv[1])
    pub_meta = json.loads((run / "pub_meta.json").read_text())
    go = pub_meta["go_wall_ns"]
    with open(run / "pub_gt.csv") as h:
        gt = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(h)]
    with open(run / "pub_events.csv") as h:
        events = [(float(r["t_rel_s"]), r["action"], int(r["input"]), int(r["abs_wall_ns"])) for r in csv.DictReader(h)]
    with open(run / "out.csv") as h:
        out = [{k: (int(v) if k != "frame_id" else v) for k, v in r.items()} for r in csv.DictReader(h)]
    lines = (run / "node.log").read_text(errors="replace").splitlines()
    print(f"run_dir={run} go_wall_ns={go} gt_rows={len(gt)} outputs={len(out)} node_log_lines={len(lines)}")

    print("=== 1. wall minus monotonic, per process ===")
    pub_rows = sorted(((r["actual_wall_ns"], r["actual_mono_ns"]) for r in gt), key=lambda x: x[1])
    log_rows = sorted(((r["recv_wall_ns"], r["recv_mono_ns"]) for r in out), key=lambda x: x[1])
    # go is a wall time taken before any later step; the first publisher row gives the wall-mono offset then
    off0 = pub_rows[0][0] - pub_rows[0][1]
    go_mono = go - off0
    print(f"reference: wall-mono offset at the first publish = {off0} ns; go_mono_ns={go_mono}")
    p = offset_report("publisher", pub_rows, go, go_mono)
    lg = offset_report("logger", log_rows, go, go_mono)
    found = [n for n, r in (("publisher", p), ("logger", lg)) if r]
    print(f"CLOCK_STEP={'FOUND in ' + ','.join(found) if found else 'NONE'} (threshold {STEP_MS:.0f} ms between consecutive rows)")

    print("=== 2. node log timestamps ===")
    stamps = []
    for n, raw in enumerate(lines, 1):
        m = LINE.match(raw)
        if m:
            stamps.append((n, int(m["t"]), m["msg"]))
    back = [(stamps[i - 1][1] - stamps[i][1], stamps[i][0], stamps[i][1]) for i in range(1, len(stamps)) if stamps[i][1] < stamps[i - 1][1]]
    gaps = [(stamps[i][1] - stamps[i - 1][1], stamps[i][0], stamps[i][1]) for i in range(1, len(stamps))]
    if back:
        b = max(back)
        print(f"node_log_backward_steps={len(back)} largest_backward_ms={b[0] / 1e6:.3f} at_line={b[1]} at_wall_rel_go_s={rel(b[2], go):.3f}")
    else:
        print("node_log_backward_steps=0")
    if gaps:
        g = max(gaps)
        print(f"node_log_largest_gap_ms={g[0] / 1e6:.1f} ending_at_line={g[1]} at_wall_rel_go_s={rel(g[2], go):.3f}")

    print("=== 3. monitor counters per summary (time_jumps, clamped arrivals per input) ===")
    current = None
    for n, t, msg in stamps:
        if (x := SUM.match(msg)):
            current = {"t": t, "jumps": x[1], "dropped": x[2], "clamped": {}}
            print(f"summary@{rel(t, go):+.2f}s time_jumps={x[1]} dropped={x[2]}", end="")
        elif (x := SUM_IN.match(msg)) and current is not None:
            current["clamped"][int(x[1])] = int(x[5])
            if len(current["clamped"]) == 4:
                print(" clamped=" + ",".join(f"in{i}:{c}" for i, c in sorted(current["clamped"].items())))
                current = None

    print("=== 4. per input: recorded timeline vs real pauses vs node transitions (seconds after go) ===")
    for t_rel, action, i, at in events:
        print(f"recorded {action:5s} in{i} t_rel={t_rel:5.1f}  abs_wall_rel_go={rel(at, go):7.3f}")
    trans = [(t, int(x[1]), x[3], x[4]) for _, t, msg in stamps if (x := IN_T.match(msg))]
    for i in range(4):
        rows = sorted((r for r in gt if r["input"] == i), key=lambda r: r["seq"])
        pauses = [(a, b) for a, b in zip(rows, rows[1:]) if b["seq"] - a["seq"] > 1]
        for a, b in pauses:
            print(f"in{i} pause: last_pub wall={rel(a['actual_wall_ns'], go):7.3f} mono={(a['actual_mono_ns'] - go_mono) / 1e9:7.3f}"
                  f" | next_pub wall={rel(b['actual_wall_ns'], go):7.3f} mono={(b['actual_mono_ns'] - go_mono) / 1e9:7.3f}"
                  f" | frames_skipped={b['seq'] - a['seq'] - 1}")
        mine = " ".join(f"{a}>{b}@{rel(t, go):.3f}" for t, x, a, b in trans if x == i and t >= go - int(5e9))
        print(f"in{i} node transitions (wall): {mine}")

    print("=== 5. fused outputs inside the outage windows ===")
    base = pub_meta["base_wall_ns"]
    period = int(round(1e9 / pub_meta["rate_hz"]))
    for name, a, b in (("j_out", 20.5, 22.5), ("low", 27.5, 29.5)):
        inside = [r for r in out if go + int(a * 1e9) <= r["recv_wall_ns"] < go + int(b * 1e9)]
        if not inside:
            print(f"{name}[{a},{b}] outputs=0")
            continue
        recv = [rel(r["recv_wall_ns"], go) for r in inside]
        recv_mono = [(r["recv_mono_ns"] - go_mono) / 1e9 for r in inside]
        stamp = [rel(r["stamp_ns"], go) for r in inside]
        print(f"{name}[{a},{b}] outputs={len(inside)} recv_wall={min(recv):.3f}..{max(recv):.3f} "
              f"recv_mono={min(recv_mono):.3f}..{max(recv_mono):.3f} stamp_rel_go={min(stamp):.3f}..{max(stamp):.3f} "
              f"stamp_seq={(min(r['stamp_ns'] for r in inside) - base) // period}..{(max(r['stamp_ns'] for r in inside) - base) // period}")
    print("DIAG_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
