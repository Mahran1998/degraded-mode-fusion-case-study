# Degraded mode for multi-LiDAR point cloud fusion: a case study

This repository documents my work on
[Issue #4](https://github.com/openads-project/point_cloud_fusion/issues/4)
of [`point_cloud_fusion`](https://github.com/openads-project/point_cloud_fusion), a ROS 2 node in
[OpenADS](https://github.com/openads-project), the open-source automated-driving stack developed at
ika, RWTH Aachen University.

The issue asks for a **degraded mode**: when one input sensor stops, the node should keep fusing the
remaining inputs instead of stalling.

> **Status.** This work was not merged. In late October 2026 the maintainers continued with another
> contributor's implementation ([PR #16](https://github.com/openads-project/point_cloud_fusion/pull/16)).
> This repository keeps my part: the analysis, the offline model, a first C++ component, and the
> fault-injection harness with its results.

---

## The problem

The node fuses N point clouds with the ROS 2 `message_filters` **ApproximateTime** synchronizer.
ApproximateTime only emits complete sets, one message per input. **If one of four LiDARs goes silent,
the fused output stops completely, without a warning**, so everything downstream loses the other three
sensors as well. I reproduced this on the real node with Docker-based fault injection before designing
anything.

## What I did

| When (2026) | Step |
|---|---|
| Sep | Reproduced the stall on the node and posted the reproduction on the issue. The maintainer invited a design proposal: *"Make sure it is a generic solution that works with any (realistic) setup."* |
| Sep | Read the node's source (`main` and the multi-LiDAR feature branch, PR #9), tracing how synchronized batches are produced and consumed |
| Sep–Oct | Built an **offline model** of the synchronization layer and compared design families on it (see [`model/`](model/)) |
| 3 Oct | Posted a design proposal on the issue ([`docs/proposal-r3.md`](docs/proposal-r3.md)). The maintainer delegated the design: *"create a version of the node that you think makes sense and then create a PR"* |
| 8–9 Oct | Implemented **Commit 1**, a passive input-availability monitor in C++ (see [`monitor/`](monitor/)) |
| 14–17 Oct | Built a **fault-injection harness** around the project's dev container and validated Commit 1 on the running node (see [`harness/`](harness/) and [`docs/e0-results.md`](docs/e0-results.md)) |
| late Oct | The maintainers continued with PR #16; I stopped my implementation and published this case study |

## Key findings

1. **Input identity is positional.** The fusion handler receives a vector in which position `i` is
   configured input `i`. This holds only because every batch is complete. A partial batch without an
   explicit absence marker would shift positions, and on the multi-LiDAR branch one sensor's calibration
   could be applied to another sensor's points. Any degraded mode has to keep each sensor's identity.
2. **"Alive" is not "fusable".** A sensor can keep publishing while its timestamps no longer fit the
   others. In the model, designs that only reacted to missing messages produced **0 Hz** in that case.
   A degraded mode needs both an availability check (arrival time) and a temporal check (`header.stamp`).
3. **ApproximateTime publishes late by design.** In the model at 10 Hz it emitted each set a median of
   92 ms after the set's last member arrived, because it waits until later messages prove no better set
   can follow. A design that publishes earlier changes nominal timing for existing users.
4. **Liveness needs a steady clock.** On my WSL2 machine, time sync stepped the system clock back by
   1.2–2.5 s about every 32 s. The monitor measured time on the node clock (system time), so one outage
   was detected 1.5 s after the last message (expected about 0.3 s), while the monitor itself reported
   305 ms, and its backward-jump counter stayed at 0. A system clock can also step forward, for example
   when PTP or GNSS time locks after start-up. The test harness now has a clock-validity gate. Details:
   [`docs/e0-results.md`](docs/e0-results.md#clock-steps).

## Evidence

**Offline model** (Python, no ROS; [`model/`](model/))

- A port of ApproximateTime from `message_filters` 4.11.12, checked against a C++ oracle compiled with
  the real headers: **identical callbacks on 2,000 seeded traces** (19,144 callbacks), every policy branch
  exercised.
- A pre-registered scenario matrix: **7 architectures and variants, 162 traces, 27,378 runs, 0 invariant
  violations**, with 2, 4 and 9 inputs. The pass/warn/fail rules were fixed before the runs, and the
  matrix was reproduced byte for byte in a second environment.
- Four design families compared: rebuilding the synchronizer, a fully custom synchronizer, a timer with
  latest-message selection, and a hybrid that keeps ApproximateTime for nominal operation and hands over
  to a degraded selector. The proposal summarises the trade-offs.

**Commit 1: passive input-availability monitor** ([`monitor/`](monitor/))

- Per-input `UNKNOWN / HEALTHY / STALE / RECOVERING` and aggregate `NOMINAL / DEGRADED / INSUFFICIENT`,
  from arrival times, with a 500 ms recovery hold. Plain C++17 without ROS types, unit-tested with
  simulated time. It **observes only**: synchronization, fusion and publishing are unchanged.
- It passed the project's gates in both build configurations: build, tests (62/62 and 57/57), clang-tidy
  with 0 findings, clang-format, no new compiler-warning fingerprints, and the repository consistency
  checks.
- Tests show that the observer, the synchronizer and the published message share the same object (no
  copy), and that ApproximateTime output is identical with and without the observer (N = 2 and 4).

**On the running node** ([`docs/e0-results.md`](docs/e0-results.md))

Four synthetic 10 Hz inputs in the project's dev container, with scripted outages (one input absent at
start-up, one single outage, three staggered outages); timeout 300 ms; 3 runs that rotate the roles
across inputs.

| Measure | Result | Pre-registered limit |
|---|---|---|
| Outage detection, last message → `STALE` | 301.9–309.0 ms | 300–350 ms |
| Recovery start after an input returns | 0.6–8.3 ms | 0–50 ms |
| Recovery hold, measured by the monitor | 502.8–507.1 ms | 500–530 ms |
| Unexpected transitions in steady phases | 0 | 0 |
| Monitor cost per message (p50 / p99) | 3.7–4.0 / 5.7–6.2 µs | reported only |

## Repository layout

| Path | Content |
|---|---|
| [`docs/proposal-r3.md`](docs/proposal-r3.md) | The design proposal as posted on Issue #4 |
| [`docs/e0-results.md`](docs/e0-results.md) | Node experiments: setup, results, the clock-step diagnosis, limits |
| [`model/`](model/) | Offline model: ApproximateTime port and C++ oracle (Phase 2A) |
| [`monitor/`](monitor/) | Commit 1 as a `git am` patch against the multi-LiDAR branch |
| [`harness/`](harness/) | Fault-injection tools: publisher, output logger, runners, analyzers, clock checks |

## How I worked

- **Reproduce before designing, model before coding, measure before claiming.** Each step had pass
  criteria fixed before the run.
- **Evidence tags** in my notes: verified by output, inferred, or open. Model results are never presented
  as node results.
- **Negative controls** for every check that can pass trivially: a forced crash, an injected clock step,
  an extra config change.
- **Reproducible builds:** the node was rebuilt from clean `git archive` exports and the libraries were
  byte-identical.
- **AI assistance.** I used AI assistants (Claude and another model) for drafting, review and
  cross-checking, and ran every claim against command output. Commit 1 carries a `Co-Authored-By` trailer.

## What I would do next

- Move availability to a steady clock (ROS time only with `use_sim_time`) and detect clock steps
  explicitly.
- Run the same harness against any degraded-mode implementation: it measures, from the fused output, how
  quickly partial batches start after an input stops, the output rate during outages, and how quickly
  full batches return.

## License and credits

Apache License 2.0, the same as `point_cloud_fusion` (see [`LICENSE`](LICENSE) and [`NOTICE.md`](NOTICE.md)).
Thanks to the OpenADS maintainers at ika, RWTH Aachen University, for the open discussion on the issue.

Mahran Rezk · [GitHub](https://github.com/Mahran1998) · [LinkedIn](https://www.linkedin.com/in/mahran-rezk-3724b9213)
