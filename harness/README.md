# Fault-injection harness

These tools ran the experiments in [`../docs/e0-results.md`](../docs/e0-results.md). The `*_inner.sh`
runners run **inside** the project's dev container (`ghcr.io/openads-project/point_cloud_fusion:latest-dev`)
with a built workspace mounted at `/out`. The analyzers are plain Python 3 (stdlib only) and also run on
the host.

| Tool | Role |
|---|---|
| `e0_publisher.py` | N PointCloud2 streams from one scheduler: common stamps on a grid, per-input send offsets, seeded jitter, and a scripted stop/start timeline (`"t:start|stop:i[,j];..."`). Writes ground truth (`_gt.csv`), events and metadata |
| `e0_output_logger.py` | Logs every fused message: receive time on the wall and monotonic clocks, header stamp, point count |
| `e0_make_params.py` | Derives the test `params.yml` from the package's own file. Changes only topics, transport hints and `use_cuda`, and drops two known empty YAML lists that crash Jazzy. Any other empty list stops it |
| `e0_tooltest_inner.sh`, `e0_check_tools.py` | Tests the tools themselves: publisher stamps, offsets and gaps; logger receipt |
| `e0_paramcheck_inner.sh` | Checks one build with the shipped params and with the derived params; the verdict needs both the log line and the exit code |
| `e0_qb_inner.sh`, `e0_analyze_qb.py` | Q-B: the scripted outage scenario (warm-up, start-up, single and staggered outages, recovery) and its pre-registered verdicts |
| `e0_qc_inner.sh`, `e0_proc_sample.py`, `e0_compare_qc.py` | Q-C: CPU, memory, output rate and log counts, idle and active, in ABBA order (prepared, not run) |
| `e0_diag_qb_clock.py` | Read-only diagnosis of a saved run: wall-clock steps, node-log monotonicity, clamped arrivals, real pauses |
| `e0_clock_sampler.py` | Host check: wall-clock steps over N seconds, and the guest clock rate against the Windows clock (WSL2) |

## Run one Q-B scenario

```bash
BUILD=/path/to/workspace-with-install      # contains install/point_cloud_fusion
PCDS=/path/to/pcds                          # files named <n>_fl.pcd, <n>_fr.pcd, <n>_rl.pcd, <n>_rr.pcd
RUN=/path/to/output-dir; mkdir -p "$RUN"
docker run --rm \
  --mount type=bind,source="$BUILD",target=/out,readonly \
  --mount type=bind,source="$PWD/tools",target=/e0/tools,readonly \
  --mount type=bind,source="$PCDS",target=/e0/pcds,readonly \
  --mount type=bind,source="$RUN",target=/e0/run \
  -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_DOMAIN_ID=42 \
  -e E0_TOOLS=/e0/tools -e E0_PCDS=/e0/pcds -e E0_OUT=/e0/run \
  -e E0_RUNS="0 1 2" -e E0_T_S=0.3 -e E0_END_S=44 \
  ghcr.io/openads-project/point_cloud_fusion:latest-dev bash /e0/tools/e0_qb_inner.sh
```

`QB_OVERALL` is `PASS`, `FAIL` or `INVALID`. `INVALID` means the wall clock stepped during a run, so its
verdicts are not evidence either way. On WSL2, check the host with `e0_clock_sampler.py` first; see
[Clock steps](../docs/e0-results.md#clock-steps).

## Design rules

- Verdict limits are written in the analyzers before any run.
- A runner never starts a measured run after a failed warm-up.
- Every check that could pass trivially has a negative control (forced crash, injected clock step,
  extra config change).
