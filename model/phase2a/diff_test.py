#!/usr/bin/env python3
"""Differential test: C++ oracle (message_filters 4.11.12) vs Python reference.

Usage:
    python3 diff_test.py out/traces.txt out/oracle_log.txt
    python3 diff_test.py out/traces.txt --write-py-log out/py_log.txt   (Python only)

Exit code: 0 = every trace and every SCALE probe matches exactly,
           1 = at least one mismatch, 2 = oracle log incomplete.
"""

import argparse
import sys
from collections import Counter

from at_reference import run_trace, scale_duration
from tracefmt import parse_log, parse_traces


def first_divergence(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b)) if len(a) != len(b) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces")
    ap.add_argument("oracle_log", nargs="?")
    ap.add_argument("--write-py-log")
    ap.add_argument("--scale-mode", default="longdouble", choices=["longdouble", "double", "exact"])
    args = ap.parse_args()

    traces, probes = parse_traces(args.traces)
    py = {}
    coverage = Counter()
    for t in traces:
        cbs, counters = run_trace(t, scale_mode=args.scale_mode)
        py[t["name"]] = cbs
        coverage.update(counters)

    if args.write_py_log:
        with open(args.write_py_log, "w") as f:
            for t in traces:
                f.write(f"TRACE {t['name']}\n")
                for trig, ids in py[t["name"]]:
                    f.write(f"CB {trig} " + " ".join(map(str, ids)) + "\n")
                f.write(f"END {t['name']} {len(py[t['name']])}\n")
            for s, nss in probes:
                for ns in nss:
                    f.write(f"SCALE {s!r} {ns} {scale_duration(ns, s, args.scale_mode)}\n")

    print("=== Python reference: branch coverage over the corpus ===")
    for key in sorted(coverage):
        print(f"  {key:30s} {coverage[key]}")
    required = ["publish_exhausted", "publish_provably_optimal", "publish_virtual_proof",
                "virtual_cannot_prove", "interval_too_big_delete", "dropped_pivot_delete",
                "queue_overflow_drop", "candidate_destroyed_by_drop", "candidate_better",
                "candidate_not_better", "warn_out_of_order", "warn_below_lower_bound"]
    missing = [k for k in required if coverage[k] == 0]
    print(f"  branches never exercised: {missing if missing else 'none'}")

    if not args.oracle_log:
        return 0

    logs, scales = parse_log(args.oracle_log)
    rc = 0

    print("\n=== Trace comparison ===")
    fam_total, fam_ok = Counter(), Counter()
    mismatches, absent = [], []
    total_cb = 0
    for t in traces:
        name = t["name"]
        fam = name.split("_", 1)[1]
        fam_total[fam] += 1
        if name not in logs:
            absent.append(name)
            continue
        o = logs[name]["callbacks"]
        p = py[name]
        total_cb += len(o)
        if logs[name]["count"] != len(o):
            mismatches.append((name, "count line inconsistent", None, None))
        elif o == p:
            fam_ok[fam] += 1
        else:
            i = first_divergence(o, p)
            mismatches.append((name, i, o[i] if i < len(o) else None, p[i] if i < len(p) else None))
    for fam in sorted(fam_total):
        print(f"  {fam:18s} {fam_ok[fam]:5d} / {fam_total[fam]:5d} exact")
    print(f"  oracle callbacks compared: {total_cb}")
    print(f"  traces missing in oracle log: {len(absent)}")
    print(f"  mismatching traces: {len(mismatches)}")
    for name, i, o, p in mismatches[:10]:
        print(f"    {name}: first divergence at callback #{i}: oracle={o} python={p}")
    if absent:
        rc = 2
    if mismatches:
        rc = max(rc, 1)

    print("\n=== Duration * double probe ===")
    bad = {"longdouble": 0, "double": 0, "exact": 0}
    for s, ns, res in scales:
        for mode in bad:
            if scale_duration(ns, s, mode) != res:
                bad[mode] += 1
    print(f"  probes: {len(scales)}")
    for mode, n_bad in bad.items():
        print(f"  mismatches if emulated as {mode:10s}: {n_bad}")
    if not scales:
        print("  (no SCALE lines in oracle log)")
        rc = max(rc, 2)
    elif bad[args.scale_mode]:
        rc = max(rc, 1)

    print(f"\nRESULT: {'EXACT MATCH' if rc == 0 else 'MISMATCH' if rc == 1 else 'INCOMPLETE'} (rc={rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
