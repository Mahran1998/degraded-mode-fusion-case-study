#!/usr/bin/env python3
"""Print one JSON sample of a process's CPU and memory counters (stdlib only).

sched_ns: sum of /proc/<pid>/task/*/schedstat field 1 (on-CPU time of every live thread, ns).
ticks: utime + stime from /proc/<pid>/stat (whole process including exited threads, in CLK_TCK units).
vmhwm_kb: peak resident set size from /proc/<pid>/status.
wall_ns and mono_ns: both clocks, so a wall-clock step inside a window can be detected.
"""
import json
import os
import sys
import time

pid = int(sys.argv[1])
sched = 0
threads = 0
for tid in os.listdir(f"/proc/{pid}/task"):
    try:
        with open(f"/proc/{pid}/task/{tid}/schedstat") as h:
            sched += int(h.read().split()[0])
            threads += 1
    except (FileNotFoundError, ProcessLookupError):
        pass
with open(f"/proc/{pid}/stat") as h:
    after = h.read().rsplit(")", 1)[1].split()
ticks = int(after[11]) + int(after[12])
vmhwm = None
with open(f"/proc/{pid}/status") as h:
    for line in h:
        if line.startswith("VmHWM:"):
            vmhwm = int(line.split()[1])
print(json.dumps({"wall_ns": time.time_ns(), "mono_ns": time.monotonic_ns(), "sched_ns": sched, "ticks": ticks, "clk_tck": os.sysconf("SC_CLK_TCK"),
                  "vmhwm_kb": vmhwm, "threads": threads}))
