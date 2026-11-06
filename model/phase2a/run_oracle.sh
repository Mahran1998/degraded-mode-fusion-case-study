#!/usr/bin/env bash
# Build and run the ApproximateTime C++ oracle inside a throwaway ROS 2 Jazzy container.
#
#   bash run_oracle.sh [traces_file] [log_file]        (paths relative to this folder)
#
# - Compiles oracle/oracle.cpp against ./mf-4.11.12 (the headers extracted from
#   ghcr.io/openads-project/point_cloud_fusion:latest-slim), which take precedence
#   over the base image's own message_filters headers.
# - Nothing is installed on the host; the container is removed afterwards (--rm).
# - Override the image with ORACLE_IMG=<image> if the default is unsuitable.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${ORACLE_IMG:-rwthika/ros2:jazzy-ros-base}"
TRACES="${1:-out/traces.txt}"
LOG="${2:-out/oracle_log.txt}"

[ -f "$HERE/$TRACES" ] || { echo "ERR: missing $HERE/$TRACES (run gen_traces.py first)"; exit 2; }
[ -f "$HERE/mf-4.11.12/message_filters/sync_policies/approximate_time.h" ] || { echo "ERR: missing $HERE/mf-4.11.12 headers"; exit 2; }
echo "INFO: image=$IMG traces=$TRACES log=$LOG"

docker run --rm -v "$HERE":/work -w /work \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -e TRACES="$TRACES" -e LOG="$LOG" \
  --entrypoint bash "$IMG" -c '
set -u
source /opt/ros/jazzy/setup.bash || { echo "ERR: /opt/ros/jazzy/setup.bash not found"; exit 10; }
if ! command -v g++ >/dev/null || ! command -v cmake >/dev/null || ! command -v make >/dev/null; then
  echo "INFO: installing g++/cmake/make inside the throwaway container"
  apt-get update -qq && apt-get install -y -qq --no-install-recommends g++ cmake make >/dev/null \
    || { echo "ERR: apt install failed"; exit 11; }
fi
MFV=$(sed -n "s:.*<version>\(.*\)</version>.*:\1:p" /opt/ros/jazzy/share/message_filters/package.xml | head -1)
echo "INFO: base image message_filters (linked library) version: ${MFV:-unknown}"
cmake -S oracle -B /tmp/build -DMF_HEADERS=/work/mf-4.11.12 > /tmp/cmake.log 2>&1 || { cat /tmp/cmake.log; exit 12; }
grep "Oracle uses" /tmp/cmake.log
cmake --build /tmp/build -j"$(nproc)" > /tmp/build.log 2>&1 || { tail -40 /tmp/build.log; exit 13; }
DEP=$(grep -rhoE "[^ ]*sync_policies/approximate_time\.h" /tmp/build/CMakeFiles/at_oracle.dir/ 2>/dev/null | sort -u | tr "\n" " ")
echo "INFO: approximate_time.h compiled from: ${DEP:-unknown}"
/tmp/build/at_oracle < "$TRACES" > "$LOG" 2> out/oracle_stderr.txt
rc=$?
echo "INFO: oracle rc=$rc ($(tail -1 out/oracle_stderr.txt))"
chown "$HOST_UID:$HOST_GID" "$LOG" out/oracle_stderr.txt
exit $rc
'
rc=$?
echo "run_oracle rc=$rc"
exit $rc
