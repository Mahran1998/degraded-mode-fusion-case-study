#!/usr/bin/env python3
"""Generate a seeded corpus of synthetic arrival traces for the ApproximateTime
differential test (C++ oracle vs Python reference).

Each trace is one ordered sequence of message arrivals across N inputs. The
families exercise the behaviors relevant to Issue #4 (loss, return, rate
differences, stamp offsets, jitter, ties, reordering, queue pressure) under
varied policy parameters (queue size, max interval, age penalty, lower bounds).

Usage:  python3 gen_traces.py --seed 4242 --count 2000 --out out/traces.txt
"""

import argparse
import os
import random

from tracefmt import write_trace

MS = 1_000_000
BASE = 1_700_000_000 * 10**9          # realistic epoch stamp (fits int32 seconds)

FAMILIES = [
    "nominal", "rates", "stop", "stop_resume", "absent", "intermittent",
    "offset_one", "offset_all", "jitter_threshold", "ties", "reorder",
    "random_drop", "latency_burst",
]


def gen_one(rng, name, family):
    n = rng.choice([2, 2, 3, 3, 4, 4, 4, 5, 6, 9])
    queue = rng.choice([1, 2, 3, 3, 5, 10])
    max_iv = rng.choice([None, 1 * MS, 10 * MS, 10 * MS, 50 * MS])
    age = rng.choice([0.0, 0.1, 0.1, 0.5, 1.0])
    if rng.random() < 0.7:
        lbs = [0] * n
    else:
        lbs = [rng.choice([0, 20 * MS, 50 * MS, 90 * MS, 150 * MS]) for _ in range(n)]
    duration = int(rng.uniform(2.0, 5.0) * 1000) * MS

    period = [100 * MS] * n
    if family == "rates":
        period = [rng.choice([50, 100, 200]) * MS for _ in range(n)]
    elif family not in ("nominal", "ties"):
        period = [(100 + rng.choice([0, 0, -3, 3])) * MS for _ in range(n)]

    phase = [rng.randrange(0, period[k]) for k in range(n)]
    offset = [rng.randrange(-2 * MS, 2 * MS + 1) for _ in range(n)]
    if family == "offset_one":
        k = rng.randrange(n)
        offset[k] += rng.choice([-1, 1]) * rng.choice([30, 150, 500]) * MS
    elif family == "offset_all":
        step = rng.choice([15, 40, 120]) * MS
        offset = [k * step for k in range(n)]
    if family == "ties":
        phase = [0] * n
        offset = [0] * n

    jitter = rng.choice([0, 1 * MS, 5 * MS])
    if family == "jitter_threshold":
        jitter = (max_iv if max_iv is not None else 10 * MS)
    if family == "ties":
        jitter = 0
    lat_max = rng.choice([0, 5 * MS, 30 * MS, 150 * MS])

    # availability windows per input: list of (start, end) in capture time
    windows = [[(0, duration)] for _ in range(n)]
    k = rng.randrange(n)
    if family == "stop":
        windows[k] = [(0, rng.randrange(duration // 4, 3 * duration // 4))]
    elif family == "stop_resume":
        t1 = rng.randrange(duration // 5, duration // 2)
        t2 = t1 + rng.randrange(150 * MS, max(200 * MS, duration // 3))
        windows[k] = [(0, t1), (t2, duration)]
    elif family == "absent":
        windows[k] = [] if rng.random() < 0.6 else [(rng.randrange(duration // 3, 2 * duration // 3), duration)]
    elif family == "intermittent":
        w, t, on = [], 0, True
        while t < duration:
            span = rng.randrange(150 * MS, 1000 * MS)
            if on:
                w.append((t, min(t + span, duration)))
            t += span
            on = not on
        windows[k] = w
    p_drop = rng.uniform(0.05, 0.3) if family == "random_drop" else 0.0

    raw = []
    for inp in range(n):
        m = 0
        prev_arrival = -1
        while True:
            c = phase[inp] + m * period[inp]
            if c >= duration:
                break
            m += 1
            if not any(a <= c < b for a, b in windows[inp]):
                continue
            if p_drop and rng.random() < p_drop:
                continue
            j = rng.randrange(-jitter, jitter + 1) if jitter else 0
            if family == "ties":
                stamp = BASE + (c // (10 * MS)) * (10 * MS)
            else:
                stamp = BASE + c + offset[inp] + j
            lat = rng.randrange(0, lat_max + 1) if lat_max else 0
            if family == "latency_burst" and rng.random() < 0.05:
                lat += rng.choice([300, 600]) * MS
            arrival = c + lat
            if family != "reorder" and arrival <= prev_arrival:
                arrival = prev_arrival + 1           # per-topic FIFO delivery
            prev_arrival = max(prev_arrival, arrival)
            raw.append((arrival, rng.random(), inp, stamp))
    raw.sort()
    events = [(eid, inp, stamp) for eid, (_, _, inp, stamp) in enumerate(raw)]
    return {
        "name": name,
        "params": {"n": n, "queue": queue, "max_interval_ns": max_iv,
                   "age_penalty": age, "lower_bounds_ns": lbs},
        "events": events,
    }


# Values where a long-double product and a double product truncate to different
# integers (found by search in at_reference.scale_duration). They let the SCALE probe
# tell which arithmetic rclcpp::Duration::operator*(double) really uses.
DISCRIMINATING = {
    1 + 1e-9: [-749999924904, -885999866724, -838999891649, 927999883130, -733999914079, 348999948879,
               559999899453, -46999994312, -724999885486, 602999949776, 406999962631, 473999951030],
    1 + 0.123456789: [471219798038, -267703321597, -591406625860, -886428590047, -414406620271, 597549504086,
                      555417621845, -105186814619, -642835185613, 785000012069, 102076930423, 912120914606],
}


def gen_probes(rng):
    scales = sorted({1 + a for a in [0.0, 0.1, 0.5, 1.0, 0.3, 0.25, 1e-9, 0.123456789]})
    lines = []
    for s in scales:
        ns = [0, 1, -1, 3, 7, 10, 999_999_999, 1_000_000_000, -1_000_000_000]
        ns += [rng.randrange(-5 * 10**9, 5 * 10**9) for _ in range(200)]
        ns += [k * 10 + d for k in (rng.randrange(1, 10**8) for _ in range(50)) for d in (-1, 0, 1)]
        ns += DISCRIMINATING.get(s, [])
        lines.append(f"PROBE_SCALE {s!r} " + " ".join(str(v) for v in ns))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--out", default="out/traces.txt")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    n_events = 0
    with open(args.out, "w") as f:
        f.write(f"# seed={args.seed} count={args.count}\n")
        for i in range(args.count):
            fam = FAMILIES[i % len(FAMILIES)]
            t = gen_one(rng, f"t{i:05d}_{fam}", fam)
            n_events += len(t["events"])
            write_trace(f, t)
        for line in gen_probes(rng):
            f.write(line + "\n")
    print(f"traces={args.count} events={n_events} seed={args.seed} out={args.out}")


if __name__ == "__main__":
    main()
