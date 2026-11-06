#!/usr/bin/env python3
"""E0 Q-B analyzer (stdlib only): judges one monitor run against the pre-registered limits (design v2.2).

Input: a run directory with node.log, pub_gt.csv, pub_events.csv, pub_meta.json, out.csv, run_meta.json.
Node log lines must use RCUTILS_CONSOLE_OUTPUT_FORMAT="[{severity}] [{time_as_nanoseconds}] [{name}]: {message}".
Prints NAME=PASS|FAIL lines (judged) and NAME=INFO lines (reported only).
Validity gate QB_CLOCK: a wall-clock step above 5 ms (publisher or logger wall-minus-monotonic, or a
backward node-log timestamp) makes the run INVALID. Exit code: 0 PASS, 1 FAIL, 2 INVALID.
"""
import csv
import json
import re
import sys
from pathlib import Path

LINE = re.compile(r"^\[(?P<sev>[A-Z]+)\] \[(?P<t>\d+)\] \[(?P<name>[^\]]+)\]: (?P<msg>.*)$")
IN_T = re.compile(r"^Input availability: input (\d+) \('([^']*)'\) (\w+) -> (\w+)$")
AGG_T = re.compile(r"^Input availability: (\w+) -> (\w+)$")
NUM = r"(\d+(?:\.\d+)?|nan|inf)"
SUM = re.compile(r"^Input availability summary: (\w+), inputs=(\d+), min_inputs=(\d+), timeout=" + NUM
                 + r" ms, time_jumps=(\d+), dropped_transitions=(\d+), observer cost \[us\] p50=" + NUM
                 + r" p99=" + NUM + r" max=" + NUM + r" \(n=(\d+)\)$")
SUM_IN = re.compile(r"^Input availability \[(\d+)\] '([^']*)': (\w+), arrivals=(\d+) .*?; detect n=(\d+) max=" + NUM
                    + r" ms; recovery n=(\d+) max=" + NUM + r" ms;")

MS = 1_000_000
CLOCK_STEP_LIMIT_NS = 5 * MS


def load(run):
    meta = json.loads((run / "run_meta.json").read_text())
    events_in, events_agg, summaries, first_log = [], [], [], None
    log_times = []
    current = None
    for raw in (run / "node.log").read_text(errors="replace").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        t = int(m["t"])
        log_times.append(t)
        first_log = t if first_log is None else first_log
        msg = m["msg"]
        if (x := IN_T.match(msg)):
            events_in.append((t, int(x[1]), x[3], x[4]))
        elif (x := AGG_T.match(msg)):
            events_agg.append((t, x[1], x[2]))
        elif (x := SUM.match(msg)):
            current = {"t": t, "aggregate": x[1], "time_jumps": int(x[5]), "dropped": int(x[6]),
                       "cost_p50_us": x[7], "cost_p99_us": x[8], "cost_max_us": x[9], "cost_n": int(x[10]), "inputs": {}}
            summaries.append(current)
        elif (x := SUM_IN.match(msg)) and current is not None:
            current["inputs"][int(x[1])] = {"state": x[3], "arrivals": int(x[4]), "detect_n": int(x[5]),
                                            "detect_max_ms": float(x[6]), "recovery_n": int(x[7]),
                                            "recovery_max_ms": float(x[8])}
    with open(run / "pub_gt.csv") as h:
        gt = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(h)]
    pub_meta = json.loads((run / "pub_meta.json").read_text())
    with open(run / "pub_events.csv") as h:
        ev = [(float(r["t_rel_s"]), r["action"], int(r["input"]), int(r["abs_wall_ns"])) for r in csv.DictReader(h)]
    out, out_pairs = [], []
    if (run / "out.csv").exists():
        with open(run / "out.csv") as h:
            for r in csv.DictReader(h):
                out.append(int(r["recv_wall_ns"]))
                out_pairs.append((int(r["recv_wall_ns"]), int(r["recv_mono_ns"])))
    return meta, events_in, events_agg, summaries, first_log, gt, pub_meta, ev, out, out_pairs, log_times


def largest_offset_step(pairs):
    """Largest change of (wall - monotonic) between consecutive rows, sorted by monotonic time (ns)."""
    pairs = sorted(pairs, key=lambda x: x[1])
    offs = [w - m for w, m in pairs]
    return max((abs(b - a) for a, b in zip(offs, offs[1:])), default=0)


def verdict(name, ok, detail, results):
    print(f"{name}={'PASS' if ok else 'FAIL'} {detail}")
    results.append(ok)


def info(name, detail):
    print(f"{name}=INFO {detail}")


def main():
    run = Path(sys.argv[1])
    meta, ev_in, ev_agg, summaries, first_log, gt, pub_meta, events, out, out_pairs, log_times = load(run)
    T = int(meta["T_s"] * 1e9)
    k, j, low, end_s = meta["k"], meta["j"], meta["low"], meta["end_s"]
    go = pub_meta["go_wall_ns"]
    results = []
    print(f"run={meta['run']} k={k} j={j} low={low} T_ms={T / MS:.0f} go_seen={go is not None} "
          f"input_lines={len(ev_in)} aggregate_lines={len(ev_agg)} summaries={len(summaries)}")
    if go is None:
        verdict("QB_GO", False, "go was never reached; nothing else can be judged", results)
        return 1
    end = go + int(end_s * 1e9)

    # ---- validity gate: every comparison below assumes one continuous wall clock -------------
    # The publisher and the logger record wall and monotonic time per row; the node log has wall
    # time only, so it is checked for backward steps. A step makes the run INVALID (not FAIL).
    pub_step = largest_offset_step([(r["actual_wall_ns"], r["actual_mono_ns"]) for r in gt])
    log_step = largest_offset_step(out_pairs)
    node_back = max((a - b for a, b in zip(log_times, log_times[1:]) if b < a), default=0)
    clock_ok = max(pub_step, log_step, node_back) <= CLOCK_STEP_LIMIT_NS
    print(f"QB_CLOCK={'PASS' if clock_ok else 'INVALID'} publisher_step_ms={pub_step / MS:.3f} "
          f"logger_step_ms={log_step / MS:.3f} node_log_backward_ms={node_back / MS:.3f} limit_ms={CLOCK_STEP_LIMIT_NS / MS:.0f}")

    # ---- startup (before go) ---------------------------------------------------------------
    pre_in = [e for e in ev_in if e[0] < go]
    pre_agg = [e for e in ev_agg if e[0] < go]
    last_state = {}
    for _, i, _, to in pre_in:
        last_state[i] = to
    k_stale = [e for e in pre_in if e[1] == k and e[2] == "UNKNOWN" and e[3] == "STALE"]
    others_ok = all(last_state.get(i) == "HEALTHY" for i in range(4) if i != k)
    agg_at_go = pre_agg[-1][2] if pre_agg else "NOMINAL"
    verdict("QB_STARTUP", bool(k_stale) and last_state.get(k) == "STALE" and others_ok and agg_at_go == "DEGRADED",
            f"k_unknown_to_stale={bool(k_stale)} state_at_go={dict(sorted(last_state.items()))} aggregate_at_go={agg_at_go}",
            results)
    paths = []
    for i in range(4):
        seq = [f"{a}>{b}" for _, x, a, b in pre_in if x == i]
        first = next((t for t, x, _, _ in pre_in if x == i), None)
        delay = f"{(first - first_log) / MS:.0f}ms" if first is not None and first_log is not None else "n/a"
        paths.append(f"in{i}:[{' '.join(seq)}]@{delay}")
    spurious = [i for i in range(4) if i != k and any(x == i and b == "STALE" for _, x, _, b in pre_in)]
    info("O11_STARTUP", f"spurious_startup_stale={spurious} " + " ".join(paths))

    # ---- expected sequences inside [go, end) -----------------------------------------------
    exp_in = {i: [] for i in range(4)}
    exp_in[k] += [("STALE", "RECOVERING"), ("RECOVERING", "HEALTHY")]
    exp_in[j] += [("HEALTHY", "STALE"), ("STALE", "RECOVERING"), ("RECOVERING", "HEALTHY")]
    for x in low:
        exp_in[x] += [("HEALTHY", "STALE")]
    for x in low:
        exp_in[x] += [("STALE", "RECOVERING"), ("RECOVERING", "HEALTHY")]
    exp_agg = [("DEGRADED", "NOMINAL"), ("NOMINAL", "DEGRADED"), ("DEGRADED", "NOMINAL"), ("NOMINAL", "DEGRADED"),
               ("DEGRADED", "INSUFFICIENT"), ("INSUFFICIENT", "DEGRADED"), ("DEGRADED", "NOMINAL")]
    win_in = [e for e in ev_in if go <= e[0] < end]
    win_agg = [e for e in ev_agg if go <= e[0] < end]
    bad = []
    for i in range(4):
        got = [(a, b) for _, x, a, b in win_in if x == i]
        if got != exp_in[i]:
            bad.append(f"in{i} got={got} want={exp_in[i]}")
    verdict("QB_SEQUENCE_INPUTS", not bad, "; ".join(bad) or "all 4 inputs exactly as expected", results)
    got_agg = [(a, b) for _, a, b in win_agg]
    verdict("QB_SEQUENCE_AGGREGATE", got_agg == exp_agg,
            f"got={got_agg}" + ("" if got_agg == exp_agg else f" want={exp_agg}"), results)

    # ---- end-to-end detection and recovery start (ground truth vs log time) -----------------
    def pubs(i):
        return sorted(r["actual_wall_ns"] for r in gt if r["input"] == i)

    stops = [(t_rel, i, at) for t_rel, act, i, at in events if act == "stop"]
    starts = [(t_rel, i, at) for t_rel, act, i, at in events if act == "start"]
    det, det_notes = [], []
    for t_rel, i, at in stops:
        before = [p for p in pubs(i) if p < at]
        stale = next((t for t, x, a, b in win_in if x == i and b == "STALE" and t >= at), None)
        if not before or stale is None:
            det_notes.append(f"in{i}@{t_rel}s:missing")
            det.append(False)
            continue
        e2e = stale - before[-1]
        det.append(T <= e2e <= T + 50 * MS)
        det_notes.append(f"in{i}@{t_rel}s:{e2e / MS:.1f}ms")
    verdict("QB_DETECT_E2E", all(det) and bool(det), " ".join(det_notes) + " limit=[T, T+50ms]", results)
    rec, rec_notes, rec_dur = [], [], []
    for t_rel, i, at in starts:
        after = [p for p in pubs(i) if p >= at]
        r_t = next((t for t, x, a, b in win_in if x == i and b == "RECOVERING" and t >= at), None)
        h_t = next((t for t, x, a, b in win_in if x == i and b == "HEALTHY" and r_t is not None and t >= r_t), None)
        if not after or r_t is None:
            rec_notes.append(f"in{i}@{t_rel}s:missing")
            rec.append(False)
            continue
        d = r_t - after[0]
        rec.append(0 <= d <= 50 * MS)
        rec_notes.append(f"in{i}@{t_rel}s:{d / MS:.1f}ms")
        if h_t is not None:
            rec_dur.append(f"in{i}@{t_rel}s:{(h_t - r_t) / MS:.0f}ms")
    verdict("QB_RECOVERY_START", all(rec) and bool(rec), " ".join(rec_notes) + " limit=[0, 50ms]", results)
    info("RECOVERY_LOG_DURATION", " ".join(rec_dur) + " (log-to-log; the monitor-side value is judged below)")

    # ---- monitor-side histograms from the last summary inside the window --------------------
    inside = [s for s in summaries if s["t"] < end]
    last = inside[-1] if inside else None
    if last is None or not last["inputs"]:
        verdict("QB_SUMMARY", False, "no summary before the end of the window", results)
    else:
        det_max = max((v["detect_max_ms"] for v in last["inputs"].values() if v["detect_n"] > 0), default=None)
        rec_max = max((v["recovery_max_ms"] for v in last["inputs"].values() if v["recovery_n"] > 0), default=None)
        det_ok = det_max is not None and T / MS < det_max <= T / MS + 30
        rec_ok = rec_max is not None and 500 <= rec_max <= 530
        verdict("QB_DETECT_MONITOR", det_ok, f"detect_max_ms={det_max} limit=(T, T+30ms] summary_age_before_end_s={(end - last['t']) / 1e9:.1f}", results)
        verdict("QB_RECOVERY_MONITOR", rec_ok, f"recovery_max_ms={rec_max} limit=[500, 530]", results)
        verdict("QB_INTEGRITY", last["dropped"] == 0 and last["time_jumps"] == 0,
                f"dropped_transitions={last['dropped']} time_jumps={last['time_jumps']}", results)
        info("OBSERVER_COST_US", f"p50={last['cost_p50_us']} p99={last['cost_p99_us']} max={last['cost_max_us']} n={last['cost_n']}")

    # ---- steady phases: no transitions -------------------------------------------------------
    quiet = [(1.5, 20.0), (31.5, end_s)]
    noise = [e for e in win_in if any(go + int(a * 1e9) <= e[0] < go + int(b * 1e9) for a, b in quiet)]
    notes = []
    for t, i, a, b in noise:
        p = [x for x in pubs(i) if x < t]
        gap = (p[-1] - p[-2]) / MS if len(p) >= 2 else float("nan")
        blame = "publisher" if p and (t - p[-1]) >= T else "monitor_or_transport"
        notes.append(f"in{i}:{a}>{b}@{(t - go) / 1e9:.2f}s last_gap_ms={gap:.0f} blame={blame}")
    verdict("QB_STEADY", not noise, "; ".join(notes) or "0 transitions in steady phases", results)

    # ---- fused output (reported only) ---------------------------------------------------------
    def rate(a, b):
        n = sum(1 for t in out if go + int(a * 1e9) <= t < go + int(b * 1e9))
        return n / (b - a)
    info("OUTPUT_RATE_HZ", f"steady[2,19]={rate(2, 19):.2f} j_out[20.5,22.5]={rate(20.5, 22.5):.2f} "
         f"low[27.5,29.5]={rate(27.5, 29.5):.2f} tail[32,{end_s - 1:.0f}]={rate(32, end_s - 1):.2f}")
    if not clock_ok:
        print("QB_RUN=INVALID (wall clock stepped during the run; the verdicts above are not evidence either way)")
        return 2
    ok = all(results)
    print(f"QB_RUN={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
