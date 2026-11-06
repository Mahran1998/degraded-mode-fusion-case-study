# Offline model

The model ran before any C++ was written, so that design choices could be compared on the same
scenarios without the node.

## Phase 2A: a trusted ApproximateTime model ([`phase2a/`](phase2a/))

- `at_reference.py` is a Python port of `message_filters` 4.11.12 `ApproximateTime`, with line references.
- `oracle/` is a small C++ program that drives the real `Synchronizer<ApproximateTime<…>>` in trace order
  and prints every callback.
- `gen_traces.py` generates a seeded corpus: 13 families (nominal, different rates, stops, resumes,
  absent inputs, intermittent inputs, offsets, jitter, ties, reordering, drops, latency bursts), N = 2–9,
  and varied policy parameters.
- **Result:** identical callbacks on all 2,000 traces (19,144 callbacks), with every policy branch
  exercised. Only after that was the Python model trusted.

See [`phase2a/README.md`](phase2a/README.md) for how to build and run the oracle. `SHA256SUMS` lists
the original file hashes.

## Phase 2C: architecture comparison (results)

- 7 architectures and variants × 162 traces = **27,378 runs**, with N = 2, 4 and 9.
- Pass/warn/fail rules fixed in the code before the run. 0 invariant violations.
- Reproduced byte for byte in a second execution environment.
- The summary is in the design proposal ([`../docs/proposal-r3.md`](../docs/proposal-r3.md), "Model
  results in short").
