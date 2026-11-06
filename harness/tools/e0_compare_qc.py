#!/usr/bin/env python3
"""Compare the four Q-C results (ABBA: base idle, c1 idle, c1 active, base active) against the registered flags.

Usage: e0_compare_qc.py <base_idle.json> <c1_idle.json> <c1_active.json> <base_active.json>
Flags (design v2.3 §10, to be approved before the run): idle CPU +1.0 percentage point of one core;
active CPU +max(1.0 pp, 10 % of base); output rate within 2 %; VmHWM +5 MB; no new WARN/ERROR.
Validity gate QC_CLOCK: a wall-clock step above 5 ms inside any window makes the set INVALID (exit 2).
"""
import json
import sys

CLOCK_STEP_LIMIT_MS = 5.0  # wall minus monotonic change inside one window; duration_s is monotonic


def pct(r):
    return 100.0 * r["cpu_sched_ns"] / (r["duration_s"] * 1e9)


def pct_ticks(r):
    return 100.0 * r["cpu_ticks"] / r["clk_tck"] / r["duration_s"]


def main():
    bi, ci, ca, ba = (json.load(open(p)) for p in sys.argv[1:5])
    steps = {r["label"] + "_" + r["mode"]: r.get("clock_step_ms") for r in (bi, ci, ca, ba)}
    if any(v is None or abs(v) > CLOCK_STEP_LIMIT_MS for v in steps.values()):
        print(f"QC_CLOCK=INVALID clock_step_ms={steps} limit_ms={CLOCK_STEP_LIMIT_MS:.0f} (rerun the affected window)")
        print("QC_OVERALL=INVALID")
        return 2
    print(f"QC_CLOCK=PASS clock_step_ms={steps}")
    ok = True

    def line(name, passed, detail):
        nonlocal ok
        ok &= passed
        print(f"{name}={'PASS' if passed else 'FLAG'} {detail}")

    res_pp = 100.0 / bi["clk_tck"] / bi["duration_s"]
    print(f"cpu_%core sched: base_idle={pct(bi):.3f} c1_idle={pct(ci):.3f} c1_active={pct(ca):.3f} base_active={pct(ba):.3f}")
    print(f"cpu_%core ticks: base_idle={pct_ticks(bi):.3f} c1_idle={pct_ticks(ci):.3f} c1_active={pct_ticks(ca):.3f} "
          f"base_active={pct_ticks(ba):.3f} (tick resolution {res_pp:.3f} pp)")
    d_idle = pct(ci) - pct(bi)
    line("QC_IDLE_CPU", d_idle <= 1.0, f"c1-base={d_idle:+.3f} pp limit=+1.000 pp")
    d_act = pct(ca) - pct(ba)
    lim = max(1.0, 0.10 * pct(ba))
    line("QC_ACTIVE_CPU", d_act <= lim, f"c1-base={d_act:+.3f} pp limit=+{lim:.3f} pp")
    rb, rc = ba["outputs"] / ba["duration_s"], ca["outputs"] / ca["duration_s"]
    line("QC_RATE", rb > 0 and abs(rc - rb) / rb <= 0.02, f"base={rb:.2f} Hz c1={rc:.2f} Hz limit=±2%")
    d_mem = max(ci["vmhwm_kb"] - bi["vmhwm_kb"], ca["vmhwm_kb"] - ba["vmhwm_kb"]) / 1024
    line("QC_VMHWM", d_mem <= 5.0, f"max(c1-base)={d_mem:+.2f} MB limit=+5 MB "
         f"(idle {bi['vmhwm_kb']}/{ci['vmhwm_kb']} kB, active {ba['vmhwm_kb']}/{ca['vmhwm_kb']} kB)")
    wb, wc = bi["warn"] + bi["error"] + ba["warn"] + ba["error"], ci["warn"] + ci["error"] + ca["warn"] + ca["error"]
    line("QC_LOGS", wc <= wb, f"warn+error base={wb} c1={wc}")
    print(f"threads base={bi['threads']}/{ba['threads']} c1={ci['threads']}/{ca['threads']} (idle/active)")
    print(f"QC_OVERALL={'PASS' if ok else 'FLAG'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
