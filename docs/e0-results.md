# E0: Commit 1 on the running node

E0 checked the passive input-availability monitor (Commit 1) on the real `point_cloud_fusion` node.
Each check had its limits fixed before it ran. The tools are in [`../harness/`](../harness/).

## Setup

| Item | Value |
|---|---|
| Image | `ghcr.io/openads-project/point_cloud_fusion:latest-dev` (ROS 2 Jazzy), id `sha256:54649bdd…` |
| Middleware | `rmw_fastrtps_cpp`, one container per run |
| Builds | Base `fb0141e` (PR #9 head) and Commit 1 `cf9d2f1`. Both were rebuilt from clean `git archive` exports, and the rebuilt libraries were byte-identical to earlier builds |
| Host | WSL2 on Windows, 3 CPUs, about 7 GB RAM |
| Inputs | 4 synthetic PointCloud2 streams at 10 Hz from one scheduler. Common header stamps on a 100 ms grid; send offsets 5 / 15 / 25 / 35 ms; seeded jitter of ±2 ms |
| Output | A best-effort subscriber logs every fused message (receive time on both clocks, header stamp, point count) |
| Parameters | The package's own `params.yml`, changing only the topics, the transport hints (`raw`) and `use_cuda`; `degraded_mode.input_timeout = 0.3 s`, `max_time_diff_sec = 0.05 s` |

**Scenario (44 s after "go").** Input *k* is absent at start-up and starts at 0 s. Input *j* stops from
20 s to 23 s. Three inputs stop at 26, 26.5 and 27 s and all return at 30 s, so the aggregate goes
`DEGRADED → INSUFFICIENT → DEGRADED → NOMINAL`. Three runs rotate the roles:

| Run | k (absent at start) | j (single outage) | staggered outage |
|---|---|---|---|
| 0 | 0 | 1 | 2, 3, 0 |
| 1 | 1 | 2 | 3, 0, 1 |
| 2 | 2 | 3 | 0, 1, 2 |

## Gates before the measurement

| Gate | Result |
|---|---|
| Tool test: publisher | Stamps on the grid, offsets as configured, largest gap per input 107 ms (limit 150 ms) |
| Tool test: logger | 50 of 50 messages received; transport p50 0.8 ms, p99 2.0 ms |
| Parameter check | See below |
| Warm-up | The node must start and still be alive 1 s later, otherwise no measured run starts |
| Parameter identity | The generated `params.yml` must have the same sha256 in the check and in every run |
| `QB_CLOCK` | No wall-clock step above 5 ms during the run (see [Clock steps](#clock-steps)) |

**Parameter check.** The shipped `config/params.yml` at `fb0141e` contained two empty YAML lists
(`feature_calibration.leading_inputs` and `follower_inputs`). On Jazzy the YAML parameter parser stores no
value for an empty sequence, and the node aborted at start-up with `No parameter value set`. This
happened on the base build and on Commit 1 alike, so it was not caused by the monitor. Dropping only these
two keys, verified by a diff, let both builds start, and the node logged both defaults as `[]`.
Upstream fixed the default config on 20 October 2026, independently of this work (`dcded65`).

## Results

All judged checks passed in all three valid runs.

| Run | Largest clock step | Detection, last message → `STALE` (ms) | Recovery start (ms) | Monitor: detect / recovery hold (ms) | Observer cost p50 / p99 / max (µs) |
|---|---|---|---|---|---|
| 0 | 0.004 ms | 304.4–306.3 | 2.6–6.2 | 305.7 / 504.6 | 4.0 / 6.2 / 87.6 |
| 1 | 0.024 ms | 301.9–304.6 | 0.6–2.9 | 304.1 / 502.8 | 3.7 / 5.7 / 52.5 |
| 2 | 0.049 ms | 307.6–309.0 | 5.3–8.3 | 308.5 / 507.1 | 3.7 / 6.2 / 66.3 |
| **Limit** | ≤ 5 ms | 300–350 | 0–50 | (300, 330] / 500–530 | reported only |

Also in every run:

- the per-input and aggregate transitions matched the expected sequence exactly;
- there were no transitions in the steady phases, and no spurious `STALE` at start-up;
- the fused output ran at 10.00 Hz in steady phases and at 0.00 Hz during both outage windows. The
  0 Hz comes from the unchanged ApproximateTime path, which Commit 1 does not touch. This is the behaviour
  Issue #4 is about;
- there were 0 dropped transitions and 0 detected time jumps.

The spread of the detection values follows from the design. The monitor evaluates every 10 ms, so
`STALE` is set at the first tick after *last arrival + 300 ms*, plus about 1 ms of delivery.
`RECOVERING` is set on arrival and logged at the next tick.

## Clock steps

The first measured run (16 October) failed in an odd pattern. The input transitions were in the right
order, but most of them were logged *before* the outage that caused them, and the fused output was not
zero during the outages (3.5 and 3.0 Hz).

A read-only diagnosis of the saved run showed why:

| Evidence | Value |
|---|---|
| Publisher and logger, wall minus monotonic time | one step of −1200.2 ms, 19.92 s after "go", in both processes |
| Node log | one backward timestamp step of 1100.7 ms |
| Monitor | 11–12 "clamped" arrivals on each live input after the step; `time_jumps = 0` |
| Outputs inside the outage windows | header stamps from *after* the inputs returned |
| Host journal | `systemd-resolved: Clock change detected` within 4 ms of the computed step, and again every ~31 s |

The next day the host showed steps of −2527 ms and −2570 ms, 32.24 s apart. `systemd-timesyncd` reported a
32 s poll interval and a −2.55 s offset, with 103 clock changes since boot. This matches a known WSL2
problem ([microsoft/WSL#11790](https://github.com/microsoft/WSL/issues/11790)). With `systemd-timesyncd`
stopped, a 70 s check showed no steps, and the guest clock ran at the Windows clock's rate within the
measurement error (−0.49 % ± 0.71 %). The three valid runs were made with timesyncd stopped and the
`QB_CLOCK` gate on.

**What this means for the monitor.** Commit 1 measures availability on the node clock, which is system
time unless `use_sim_time` is set. In the run with the −1.2 s step:

- the monitor's backward-jump counter stayed at 0. rcl calls a timer's clock-jump handler only when ROS
  time is overridden, so after a system-clock step the 10 ms timer simply waits until the clock catches
  up, and no tick ever sees time go backwards;
- the outage of input 1 was flagged 1.506 s after its last message on the monotonic clock, while the
  monitor's own statistics reported about 305 ms;
- no spurious transitions occurred.

A forward step larger than the timeout would mark every input `STALE` at once. This was not observed,
only inferred. Clock steps also happen on vehicles, for example when PTP or GNSS time locks after
start-up. The conclusion is to use a **steady clock** for liveness, keep ROS time only with
`use_sim_time`, and detect steps explicitly. The tools were changed accordingly: the Q-B analyzer
declares a run `INVALID` (not `FAIL`) after any step, and the overhead comparison measures durations on
the monotonic clock.

## Limits

- The inputs are synthetic and share identical header stamps, so the temporal check (stamps outside
  `max_time_diff_sec`) was not exercised on the node.
- All times are guest-clock times inside WSL2. Relative comparisons are valid; absolute values may differ
  from real time by a small percentage.
- With three runs, input 3 never started absent and input 0 never had the single outage.
- The CPU and memory comparison between base and Commit 1 (Q-C) was prepared, but not run before the
  work stopped.
- Only the monitor exists. Degraded publication itself was not implemented.
