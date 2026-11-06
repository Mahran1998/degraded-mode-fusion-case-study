#!/usr/bin/env python3
"""E0 host clock check (read-only, stdlib only). Run on the WSL host, not in a container.

Usage: python3 -I e0_clock_sampler.py [--duration-s 70] [--interval-s 0.2]

1. Steps: samples CLOCK_REALTIME and CLOCK_MONOTONIC every interval; a change of (wall - mono)
   above 5 ms between two samples is a step of the wall clock. Reports each step, its size, and the
   time since the previous one.
2. Rate: if Windows interop works, reads the Windows (host) clock through powershell.exe at the start
   and the end and compares the elapsed time with the guest's monotonic clock. A guest clock that
   runs fast shows a positive rate; the uncertainty comes from the time each powershell call takes.
Nothing is changed on the system.
"""
import argparse
import subprocess
import time

STEP_NS = 5_000_000
PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()"]


def host_ms():
    """(host_unix_ms, guest_mono_ns at the middle of the call, call duration ns) or None."""
    try:
        before = time.monotonic_ns()
        out = subprocess.run(PS, capture_output=True, text=True, timeout=30)
        after = time.monotonic_ns()
        value = int(out.stdout.strip().splitlines()[-1])
        return value, (before + after) // 2, after - before
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        return f"{type(exc).__name__}: {exc}"[:160]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=70.0)
    parser.add_argument("--interval-s", type=float, default=0.2)
    args = parser.parse_args()

    start_host = host_ms()
    mono0, wall0 = time.monotonic_ns(), time.time_ns()
    prev_off, steps, samples = wall0 - mono0, [], 0
    end = mono0 + int(args.duration_s * 1e9)
    while time.monotonic_ns() < end:
        time.sleep(args.interval_s)
        mono, wall = time.monotonic_ns(), time.time_ns()
        off = wall - mono
        samples += 1
        if abs(off - prev_off) > STEP_NS:
            steps.append(((mono - mono0) / 1e9, (off - prev_off) / 1e6))
        prev_off = off
    mono1, wall1 = time.monotonic_ns(), time.time_ns()
    end_host = host_ms()

    print(f"sampler: duration_s={(mono1 - mono0) / 1e9:.1f} interval_s={args.interval_s} samples={samples}")
    last = None
    for i, (t, size) in enumerate(steps, 1):
        since = f"{t - last:.2f}" if last is not None else "n/a"
        print(f"step #{i}: at_mono_s={t:.2f} size_ms={size:+.1f} since_previous_s={since}")
        last = t
    print(f"CLOCK_STEPS={len(steps)} (threshold {STEP_NS / 1e6:.0f} ms) "
          f"wall_minus_mono_change_total_ms={((wall1 - mono1) - (wall0 - mono0)) / 1e6:+.1f}")

    if isinstance(start_host, tuple) and isinstance(end_host, tuple):
        host_el = (end_host[0] - start_host[0]) / 1e3
        mono_el = (end_host[1] - start_host[1]) / 1e9
        unc = (start_host[2] + end_host[2]) / 2 / 1e9
        rate = (mono_el - host_el) / host_el * 100
        print(f"HOST_REF=ok host_elapsed_s={host_el:.3f} guest_mono_elapsed_s={mono_el:.3f} "
              f"guest_mono_vs_host={rate:+.2f} % uncertainty_s=±{unc:.2f} (±{unc / host_el * 100:.2f} %)")
    else:
        reason = start_host if not isinstance(start_host, tuple) else end_host
        print(f"HOST_REF=unavailable ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
