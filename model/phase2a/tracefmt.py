"""Shared text format for traces (input) and callback logs (output).

Trace file (read by the C++ oracle and by the Python reference):

    TRACE <name>
    PARAM n <2..9>
    PARAM queue <uint>
    PARAM max_interval_ns <int64> | default
    PARAM age_penalty <double, repr()>
    PARAM lower_bounds_ns <int64> x n
    EV <event_id> <input_index> <stamp_ns>
    ...
    END
    PROBE_SCALE <double> <ns> <ns> ...          (optional, any number of lines)

Output log (written by the oracle; produced in memory by the Python model):

    TRACE <name>
    CB <trigger_event_id> <event_id_0> ... <event_id_n-1>
    END <name> <callback_count>
    SCALE <double> <ns> <result_ns>
"""


def parse_traces(path):
    traces, probes = [], []
    cur = None
    with open(path) as f:
        for raw in f:
            parts = raw.split()
            if not parts or parts[0].startswith("#"):
                continue
            tag = parts[0]
            if tag == "TRACE":
                cur = {"name": parts[1], "params": {}, "events": []}
            elif tag == "PARAM":
                key, vals = parts[1], parts[2:]
                if key in ("n", "queue"):
                    cur["params"][key] = int(vals[0])
                elif key == "max_interval_ns":
                    cur["params"][key] = None if vals[0] == "default" else int(vals[0])
                elif key == "age_penalty":
                    cur["params"][key] = float(vals[0])
                elif key == "lower_bounds_ns":
                    cur["params"][key] = [int(v) for v in vals]
                else:
                    raise ValueError(f"unknown PARAM {key}")
            elif tag == "EV":
                cur["events"].append((int(parts[1]), int(parts[2]), int(parts[3])))
            elif tag == "END":
                traces.append(cur)
                cur = None
            elif tag == "PROBE_SCALE":
                probes.append((float(parts[1]), [int(v) for v in parts[2:]]))
            else:
                raise ValueError(f"unknown line: {raw!r}")
    return traces, probes


def write_trace(f, trace):
    p = trace["params"]
    f.write(f"TRACE {trace['name']}\n")
    f.write(f"PARAM n {p['n']}\n")
    f.write(f"PARAM queue {p['queue']}\n")
    mi = "default" if p["max_interval_ns"] is None else str(p["max_interval_ns"])
    f.write(f"PARAM max_interval_ns {mi}\n")
    f.write(f"PARAM age_penalty {p['age_penalty']!r}\n")
    lbs = p.get("lower_bounds_ns") or [0] * p["n"]
    f.write("PARAM lower_bounds_ns " + " ".join(str(v) for v in lbs) + "\n")
    for eid, inp, stamp in trace["events"]:
        f.write(f"EV {eid} {inp} {stamp}\n")
    f.write("END\n")


def parse_log(path):
    """Returns (logs: name -> dict(callbacks=[(trigger, ids)], count=int), scales: [(scale, ns, result)])."""
    logs, scales = {}, []
    cur_name, cur = None, None
    with open(path) as f:
        for raw in f:
            parts = raw.split()
            if not parts:
                continue
            tag = parts[0]
            if tag == "TRACE":
                cur_name, cur = parts[1], []
            elif tag == "CB":
                cur.append((int(parts[1]), tuple(int(v) for v in parts[2:])))
            elif tag == "END":
                assert parts[1] == cur_name, f"END mismatch {parts[1]} != {cur_name}"
                logs[cur_name] = {"callbacks": cur, "count": int(parts[2])}
                cur_name, cur = None, None
            elif tag == "SCALE":
                scales.append((float(parts[1]), int(parts[2]), int(parts[3])))
            # other lines (e.g. INFO) are ignored
    return logs, scales
