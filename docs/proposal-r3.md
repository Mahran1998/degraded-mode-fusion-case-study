# Design proposal (r3)

Posted as a comment on [Issue #4](https://github.com/openads-project/point_cloud_fusion/issues/4) on 3 October 2026, addressed to the maintainer. Reproduced here unchanged. Line references point to `main@48a69c7` and PR #9 at `fb0141e`.

---

Hi Bastian,

thanks for leaving the design open. Below is my proposal for #4.

The design is intended to be generic: any configured number of inputs from 2 to 9, with independent input rates and timestamp offsets. It also aims to keep today's behavior when the feature is disabled, and to keep input identity intact, including together with PR #9.

So far I have only evaluated it in an offline model, with 2, 4 and 9 inputs and one synthetic timing setup. Before writing any C++, I would like your view on a few decisions (section 4).

### 1. What this is based on

- I read the current node (`main@48a69c7`) and PR #9 (`fb0141e`), focusing on how synchronized batches are produced and consumed.
- I built an offline model of the synchronization layer (Python, no ROS). Its `ApproximateTime` part is a port of `message_filters` 4.11.12. I checked it against a small C++ harness compiled with the real headers: identical output on 2,000 randomized traces.
- With that model, I compared four design families and some variants on synthetic scenarios with 2, 4 and 9 inputs (the two subset scenarios with 4 and 9 only):
  - an input stops;
  - an input is absent from startup;
  - a single isolated message after an outage;
  - recovery;
  - an intermittent input;
  - a live input with misaligned timestamps;
  - several valid subsets;
  - no valid subset;
  - all inputs healthy.

This is model evidence only. Nothing has been measured on the node yet, and the pass/fail thresholds were my own.

### 2. Two findings in the current code that shape the design

1. **Input identity is positional.**
   - The handler receives a vector where position `i` is configured input `i`. This holds today because `ApproximateTime` always delivers a complete tuple, so no position shifts.
   - The producer currently filters out null entries ([point_cloud_fusion.cpp L784–796](https://github.com/openads-project/point_cloud_fusion/blob/48a69c75f57dfe2140f5b30991e7c265700419c1/point_cloud_fusion/src/point_cloud_fusion.cpp#L784-L796)). If an absent slot were introduced through this callback path, the vector would be compacted and positions would no longer match configured inputs.
   - On PR #9, this could apply one sensor's calibration mapping to another sensor's points (`point_cloud_fusion.cpp` L1237–1241 at `fb0141e`). This comes from reading the code; I have not tested it.
   - So a degraded batch must either carry the configured index explicitly or keep fixed positions with an absence marker.
2. **"Alive" is not the same as "fusable".**
   - A sensor can publish normally while its stamps are outside `max_time_diff_sec`.
   - If degraded mode only reacts to missing messages, the node can stall silently in that case. In the model, designs that reacted only to availability had a steady output rate of 0 Hz there.
   - So the design needs both an availability check (arrival time) and a temporal check (`header.stamp`).

### 3. Proposed direction (a candidate, pending your answers)

**Availability per input**

- States, based on arrival time: `UNKNOWN → HEALTHY ⇄ STALE → RECOVERING → HEALTHY`.
- The timeout is derived from each input's observed period (e.g. 3 × the median interval), with an absolute fallback. The exact rule should be set after measuring real per-input arrival times.
- Recovery needs more than one message: a count or a time window.

**Node state**

- `NOMINAL`: all configured inputs healthy.
- `DEGRADED`: at least `min_inputs` healthy.
- `INSUFFICIENT`: fewer than `min_inputs` healthy.
- Separately, each fusion decision reports `FULL`, `PARTIAL` or `NO_VALID_BATCH`. A live sensor with ineligible stamps is then reported as "not fusable", not as "missing".

**Publication**

- In `NOMINAL`: the existing `ApproximateTime` path, unchanged. In the model, its output was identical to the disabled reference.
- In `DEGRADED`: a selector picks a temporally consistent subset of healthy inputs (at least `min_inputs`, within `max_time_diff_sec`).
- If all inputs are alive but the nominal path stays silent for a bounded starvation interval (the misaligned-stamp case), the selector may take over until nominal output resumes. The model used 300 ms; that is a model parameter, not a proposed default.
- In the model, both paths feed a single publication gate, which suppresses duplicate batch offers and prevents overlap between nominal and degraded output. This is not yet verified under the node's real executor and callback concurrency.
- With the feature disabled, the intent is to keep the existing path as it is. In the model, the disabled path matched the original `ApproximateTime` output on all 2,000 traces.

**Diagnostics**

Availability, batch outcome and per-input state, probably via `diagnostic_msgs`. That would be a new dependency, so it needs your OK.

**How this covers the aspects in the issue**

| Aspect | Covered by |
|---|---|
| Configurable timeout | Relative per-input timeout with absolute fallback |
| Minimum usable inputs | `min_inputs` |
| Temporal consistency | Subset within `max_time_diff_sec` |
| Diagnostics | Availability state, batch outcome, per-input state |
| Reliable recovery | Hysteresis |
| Generic | Design intent: any configured N from 2 to 9, no assumptions about the demo's four sensors. Modeled with N = 2, 4 and 9 |

**How the alternatives compared in the model** (synthetic traces at 10 Hz)

- **Rebuilding the synchronizer** on every availability change thrashed with an intermittent sensor: about 23 rebuilds in 10 s, and a median of ~3.5 Hz output instead of 10 Hz.
- **A fully custom synchronizer** handled the fault scenarios. In normal operation it produced the same sets with different timing: a median of 0 ms from the last member's arrival to publication, versus 92 ms for the `ApproximateTime` reference. If the node behaves the same, that is a visible change for existing users.
- **A timer with latest-message selection** covered the most fault scenarios, but changed nominal timing and, mainly during faults, reused the same message in several outputs.
- **The hybrid with hand-over** (the candidate above) kept nominal output identical and passed every modeled scenario at N = 9. It still fails or warns in some scenarios at N = 2 and N = 4, and it depends on the open decisions below. None of this is validated on the node.

### 4. Decisions I would like your input on

**Main decisions**

1. **Default:** off by default (strict backward compatibility), or on?
2. **Nominal behavior with the feature enabled:** must it stay exactly as today (same sets, same timing), or is it fine if the same sets are published earlier? Your answer decides between the hybrid and a fully custom synchronizer.
3. **Batch representation** (identity, PR #9): a fixed-size vector with a null entry or validity mask for absent inputs, or entries that carry the configured input index?
4. **Several valid subsets:** which should win: the most inputs, the smallest timestamp spread, the previously selected subset, or another deterministic rule? And should there be at most one output per cycle? In the model, two equal-size sensor groups on different capture phases made the selection alternate between the groups.

**Smaller points** (I would follow your preference)

5. **Clock for availability:** steady clock, or ROS time, so that a paused bag does not mark every input stale?
6. **Startup:** how long may an input be absent at startup before the node fuses without it? The model used 0.5 s.
7. **All inputs stop:** should the node publish nothing more, or is one last partial batch acceptable?
8. **Waiting for a partial batch:** I propose a bound, so that newer arrivals can never postpone a partial publication indefinitely. The exact rule is open.
9. **`output_stamp_mode = input0`** when configured input 0 is missing from a degraded batch: use the first selected input, or something else? On `main`, input 0 also anchors the output field layout, so this affects more than the timestamp.
10. **Invalid parameters** (e.g. `min_inputs > N`): fail at startup with a clear error, as PR #9 does for invalid calibration config?
11. **A dependency on `diagnostic_msgs`:** OK?

### 5. Next step

If the direction is fine with you, I would start with a small C++ spike behind a parameter, off by default, to check on the real node:

- callback and executor behavior;
- real timing, detection and recovery;
- the interaction between the nominal and degraded paths;
- a batch representation that works for the existing consumers and PR #9.

Only after that would I open an implementation PR. I am happy to share the model and the full evidence tables if that is useful.

<details>
<summary>Model results in short (synthetic traces, 2/4/9 inputs, my own thresholds)</summary>

| Family | Normal operation vs today | Input stops / absent / returns | Intermittent input | Live input, misaligned stamps | Two equal valid groups |
|---|---|---|---|---|---|
| Rebuild synchronizer | identical | ok | fails (rebuild thrash) | fails (no output) | fails (no output) |
| Rebuild + timestamp-eligibility check | identical | ok | fails (rebuild thrash) | ok | warn (alternates) |
| Custom synchronizer | same sets, published earlier | ok | warn at N=2 | fails at N=2 | warn (both groups published) |
| Hybrid, without hand-over | identical | ok | warn at N=2 | fails (no output) | fails (no output) |
| Hybrid, with hand-over (candidate) | identical | ok | warn at N=2 | fails at N=2 | warn (both groups published) |
| Timer, latest messages | same sets, different timing | ok, with message reuse | ok, with message reuse | ok | warn (alternates) |

A side observation: in the model at 10 Hz, `ApproximateTime` published each set a median of 92 ms after its last member arrived. My reading of `approximate_time.h` (`process()`) is that it publishes a set only once later messages show that no better set can follow. If the same holds on the node, this is already today's behavior and not something #4 introduces.

</details>

Best regards,
Mahran
