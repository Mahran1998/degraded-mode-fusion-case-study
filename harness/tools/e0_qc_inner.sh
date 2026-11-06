# E0 Q-C measurement, inner part (one build mounted at /out; design v2.2 §5).
# Env: E0_TOOLS, E0_PCDS, E0_OUT (writable), E0_LABEL (base|c1), E0_MODE (idle|active), E0_DURATION_S (60).
# Optional overrides for local tests: E0_SETUP, E0_NODE_EXE, E0_PARAMS_SRC.
# Exit codes: 31 setup, 32 params, 33 logger, 34 publisher, 35 node, 36 warm-up.
set -u
export PYTHONUNBUFFERED=1 RCUTILS_COLORIZED_OUTPUT=0 RCUTILS_LOGGING_BUFFERED_STREAM=0 RCUTILS_LOGGING_USE_STDOUT=1
export RCUTILS_CONSOLE_OUTPUT_FORMAT='[{severity}] [{time_as_nanoseconds}] [{name}]: {message}'
SETUP="${E0_SETUP:-/out/install/setup.bash}"
NODE_EXE="${E0_NODE_EXE:-/out/install/point_cloud_fusion/lib/point_cloud_fusion/point_cloud_fusion}"
PARAMS_SRC="${E0_PARAMS_SRC:-/out/install/point_cloud_fusion/share/point_cloud_fusion/config/params.yml}"
TOPICS="/e0/in/input_0,/e0/in/input_1,/e0/in/input_2,/e0/in/input_3"
OUT_TOPIC="/point_cloud_fusion/point_cloud"
SETTLE_S=5
STOP_GRACE_S=5
# colcon/ament setup scripts are not nounset-safe
set +u
# shellcheck disable=SC1090
source "$SETUP" || { echo "SETUP=FAIL ($SETUP)"; exit 31; }
set -u
wait_file() {  # $1=file $2=timeout_s $3=pid
  local i
  for i in $(seq 1 $(($2 * 10))); do
    [ -e "$1" ] && return 0
    kill -0 "$3" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}
wait_line() {  # $1=file $2=pattern $3=timeout_s $4=pid
  local i
  for i in $(seq 1 $(($3 * 10))); do
    grep -q "$2" "$1" 2>/dev/null && return 0
    kill -0 "$4" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}
stop_pid() {
  local pid="$1" label="$2" i
  kill -TERM "$pid" 2>/dev/null || { echo "${label}_stop=already_exited"; wait "$pid" 2>/dev/null; return; }
  for i in $(seq 1 $((STOP_GRACE_S * 10))); do
    if ! kill -0 "$pid" 2>/dev/null; then wait "$pid" 2>/dev/null; echo "${label}_stop=TERM after_s=$((i / 10)).$((i % 10)) rc=$?"; return; fi
    sleep 0.1
  done
  kill -KILL "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  echo "${label}_stop=KILL after_s=$STOP_GRACE_S"
}
python3 "$E0_TOOLS/e0_make_params.py" "$PARAMS_SRC" "$E0_OUT/params.yml" "$TOPICS" || { echo "PARAMS=FAIL"; exit 32; }
echo "params_sha256=$(sha256sum "$E0_OUT/params.yml" | cut -d' ' -f1)"
echo "=== warm-up ==="
"$NODE_EXE" --ros-args --params-file "$E0_OUT/params.yml" -r __node:=point_cloud_fusion > "$E0_OUT/warmup_node.log" 2>&1 &
wpid=$!
# Stop rule: no measurement after a failed warm-up.
if wait_line "$E0_OUT/warmup_node.log" "Publishing to" 30 "$wpid" && sleep 1 && kill -0 "$wpid" 2>/dev/null; then
  echo "WARMUP=PASS (started and still alive 1 s later)"
  stop_pid "$wpid" warmup_node
else
  echo "WARMUP=FAIL"; tail -n 20 "$E0_OUT/warmup_node.log"
  stop_pid "$wpid" warmup_node
  exit 36
fi
lpid=""; ppid=""
if [ "$E0_MODE" = active ]; then
  rm -f /tmp/e0-log.ready /tmp/e0-pub.ready
  timeout -k 2 $((E0_DURATION_S + 120)) python3 "$E0_TOOLS/e0_output_logger.py" --topic "$OUT_TOPIC" \
    --out-csv "$E0_OUT/out.csv" --ready-file /tmp/e0-log.ready > "$E0_OUT/logger.log" 2>&1 &
  lpid=$!
  wait_file /tmp/e0-log.ready 30 "$lpid" || { echo "LOGGER_READY=FAIL"; exit 33; }
  timeout -k 2 $((E0_DURATION_S + 120)) python3 "$E0_TOOLS/e0_publisher.py" --pcd-dir "$E0_PCDS" --topics "$TOPICS" \
    --end-s $((E0_DURATION_S + 60)) --ready-file /tmp/e0-pub.ready --out-prefix "$E0_OUT/pub" > "$E0_OUT/publisher.log" 2>&1 &
  ppid=$!
  wait_file /tmp/e0-pub.ready 30 "$ppid" || { echo "PUBLISHER_READY=FAIL"; stop_pid "$lpid" logger; exit 34; }
fi
"$NODE_EXE" --ros-args --params-file "$E0_OUT/params.yml" -r __node:=point_cloud_fusion > "$E0_OUT/node.log" 2>&1 &
npid=$!
if ! wait_line "$E0_OUT/node.log" "Publishing to" 30 "$npid"; then
  echo "NODE_READY=FAIL"; tail -n 20 "$E0_OUT/node.log"
  [ -n "$ppid" ] && stop_pid "$ppid" publisher; [ -n "$lpid" ] && stop_pid "$lpid" logger; exit 35
fi
sleep "$SETTLE_S"
python3 "$E0_TOOLS/e0_proc_sample.py" "$npid" > "$E0_OUT/sample_t0.json"
sleep "$E0_DURATION_S"
python3 "$E0_TOOLS/e0_proc_sample.py" "$npid" > "$E0_OUT/sample_t1.json"
stop_pid "$npid" node
[ -n "$ppid" ] && stop_pid "$ppid" publisher
[ -n "$lpid" ] && stop_pid "$lpid" logger
python3 - "$E0_OUT" "$E0_LABEL" "$E0_MODE" <<'PY'
import csv, json, os, sys
d, label, mode = sys.argv[1:4]
a = json.load(open(f"{d}/sample_t0.json")); b = json.load(open(f"{d}/sample_t1.json"))
outputs = None
if os.path.exists(f"{d}/out.csv"):
    with open(f"{d}/out.csv") as h:  # monotonic window: immune to wall-clock steps
        outputs = sum(1 for r in csv.DictReader(h) if a["mono_ns"] <= int(r["recv_mono_ns"]) < b["mono_ns"])
log = open(f"{d}/node.log", errors="replace").read().splitlines()
res = {"label": label, "mode": mode, "duration_s": (b["mono_ns"] - a["mono_ns"]) / 1e9,
       "clock_step_ms": ((b["wall_ns"] - a["wall_ns"]) - (b["mono_ns"] - a["mono_ns"])) / 1e6,
       "cpu_sched_ns": b["sched_ns"] - a["sched_ns"], "cpu_ticks": b["ticks"] - a["ticks"], "clk_tck": b["clk_tck"],
       "vmhwm_kb": b["vmhwm_kb"], "threads": b["threads"], "outputs": outputs,
       "warn": sum(1 for x in log if x.startswith("[WARN]")), "error": sum(1 for x in log if x.startswith("[ERROR]"))}
json.dump(res, open(f"{d}/qc_result.json", "w"), indent=2)
print("QC_RESULT " + " ".join(f"{k}={v}" for k, v in res.items()))
PY
