# E0 Q-B runner, inner part (runs inside the dev container; design v2.2 §9.3).
# Mounts: /out = the build under test (read-only), E0_TOOLS (read-only), E0_PCDS (read-only), E0_OUT (writable).
# Env: E0_TOOLS, E0_PCDS, E0_OUT, E0_RUNS (e.g. "0" or "1 2"), E0_T_S (0.3), E0_END_S (44).
# Optional overrides (used only by local tests): E0_SETUP, E0_NODE_EXE, E0_PARAMS_SRC.
# Exit codes: 31 setup, 32 params, 33 warm-up (stop rule: no measured run after a failed warm-up).
# QB_OVERALL: PASS, FAIL, or INVALID (a run had a wall-clock step; see QB_CLOCK in the analyzer).
set -u
export PYTHONUNBUFFERED=1 RCUTILS_COLORIZED_OUTPUT=0 RCUTILS_LOGGING_BUFFERED_STREAM=0 RCUTILS_LOGGING_USE_STDOUT=1
export RCUTILS_CONSOLE_OUTPUT_FORMAT='[{severity}] [{time_as_nanoseconds}] [{name}]: {message}'
SETUP="${E0_SETUP:-/out/install/setup.bash}"
NODE_EXE="${E0_NODE_EXE:-/out/install/point_cloud_fusion/lib/point_cloud_fusion/point_cloud_fusion}"
PARAMS_SRC="${E0_PARAMS_SRC:-/out/install/point_cloud_fusion/share/point_cloud_fusion/config/params.yml}"
TOPICS="/e0/in/input_0,/e0/in/input_1,/e0/in/input_2,/e0/in/input_3"
OUT_TOPIC="/point_cloud_fusion/point_cloud"
STARTUP_GUARD_S=30
SETTLE_S=2
STOP_GRACE_S=5
# colcon/ament setup scripts read optional variables (COLCON_TRACE, AMENT_TRACE_SETUP_FILES, ...)
# and are not nounset-safe, so nounset is off only while sourcing them.
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
stop_pid() {  # $1=pid $2=label: TERM, wait up to STOP_GRACE_S, then KILL
  local pid="$1" label="$2" i
  kill -TERM "$pid" 2>/dev/null || { echo "${label}_stop=already_exited"; wait "$pid" 2>/dev/null; return; }
  for i in $(seq 1 $((STOP_GRACE_S * 10))); do
    if ! kill -0 "$pid" 2>/dev/null; then wait "$pid" 2>/dev/null; echo "${label}_stop=TERM after_s=$((i / 10)).$((i % 10)) rc=$?"; return; fi
    sleep 0.1
  done
  kill -KILL "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  echo "${label}_stop=KILL after_s=$STOP_GRACE_S"
}
startup_done() {  # $1=log $2=k: k went UNKNOWN->STALE and every other input reached HEALTHY
  local log="$1" k="$2" i
  grep -qE "Input availability: input $k \(.*\) UNKNOWN -> STALE" "$log" || return 1
  for i in 0 1 2 3; do
    [ "$i" = "$k" ] && continue
    grep -qE "Input availability: input $i \(.*\) [A-Z]+ -> HEALTHY" "$log" || return 1
  done
  return 0
}

echo "=== params (package defaults; only topics, transport hints and use_cuda changed; the two PR #9 empty lists dropped) ==="
# Shared generator (also used by Q-C and the paramcheck): see e0_make_params.py for the why and the guard.
python3 "$E0_TOOLS/e0_make_params.py" "$PARAMS_SRC" "$E0_OUT/params.yml" "$TOPICS" || { echo "PARAMS=FAIL"; exit 32; }
echo "params_sha256=$(sha256sum "$E0_OUT/params.yml" | cut -d' ' -f1)"
echo "node_lib_sha256=$(sha256sum "$(dirname "$(dirname "$NODE_EXE")")/libpoint_cloud_fusion_component.so" 2>/dev/null | cut -d' ' -f1)"

echo "=== warm-up: one throw-away node start (not measured) ==="
"$NODE_EXE" --ros-args --params-file "$E0_OUT/params.yml" -r __node:=point_cloud_fusion > "$E0_OUT/warmup_node.log" 2>&1 &
wpid=$!
for i in $(seq 1 100); do grep -q "Publishing to" "$E0_OUT/warmup_node.log" && break; kill -0 "$wpid" 2>/dev/null || break; sleep 0.1; done
# Stop rule: a node that cannot start says nothing about the monitor, so no measured run follows.
if grep -q "Publishing to" "$E0_OUT/warmup_node.log" && sleep 1 && kill -0 "$wpid" 2>/dev/null; then
  echo "WARMUP=PASS (started and still alive 1 s later)"
  grep -E "Missing parameter '" "$E0_OUT/warmup_node.log" | sed -E 's/^\[[A-Z]+\] \[[0-9]+\] \[[^]]*\]: /warmup_default: /'
  stop_pid "$wpid" warmup_node
else
  echo "WARMUP=FAIL"
  tail -n 20 "$E0_OUT/warmup_node.log"
  stop_pid "$wpid" warmup_node
  echo "QB_OVERALL=FAIL reason=warmup runs=[$E0_RUNS] (no measured run started)"
  exit 33
fi

overall=PASS
for r in $E0_RUNS; do
  k=$r; j=$(( (r + 1) % 4 )); l0=$(( (r + 2) % 4 )); l1=$(( (r + 3) % 4 )); l2=$r
  dir="$E0_OUT/run$r"; mkdir -p "$dir"
  timeline="0:start:$k;20:stop:$j;23:start:$j;26:stop:$l0;26.5:stop:$l1;27:stop:$l2;30:start:$l0,$l1,$l2"
  active=$(for i in 0 1 2 3; do [ "$i" != "$k" ] && printf '%s,' "$i"; done); active=${active%,}
  echo "################ run $r: k=$k j=$j low=[$l0,$l1,$l2] ################"
  python3 -c "import json,sys; json.dump({'run': $r, 'k': $k, 'j': $j, 'low': [$l0, $l1, $l2], 'T_s': float('$E0_T_S'), 'end_s': float('$E0_END_S'), 'timeline': '$timeline'}, open('$dir/run_meta.json', 'w'))"
  rm -f /tmp/e0-go /tmp/e0-pub.ready /tmp/e0-log.ready
  timeout -k 2 $((E0_END_S + 120)) python3 "$E0_TOOLS/e0_output_logger.py" --topic "$OUT_TOPIC" \
    --out-csv "$dir/out.csv" --ready-file /tmp/e0-log.ready > "$dir/logger.log" 2>&1 &
  lpid=$!
  wait_file /tmp/e0-log.ready 30 "$lpid" || { echo "LOGGER_READY=FAIL"; cat "$dir/logger.log"; overall=FAIL; break; }
  timeout -k 2 $((E0_END_S + 120)) python3 "$E0_TOOLS/e0_publisher.py" --pcd-dir "$E0_PCDS" --topics "$TOPICS" \
    --initially-active "$active" --go-file /tmp/e0-go --timeline "$timeline" --end-s "$E0_END_S" \
    --ready-file /tmp/e0-pub.ready --out-prefix "$dir/pub" > "$dir/publisher.log" 2>&1 &
  ppid=$!
  wait_file /tmp/e0-pub.ready 30 "$ppid" || { echo "PUBLISHER_READY=FAIL"; cat "$dir/publisher.log"; stop_pid "$lpid" logger; overall=FAIL; break; }
  "$NODE_EXE" --ros-args --params-file "$E0_OUT/params.yml" -r __node:=point_cloud_fusion \
    --log-level point_cloud_fusion:=debug > "$dir/node.log" 2>&1 &
  npid=$!
  ok=0
  for i in $(seq 1 $((STARTUP_GUARD_S * 10))); do
    if startup_done "$dir/node.log" "$k"; then ok=1; break; fi
    kill -0 "$npid" 2>/dev/null || break
    sleep 0.1
  done
  if [ "$ok" -ne 1 ]; then
    echo "STARTUP=FAIL (k=$k STALE plus others HEALTHY not seen within ${STARTUP_GUARD_S}s)"
    grep -E "Input availability|ERROR|WARN" "$dir/node.log" | head -n 30
    stop_pid "$npid" node; stop_pid "$ppid" publisher; stop_pid "$lpid" logger; overall=FAIL; break
  fi
  echo "STARTUP=PASS"
  sleep "$SETTLE_S"
  touch /tmp/e0-go
  wait "$ppid"; echo "publisher_rc=$?"
  stop_pid "$npid" node
  stop_pid "$lpid" logger
  cat "$dir/publisher.log" "$dir/logger.log"
  echo "node_log_lines=$(wc -l < "$dir/node.log") warn=$(grep -c '^\[WARN\]' "$dir/node.log") error=$(grep -c '^\[ERROR\]' "$dir/node.log")"
  python3 "$E0_TOOLS/e0_analyze_qb.py" "$dir"
  arc=$?
  if [ "$arc" -eq 2 ]; then [ "$overall" = PASS ] && overall=INVALID
  elif [ "$arc" -ne 0 ]; then overall=FAIL; fi
done
echo "QB_OVERALL=$overall runs=[$E0_RUNS]"
[ "$overall" = PASS ]
