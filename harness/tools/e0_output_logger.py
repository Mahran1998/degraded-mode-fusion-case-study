#!/usr/bin/env python3
"""E0 output logger: one CSV row per received PointCloud2 (receive times, header stamp, size).

The subscription is best effort (sensor-data QoS), which matches both reliable and
best-effort publishers. It stops cleanly on SIGINT or SIGTERM, or after --duration-s.
"""
import argparse
import csv
import signal
import sys
import time
from pathlib import Path

import rclpy
from rclpy.qos import qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import PointCloud2

STOP = {"flag": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--ready-file", default="", help="touched once the subscription exists")
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 = until a signal")
    args = parser.parse_args()

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.update(flag=True))
    node = rclpy.create_node("e0_output_logger")
    out = open(args.out_csv, "w", newline="")
    writer = csv.writer(out)
    writer.writerow(["recv_wall_ns", "recv_mono_ns", "stamp_ns", "points", "frame_id"])
    count = {"n": 0}

    def on_cloud(msg):
        wall, mono = time.time_ns(), time.monotonic_ns()
        stamp = msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec
        writer.writerow([wall, mono, stamp, msg.width * msg.height, msg.header.frame_id])
        count["n"] += 1

    node.create_subscription(PointCloud2, args.topic, on_cloud, qos_profile_sensor_data)
    if args.ready_file:
        Path(args.ready_file).touch()
    deadline = time.monotonic() + args.duration_s if args.duration_s > 0 else None
    while not STOP["flag"] and (deadline is None or time.monotonic() < deadline):
        rclpy.spin_once(node, timeout_sec=0.1)
    out.close()
    print(f"LOGGER_RECEIVED={count['n']} SIGNAL={STOP['flag']}", flush=True)
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
