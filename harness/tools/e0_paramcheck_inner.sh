# E0 parameter check, inner part (runs inside the dev container, one container per build).
# Question: does the node abort on the shipped params.yml for this build, and does it start and keep
# running once the two PR #9 empty lists are dropped? Both variants come from e0_make_params.py, the
# same generator Q-B and Q-C use:
#   A_with_empty     = --keep-empty  (shipped empty lists kept)
#   B_without_empty  = default       (exactly the file Q-B / Q-C will use; compare params_sha256)
# Verdict per variant needs the log line AND the exit code:
#   CRASH_EMPTY_LIST = "No parameter value set" and rc 134 (SIGABRT from std::terminate)
#   STARTED          = "Publishing to" and rc 124 (still running when the RUN_S timeout stopped it)
#   OTHER            = anything else (e.g. started, then died before the timeout)
# Mounts: /out = the build (read-only), E0_TOOLS (read-only), E0_CHK_OUT (writable). Env: E0_LABEL.
# Optional overrides (local tests only): E0_SETUP, E0_NODE_EXE, E0_PARAMS_SRC.
# Exit code: 0 when A = CRASH_EMPTY_LIST and B = STARTED; 1 otherwise; 31/32 setup/params.
set -u
export RCUTILS_COLORIZED_OUTPUT=0 RCUTILS_LOGGING_USE_STDOUT=1 RCUTILS_LOGGING_BUFFERED_STREAM=0
E0_TOOLS="${E0_TOOLS:-/e0/tools}"
SETUP="${E0_SETUP:-/out/install/setup.bash}"
NODE_EXE="${E0_NODE_EXE:-/out/install/point_cloud_fusion/lib/point_cloud_fusion/point_cloud_fusion}"
PARAMS_SRC="${E0_PARAMS_SRC:-/out/install/point_cloud_fusion/share/point_cloud_fusion/config/params.yml}"
TOPICS="/e0/in/input_0,/e0/in/input_1,/e0/in/input_2,/e0/in/input_3"
RUN_S=6
RC_ABORT=134
RC_TIMEOUT=124
set +u
# shellcheck disable=SC1090
source "$SETUP" || { echo "SETUP=FAIL ($SETUP)"; exit 31; }
set -u

echo "=== $E0_LABEL: params source sha256=$(sha256sum "$PARAMS_SRC" | cut -d' ' -f1) ==="
echo "--- variant A (empty lists kept)"
python3 "$E0_TOOLS/e0_make_params.py" "$PARAMS_SRC" "$E0_CHK_OUT/A_with_empty.yml" "$TOPICS" --keep-empty || { echo "PARAMS=FAIL"; exit 32; }
echo "--- variant B (empty lists dropped, as in Q-B / Q-C)"
python3 "$E0_TOOLS/e0_make_params.py" "$PARAMS_SRC" "$E0_CHK_OUT/B_without_empty.yml" "$TOPICS" || { echo "PARAMS=FAIL"; exit 32; }
echo "B_params_sha256=$(sha256sum "$E0_CHK_OUT/B_without_empty.yml" | cut -d' ' -f1)"

declare -A result
for variant in A_with_empty B_without_empty; do
  log="$E0_CHK_OUT/$variant.log"
  timeout -k 2 "$RUN_S" "$NODE_EXE" --ros-args --params-file "$E0_CHK_OUT/$variant.yml" -r __node:=point_cloud_fusion > "$log" 2>&1
  rc=$?
  if grep -q "No parameter value set" "$log" && [ "$rc" -eq "$RC_ABORT" ]; then r=CRASH_EMPTY_LIST
  elif grep -q "Publishing to" "$log" && [ "$rc" -eq "$RC_TIMEOUT" ]; then r=STARTED
  else r=OTHER; fi
  result[$variant]=$r
  echo "variant=$variant result=$r rc=$rc log_lines=$(wc -l < "$log")"
  grep -E "what\(\)|Missing parameter 'feature_calibration\.(leading|follower)_inputs'" "$log" | head -n 4 | sed 's/^/  /'
  [ "$r" = OTHER ] && tail -n 8 "$log" | sed 's/^/  tail: /'
done
if [ "${result[A_with_empty]}" = CRASH_EMPTY_LIST ] && [ "${result[B_without_empty]}" = STARTED ]; then
  echo "PARAMCHECK_$E0_LABEL=EXPECTED (A aborts on the empty list, B runs until the ${RUN_S}s timeout)"
  exit 0
fi
echo "PARAMCHECK_$E0_LABEL=UNEXPECTED (A=${result[A_with_empty]} B=${result[B_without_empty]})"
exit 1
