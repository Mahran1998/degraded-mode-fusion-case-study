# Commit 1: passive input-availability monitor

A `git am` patch against `openads-project/point_cloud_fusion` at `fb0141e`, the head of the multi-LiDAR
branch (PR #9) when the work started. 10 files, +1456 lines. **It changes no output.**

## What it adds

- `InputAvailabilityMonitor` (`input_availability.hpp/.cpp`), plain C++17 without ROS types:
  - per input: `UNKNOWN → HEALTHY ⇄ STALE → RECOVERING → HEALTHY`, from arrival times, with a 500 ms
    recovery hold;
  - aggregate: `NOMINAL` (all inputs available), `DEGRADED` (at least `min_inputs`), `INSUFFICIENT`;
  - statistics: inter-arrival gaps, silences, detection latency, recovery time, and header-stamp anomalies.
- One const arrival observer per input, registered before the synchronizer, so it sees every message
  first and never copies it.
- A 10 ms evaluation timer, created last in `setup()`. Transitions and a 10 s summary are logged at
  `DEBUG` level only.
- One new read-only parameter, `degraded_mode.input_timeout` (default 0.3 s, provisional).
- Tests: the state machine with simulated time, including a scripted trace checked against the Python
  reference model, and `message_filters` tests showing that a const observer gets the same object the
  synchronizer and the publisher see. A negative control shows that a non-const callback does get a copy.

## Apply and check

```bash
git clone https://github.com/openads-project/point_cloud_fusion.git
cd point_cloud_fusion
git checkout -b monitor fb0141ec127b2e3e16b6c806d37841581a1498fa
git am /path/to/0001-Add-passive-input-availability-monitor-no-output-cha.patch
git rev-parse 'HEAD^{tree}'   # dde36aa089342a94c7fa8576d0c9efc1aa9cf98d
```

Build and test inside the project's dev container, as described in the upstream README. The patch is
not rebased onto the current `main`.

## Gates passed (October 2026, in the project's dev image)

| Gate | CPU build with lint | CI-like build with CUDA 12.8 |
|---|---|---|
| Build | pass | pass |
| Tests | 62/62 | 57/57 |
| clang-tidy (project config) | 0 findings | excluded by the project's CMake when CUDA is on |
| clang-format, flake8 | pass | pass |
| Compiler-warning fingerprints | no new warnings | no new warnings |
| Repository consistency checks | 18/18 | n/a |

The commit message keeps its `Co-Authored-By` trailer. One line, a private link to the assistant session,
was removed from the published patch. The code is unchanged: the tree hash above is the original one.

On-node results: [`../docs/e0-results.md`](../docs/e0-results.md).
