# E0 tool test v1.1, inner part (runs inside the dev container).
# Expects: E0_TOOLS, E0_PCDS, E0_OUT, E0_TIMELINE_T1, E0_T1_TOPICS, E0_T2_TOPIC.
# T1 and T2 are independent: both always run, and the exit code is 0 only if both pass.
set -u
export PYTHONUNBUFFERED=1 RCUTILS_COLORIZED_OUTPUT=0
wait_file() {  # $1=file $2=timeout_s $3=pid
  local i
  for i in $(seq 1 $(($2 * 10))); do
    [ -e "$1" ] && return 0
    kill -0 "$3" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}
t1=FAIL; t2=FAIL
echo "=== T1: publisher alone, 4 inputs, timeline ==="
rm -f /tmp/e0-go /tmp/e0-pub.ready
timeout -k 2 60 python3 "$E0_TOOLS/e0_publisher.py" --pcd-dir "$E0_PCDS" \
  --topics "$E0_T1_TOPICS" --initially-active 1,2,3 \
  --go-file /tmp/e0-go --timeline "$E0_TIMELINE_T1" --end-s 10 \
  --ready-file /tmp/e0-pub.ready --out-prefix "$E0_OUT/t1" > "$E0_OUT/t1_pub.log" 2>&1 &
pub=$!
if wait_file /tmp/e0-pub.ready 30 "$pub"; then
  echo "T1_READY=PASS"
  sleep 1
  touch /tmp/e0-go
  wait "$pub"; echo "t1_publisher_rc=$?"
  cat "$E0_OUT/t1_pub.log"
  python3 "$E0_TOOLS/e0_check_tools.py" publisher "$E0_OUT/t1" && t1=PASS
else
  echo "T1_READY=FAIL"
  kill -TERM "$pub" 2>/dev/null; wait "$pub" 2>/dev/null
  cat "$E0_OUT/t1_pub.log"
fi
echo "=== T2: output logger fed by a 1-input publisher ==="
rm -f /tmp/e0-log.ready
timeout -k 2 60 python3 "$E0_TOOLS/e0_output_logger.py" --topic "$E0_T2_TOPIC" \
  --out-csv "$E0_OUT/t2_logger.csv" --ready-file /tmp/e0-log.ready > "$E0_OUT/t2_logger.log" 2>&1 &
log=$!
if wait_file /tmp/e0-log.ready 30 "$log"; then
  echo "T2_LOGGER_READY=PASS"
  timeout -k 2 60 python3 "$E0_TOOLS/e0_publisher.py" --pcd-dir "$E0_PCDS" --topics "$E0_T2_TOPIC" --offsets-ms 5 \
    --lead-s 2 --end-s 5 --out-prefix "$E0_OUT/t2" > "$E0_OUT/t2_pub.log" 2>&1
  echo "t2_publisher_rc=$?"
  sleep 1
  kill -TERM "$log"; wait "$log"; echo "t2_logger_rc=$?"
  cat "$E0_OUT/t2_pub.log" "$E0_OUT/t2_logger.log"
  python3 "$E0_TOOLS/e0_check_tools.py" logger "$E0_OUT/t2" "$E0_OUT/t2_logger.csv" && t2=PASS
else
  echo "T2_LOGGER_READY=FAIL"
  kill -TERM "$log" 2>/dev/null; wait "$log" 2>/dev/null
  cat "$E0_OUT/t2_logger.log"
fi
echo "TOOLTEST T1=$t1 T2=$t2"
[ "$t1" = PASS ] && [ "$t2" = PASS ]
