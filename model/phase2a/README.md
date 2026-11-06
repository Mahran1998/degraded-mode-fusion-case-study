# pcf-proto — ApproximateTime reference model + C++ oracle (Phase 2A)

Purpose: establish a **trusted model of the nominal synchronizer** before comparing
architectures A–D for Issue #4. The C++ oracle (real `message_filters` 4.11.12
headers) is the source of truth. The Python reference model is trusted **only after**
it reproduces the oracle exactly on the whole trace corpus.

Nothing here touches the `point_cloud_fusion` repository. No ROS on the host.

## Files

| File | Role |
|---|---|
| `at_reference.py` | Python port of `sync_policies/approximate_time.h` @ 4.11.12, line references `Lnnn` in comments |
| `oracle/oracle.cpp`, `oracle/CMakeLists.txt` | C++ oracle: drives `Synchronizer<ApproximateTime<...>>::add<i>()` directly in trace order and prints every callback |
| `tracefmt.py` | Shared trace / log format |
| `gen_traces.py` | Seeded corpus: 13 families (nominal, rates, stop, stop_resume, absent, intermittent, offset_one, offset_all, jitter_threshold, ties, reorder, random_drop, latency_burst), N = 2–9, varied queue / max interval / age penalty / lower bounds, plus `Duration * double` probes |
| `diff_test.py` | Compares oracle vs Python per trace; prints branch coverage and the arithmetic probe result |
| `run_oracle.sh` | Builds and runs the oracle in a throwaway Jazzy container |
| `mf-4.11.12/` | **Not shipped** — you already extracted it from `latest-slim` with `docker cp` |

`oracle.cpp` contains an `#ifdef AT_STUB` branch. It is used only for a local
self-check with stub rclcpp types; the real build never defines it.

## What the oracle proves — and what it does not

- ✅ Policy semantics: which tuples `ApproximateTime` 4.11.12 emits, and after which
  arrival, for a given **arrival order** and parameters.
- ❌ Not threading, executor scheduling or real arrival interleavings (those remain
  Q6 / Q10). The trace fixes the arrival order.
- The linked `libmessage_filters.so` comes from the base image and may be a
  different patch version; the selection logic is header-only and comes from the
  extracted 4.11.12 headers. The build prints which header was compiled.

## Pass criteria

- `RESULT: EXACT MATCH (rc=0)`: every trace's callback sequence is identical
  (same tuples, same trigger event), and every `SCALE` probe matches the
  long-double emulation.
- `branches never exercised: none`.
- Anything else: the Python model is **not** trusted yet.
