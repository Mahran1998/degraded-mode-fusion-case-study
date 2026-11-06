#!/usr/bin/env python3
"""E0 publisher: one PointCloud2 stream per input, with common frame stamps and a stop/start timeline.

Every input publishes frame k with header.stamp = F_k (the common frame time) at
F_k + offset_i + jitter_i_k, so the stamp spread is 0 while the arrival phases differ.
Before the go file appears, only the initially active inputs publish. Timeline events and
the end time are relative to the moment the go file is first seen.
Outputs: a ground-truth CSV (one row per published message), an events CSV and a meta JSON.
"""
import argparse
import csv
import heapq
import json
import os
import random
import signal
import sys
import time
from pathlib import Path

import rclpy
from builtin_interfaces.msg import Time
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import PointCloud2, PointField
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header

PCD_TYPES = {
    ("F", 4): PointField.FLOAT32, ("F", 8): PointField.FLOAT64,
    ("I", 1): PointField.INT8, ("I", 2): PointField.INT16, ("I", 4): PointField.INT32,
    ("U", 1): PointField.UINT8, ("U", 2): PointField.UINT16, ("U", 4): PointField.UINT32,
}
SENSOR_SUFFIXES = ("fl", "fr", "rl", "rr")
STOP = {"flag": False}


def load_pcd(path, frame_id):
    """Load one ASCII or binary PCD file into a PointCloud2 (stamp is set at publish time)."""
    with open(path, "rb") as handle:
        meta = {}
        while True:
            raw = handle.readline()
            if not raw:
                raise ValueError(f"{path}: no DATA line")
            line = raw.decode("ascii").strip()
            if not line or line.startswith("#"):
                continue
            key, *values = line.split()
            if key.upper() == "DATA":
                mode = values[0].lower()
                break
            meta[key.upper()] = values
        names = [name.lower() for name in meta["FIELDS"]]
        sizes = [int(v) for v in meta["SIZE"]]
        types = [v.upper() for v in meta["TYPE"]]
        counts = [int(v) for v in meta.get("COUNT", ["1"] * len(names))]
        points = int(meta["POINTS"][0])
        fields, offset = [], 0
        for name, size, kind, count in zip(names, sizes, types, counts):
            fields.append(PointField(name=name, offset=offset, datatype=PCD_TYPES[(kind, size)], count=count))
            offset += size * count
        header = Header(frame_id=frame_id)
        if mode == "ascii":
            rows = []
            for raw in handle:
                values = raw.split()
                if not values:
                    continue
                row, cursor = [], 0
                for kind, count in zip(types, counts):
                    convert = float if kind == "F" else int
                    for _ in range(count):
                        row.append(convert(values[cursor]))
                        cursor += 1
                rows.append(tuple(row))
            if len(rows) != points:
                raise ValueError(f"{path}: POINTS={points} but {len(rows)} rows")
            return point_cloud2.create_cloud(header, fields, rows)
        if mode == "binary":
            data = handle.read(points * offset)
            if len(data) != points * offset:
                raise ValueError(f"{path}: binary payload is {len(data)} bytes, expected {points * offset}")
            return PointCloud2(header=header, height=1, width=points, fields=fields, is_bigendian=False,
                               point_step=offset, row_step=points * offset, data=data, is_dense=False)
        raise ValueError(f"{path}: unsupported DATA mode {mode}")


def parse_timeline(text, inputs):
    """Parse 't:action:i[,j...];...' into a sorted list of (t_s, action, input)."""
    events = []
    for item in filter(None, (part.strip() for part in text.split(";"))):
        t_text, action, targets = item.split(":")
        if action not in ("start", "stop"):
            raise ValueError(f"unknown action {action!r}")
        for target in targets.split(","):
            index = int(target)
            if not 0 <= index < inputs:
                raise ValueError(f"input {index} out of range")
            events.append((float(t_text), action, index))
    return sorted(events, key=lambda e: e[0])


def to_time_msg(ns):
    return Time(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcd-dir", required=True)
    parser.add_argument("--topics", required=True, help="comma-separated, one per input")
    parser.add_argument("--frame-id", default="base_link")
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--offsets-ms", default="5,15,25,35")
    parser.add_argument("--jitter-ms", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--initially-active", default="all", help="'all', 'none' or comma-separated inputs")
    parser.add_argument("--go-file", default="", help="timeline starts when this file appears; empty = at start")
    parser.add_argument("--timeline", default="", help="'t:start|stop:i[,j];...' in seconds after go")
    parser.add_argument("--end-s", type=float, required=True, help="seconds after go")
    parser.add_argument("--lead-s", type=float, default=0.5, help="delay before frame 0")
    parser.add_argument("--ready-file", default="", help="touched after the first publish")
    parser.add_argument("--out-prefix", required=True, help="writes <prefix>_gt.csv, _events.csv, _meta.json")
    args = parser.parse_args()

    topics = [t for t in args.topics.split(",") if t]
    inputs = len(topics)
    offsets_ns = [int(float(v) * 1e6) for v in args.offsets_ms.split(",")]
    if len(offsets_ns) != inputs:
        raise ValueError("--offsets-ms needs one value per topic")
    period_ns = int(round(1e9 / args.rate_hz))
    jitter_ns = int(args.jitter_ms * 1e6)
    events = parse_timeline(args.timeline, inputs)
    if args.initially_active == "all":
        active = set(range(inputs))
    elif args.initially_active == "none":
        active = set()
    else:
        active = {int(v) for v in args.initially_active.split(",")}

    pcd_dir = Path(args.pcd_dir)
    clouds = []
    for index in range(inputs):
        suffix = SENSOR_SUFFIXES[index % len(SENSOR_SUFFIXES)]
        files = sorted(pcd_dir.glob(f"*_{suffix}.pcd"), key=lambda p: int(p.stem.rsplit("_", 1)[0]))
        if not files:
            raise ValueError(f"no *_{suffix}.pcd in {pcd_dir}")
        clouds.append([load_pcd(f, args.frame_id) for f in files])

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.update(flag=True))
    node = rclpy.create_node("e0_publisher")
    qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)
    publishers = [node.create_publisher(PointCloud2, topic, qos) for topic in topics]

    rngs = [random.Random(args.seed * 1000 + i) for i in range(inputs)]
    wall0, mono0 = time.time_ns(), time.monotonic_ns()
    base_ns = ((wall0 + int(args.lead_s * 1e9)) // period_ns + 1) * period_ns
    heap = []

    def push(i, k):
        planned = base_ns + k * period_ns + offsets_ns[i] + int(rngs[i].uniform(-jitter_ns, jitter_ns))
        heapq.heappush(heap, (planned, i, k))

    for i in range(inputs):
        push(i, 0)

    out = Path(args.out_prefix)
    gt_file = open(f"{out}_gt.csv", "w", newline="")
    gt = csv.writer(gt_file)
    gt.writerow(["input", "seq", "planned_mono_ns", "actual_mono_ns", "actual_wall_ns", "stamp_ns"])
    ev_file = open(f"{out}_events.csv", "w", newline="")
    ev = csv.writer(ev_file)
    ev.writerow(["t_rel_s", "action", "input", "abs_wall_ns"])
    go_ns = None if args.go_file else base_ns
    pending = list(events)
    published = [0] * inputs
    skipped = [0] * inputs
    late_ns = []
    first_done = False

    while heap and not STOP["flag"]:
        planned, i, k = heapq.heappop(heap)
        delay = (mono0 + (planned - wall0) - time.monotonic_ns()) / 1e9
        if delay > 0:
            time.sleep(delay)
        if go_ns is None and os.path.exists(args.go_file):
            go_ns = time.time_ns()
        if go_ns is not None:
            if planned > go_ns + int(args.end_s * 1e9):
                break
            while pending and planned >= go_ns + int(pending[0][0] * 1e9):
                t_rel, action, target = pending.pop(0)
                (active.add if action == "start" else active.discard)(target)
                ev.writerow([t_rel, action, target, go_ns + int(t_rel * 1e9)])
        if i in active:
            cloud = clouds[i][k % len(clouds[i])]
            stamp_ns = base_ns + k * period_ns
            cloud.header.stamp = to_time_msg(stamp_ns)
            actual_mono, actual_wall = time.monotonic_ns(), time.time_ns()
            publishers[i].publish(cloud)
            gt.writerow([i, k, mono0 + (planned - wall0), actual_mono, actual_wall, stamp_ns])
            published[i] += 1
            late_ns.append(actual_mono - (mono0 + (planned - wall0)))
            if not first_done:
                first_done = True
                if args.ready_file:
                    Path(args.ready_file).touch()
        else:
            skipped[i] += 1
        push(i, k + 1)

    gt_file.close()
    ev_file.close()
    late_ns.sort()

    def quantile(q):
        return late_ns[min(len(late_ns) - 1, int(q * (len(late_ns) - 1) + 0.5))] / 1e6 if late_ns else float("nan")

    meta = {
        "topics": topics, "rate_hz": args.rate_hz, "offsets_ms": args.offsets_ms, "jitter_ms": args.jitter_ms,
        "seed": args.seed, "base_wall_ns": base_ns, "go_wall_ns": go_ns, "end_s": args.end_s,
        "timeline": args.timeline, "initially_active": args.initially_active, "published": published,
        "skipped": skipped, "stopped_by_signal": STOP["flag"],
        "lateness_ms": {"p50": quantile(0.5), "p99": quantile(0.99), "max": quantile(1.0)},
    }
    Path(f"{out}_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"PUBLISHED={published} SKIPPED={skipped} GO={'seen' if go_ns else 'never'} "
          f"LATENESS_MS p50={meta['lateness_ms']['p50']:.3f} p99={meta['lateness_ms']['p99']:.3f} "
          f"max={meta['lateness_ms']['max']:.3f} SIGNAL={STOP['flag']}", flush=True)
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
