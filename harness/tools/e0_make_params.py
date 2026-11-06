#!/usr/bin/env python3
"""E0 params generator, shared by e0_qb_inner.sh, e0_qc_inner.sh and e0_paramcheck_inner.sh.

Usage: e0_make_params.py <src params.yml> <dst params.yml> <comma-separated topics> [--keep-empty]

Starts from the package's own params.yml and changes only:
  input_topics, input_transport_hints (all "raw"), use_cuda (False);
  the two empty lists below are dropped, unless --keep-empty (paramcheck variant A).

Why the drop: rcl (jazzy) adds no value for an empty YAML sequence, and rclcpp then aborts while
resolving overrides ("No parameter value set"). Without the key, the node keeps its code default, an
empty vector, and logs "Missing parameter '<name>', using default value: []".

Guard: only the two keys PR #9 put in config/params.yml are expected to be empty. Any other empty
list stops the generator (exit 2) instead of being dropped silently.
Exit codes: 0 ok, 2 unexpected empty list, 1 any other error.
"""
import sys

import yaml

EXPECTED_EMPTY = ("feature_calibration.leading_inputs", "feature_calibration.follower_inputs")


def empty_lists(node, prefix=""):
    found = []
    for key, value in node.items():
        if isinstance(value, dict):
            found += empty_lists(value, f"{prefix}{key}.")
        elif isinstance(value, list) and not value:
            found.append(f"{prefix}{key}")
    return found


def main():
    args = [a for a in sys.argv[1:] if a != "--keep-empty"]
    keep_empty = "--keep-empty" in sys.argv[1:]
    src, dst, topics = args[0], args[1], args[2].split(",")
    with open(src) as handle:
        doc = yaml.safe_load(handle)
    (root,) = doc.keys()
    params = doc[root]["ros__parameters"]
    params["input_topics"] = topics
    params["input_transport_hints"] = ["raw"] * len(topics)
    params["use_cuda"] = False

    found = empty_lists(params)
    unexpected = [name for name in found if name not in EXPECTED_EMPTY]
    print(f"params_empty_lists_found={','.join(found) or 'none'}")
    if unexpected:
        print(f"PARAMS_UNEXPECTED_EMPTY={','.join(unexpected)} (only {','.join(EXPECTED_EMPTY)} may be dropped)")
        return 2
    if not keep_empty:
        for name in found:
            *path, leaf = name.split(".")
            node = params
            for part in path:
                node = node[part]
            del node[leaf]
    with open(dst, "w") as handle:
        yaml.safe_dump(doc, handle, sort_keys=False)
    print(f"params_root={root} input_timeout={params.get('degraded_mode', {}).get('input_timeout')} "
          f"max_time_diff_sec={params.get('max_time_diff_sec')} use_cuda={params['use_cuda']}")
    print(f"params_dropped_empty_lists={'none (kept on purpose)' if keep_empty else (','.join(found) or 'none')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
