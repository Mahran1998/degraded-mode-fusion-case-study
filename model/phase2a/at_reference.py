"""
Python reference model of message_filters::sync_policies::ApproximateTime,
ported line by line from message_filters 4.11.12 (the header installed in
ghcr.io/openads-project/point_cloud_fusion:latest-slim).

This model is NOT the source of truth. The C++ oracle (oracle/oracle.cpp),
compiled against the exact 4.11.12 header, is. This model is only trusted
after diff_test.py reports an exact match with the oracle on the trace corpus.

Source line references (Lnnn) point to sync_policies/approximate_time.h @ 4.11.12.

Model conventions
- A message is a tuple (stamp_ns, event_id).
- rclcpp::Time and rclcpp::Duration are plain int64 nanoseconds (all stamps
  use the same ROS clock, so clock-type checks never trigger).
- rclcpp::Duration::operator*(double) is emulated as in rclcpp (Jazzy):
  static_cast<int64>((long double)ns * (long double)scale), i.e. one rounding to a
  64-bit significand (x87 extended precision on x86-64), then truncation toward
  zero. The oracle's SCALE probe verifies this assumption directly.
"""

from collections import Counter, deque
from fractions import Fraction

NO_PIVOT = 9                                            # L840
MAX_DURATION_NS = (2**31 - 1) * 10**9 + 999_999_999     # L121: Duration(INT32_MAX, 999999999)


# ---------------------------------------------------------------------------
# rclcpp::Duration * double
# ---------------------------------------------------------------------------

def _round_to_significand(x: Fraction, bits: int) -> Fraction:
    """Round x to the nearest value with a `bits`-bit significand (ties to even)."""
    if x == 0:
        return Fraction(0)
    sign = -1 if x < 0 else 1
    a = -x if x < 0 else x
    # exponent e such that 2**e <= a < 2**(e+1)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if Fraction(2) ** e > a:
        e -= 1
    elif Fraction(2) ** (e + 1) <= a:
        e += 1
    ulp = Fraction(2) ** (e - bits + 1)
    m = round(a / ulp)          # Fraction.__round__ -> round half to even
    return sign * m * ulp


def scale_duration(ns: int, scale: float, mode: str = "longdouble") -> int:
    """Emulate rclcpp::Duration(ns) * scale -> nanoseconds()."""
    if mode == "longdouble":
        prod = _round_to_significand(Fraction(ns) * Fraction(scale), 64)
    elif mode == "double":
        prod = _round_to_significand(Fraction(ns) * Fraction(scale), 53)
    elif mode == "exact":
        prod = Fraction(ns) * Fraction(scale)
    else:
        raise ValueError(mode)
    return int(prod)            # int(Fraction) truncates toward zero, like static_cast


# ---------------------------------------------------------------------------
# ApproximateTime policy
# ---------------------------------------------------------------------------

class ApproximateTimeRef:
    def __init__(self, n, queue_size, max_interval_ns=MAX_DURATION_NS, age_penalty=0.1,
                 lower_bounds_ns=None, scale_mode="longdouble", on_publish=None):
        assert 2 <= n <= 9
        assert queue_size > 0                                   # L127
        self.n = n                                              # RealTypeCount::value
        self.queue_size = queue_size
        self.max_interval = max_interval_ns
        assert age_penalty >= 0                                 # L263
        self.age_penalty = float(age_penalty)
        self.lb = [0] * 9                                       # L124
        if lower_bounds_ns:
            for i, v in enumerate(lower_bounds_ns):
                assert v >= 0                                   # L269
                self.lb[i] = int(v)
        self.scale_mode = scale_mode
        self.deques = [deque() for _ in range(9)]
        self.past = [[] for _ in range(9)]
        self.num_non_empty = 0
        self.pivot = NO_PIVOT
        self.candidate = None
        self.candidate_start = 0
        self.candidate_end = 0
        self.pivot_time = 0
        self.has_dropped = [False] * 9
        self.warned = [False] * 9
        self.on_publish = on_publish
        self.counters = Counter()

    # (end_time - x) * (1 + age_penalty_)
    def _scaled(self, d):
        return scale_duration(d, 1 + self.age_penalty, self.scale_mode)

    # L161-206 (warnings only; no effect on selection)
    def _check_inter_message_bound(self, i):
        if self.warned[i]:
            return
        dq, v = self.deques[i], self.past[i]
        assert dq
        msg_time = dq[-1][0]
        if len(dq) == 1:
            if not v:
                return
            prev = v[-1][0]
        else:
            prev = dq[-2][0]
        if msg_time < prev:
            self.counters["warn_out_of_order"] += 1
            self.warned[i] = True
        elif (msg_time - prev) < self.lb[i]:
            self.counters["warn_below_lower_bound"] += 1
            self.warned[i] = True

    # L209-258
    def add(self, i, msg):
        assert 0 <= i < self.n
        dq = self.deques[i]
        dq.append(msg)
        if len(dq) == 1:
            self.num_non_empty += 1
            if self.num_non_empty == self.n:
                self._process()
        else:
            self._check_inter_message_bound(i)
        past = self.past[i]
        if len(dq) + len(past) > self.queue_size:
            self.counters["queue_overflow_drop"] += 1
            self.num_non_empty = 0
            for k in range(9):
                self._recover_all(k)
            assert dq
            dq.popleft()
            self.has_dropped[i] = True
            if self.pivot != NO_PIVOT:
                self.counters["candidate_destroyed_by_drop"] += 1
                self.candidate = None
                self.pivot = NO_PIVOT
                self._process()

    # L281-291
    def _delete_front(self, k):
        q = self.deques[k]
        assert q
        q.popleft()
        if not q:
            self.num_non_empty -= 1

    # L331-343
    def _move_front_to_past(self, k):
        q = self.deques[k]
        assert q
        self.past[k].append(q[0])
        q.popleft()
        if not q:
            self.num_non_empty -= 1

    # L381-427
    def _make_candidate(self):
        self.candidate = tuple(self.deques[k][0] for k in range(self.n))
        for k in range(9):
            self.past[k].clear()

    # L431-453
    def _recover_n(self, k, num):
        if k >= self.n:
            return
        v, q = self.past[k], self.deques[k]
        assert num <= len(v)
        while num > 0:
            q.appendleft(v[-1])
            v.pop()
            num -= 1
        if q:
            self.num_non_empty += 1

    # L456-476
    def _recover_all(self, k):
        if k >= self.n:
            return
        v, q = self.past[k], self.deques[k]
        while v:
            q.appendleft(v[-1])
            v.pop()
        if q:
            self.num_non_empty += 1

    # L479-502
    def _recover_and_delete(self, k):
        if k >= self.n:
            return
        v, q = self.past[k], self.deques[k]
        while v:
            q.appendleft(v[-1])
            v.pop()
        assert q
        q.popleft()
        if q:
            self.num_non_empty += 1

    # L505-527
    def _publish_candidate(self):
        self.counters["publish"] += 1
        if self.on_publish is not None:
            self.on_publish(self.candidate)
        self.candidate = None
        self.pivot = NO_PIVOT
        self.num_non_empty = 0
        for k in range(9):
            self._recover_and_delete(k)

    # L547-626: end=True -> latest head (ties: later index wins), end=False -> earliest head (ties: earlier index wins)
    def _boundary(self, end):
        time = self.deques[0][0][0]
        index = 0
        for k in range(1, self.n):
            t = self.deques[k][0][0]
            if (t < time) ^ end:
                time = t
                index = k
        return index, time

    # L630-656
    def _virtual_time(self, k):
        assert self.pivot != NO_PIVOT
        q = self.deques[k]
        if not q:
            assert self.past[k]
            last = self.past[k][-1][0]
            lower = last + self.lb[k]
            if lower > self.pivot_time:
                return lower
            return self.pivot_time
        return q[0][0]

    # L674-697
    def _virtual_boundary(self, end):
        vt = [self._virtual_time(k) for k in range(self.n)]
        time = vt[0]
        index = 0
        for k in range(self.n):
            if (vt[k] < time) ^ end:
                time = vt[k]
                index = k
        return index, time

    # L701-835
    def _process(self):
        while self.num_non_empty == self.n:
            end_index, end_time = self._boundary(True)
            start_index, start_time = self._boundary(False)
            for k in range(self.n):
                if k != end_index:
                    self.has_dropped[k] = False
            if self.pivot == NO_PIVOT:
                if end_time - start_time > self.max_interval:
                    self.counters["interval_too_big_delete"] += 1
                    self._delete_front(start_index)
                    continue
                if self.has_dropped[end_index]:
                    self.counters["dropped_pivot_delete"] += 1
                    self._delete_front(start_index)
                    continue
                self.counters["candidate_new"] += 1
                self._make_candidate()
                self.candidate_start = start_time
                self.candidate_end = end_time
                self.pivot = end_index
                self.pivot_time = end_time
                self._move_front_to_past(start_index)
            else:
                if self._scaled(end_time - self.candidate_end) >= (start_time - self.candidate_start):
                    self.counters["candidate_not_better"] += 1
                    self._move_front_to_past(start_index)
                else:
                    self.counters["candidate_better"] += 1
                    self._make_candidate()
                    self.candidate_start = start_time
                    self.candidate_end = end_time
                    self._move_front_to_past(start_index)
            assert self.pivot != NO_PIVOT
            if start_index == self.pivot:
                self.counters["publish_exhausted"] += 1
                self._publish_candidate()
            elif self._scaled(end_time - self.candidate_end) >= (self.pivot_time - self.candidate_start):
                self.counters["publish_provably_optimal"] += 1
                self._publish_candidate()
            elif self.num_non_empty < self.n:
                before = self.num_non_empty
                moves = [0] * 9
                self.counters["virtual_search"] += 1
                while True:
                    v_end_index, v_end_time = self._virtual_boundary(True)
                    v_start_index, v_start_time = self._virtual_boundary(False)
                    if self._scaled(v_end_time - self.candidate_end) >= (self.pivot_time - self.candidate_start):
                        self.counters["publish_virtual_proof"] += 1
                        self._publish_candidate()
                        break
                    if self._scaled(v_end_time - self.candidate_end) < (v_start_time - self.candidate_start):
                        self.counters["virtual_cannot_prove"] += 1
                        self.num_non_empty = 0
                        for k in range(9):
                            self._recover_n(k, moves[k])
                        assert before == self.num_non_empty
                        break
                    assert v_start_index != self.pivot
                    assert v_start_time < self.pivot_time
                    self.counters["virtual_move"] += 1
                    self._move_front_to_past(v_start_index)
                    moves[v_start_index] += 1


# ---------------------------------------------------------------------------
# Trace driver (same trace format as the C++ oracle)
# ---------------------------------------------------------------------------

def run_trace(trace, scale_mode="longdouble"):
    """trace: dict(name, params, events=[(eid, input, stamp_ns)]).
    Returns (callbacks, counters) where callbacks = [(trigger_eid, (eid_0, ..., eid_n-1))]."""
    p = trace["params"]
    callbacks = []
    current = {"eid": None}

    def on_publish(candidate):
        callbacks.append((current["eid"], tuple(m[1] for m in candidate)))

    policy = ApproximateTimeRef(
        n=p["n"], queue_size=p["queue"],
        max_interval_ns=MAX_DURATION_NS if p["max_interval_ns"] is None else p["max_interval_ns"],
        age_penalty=p["age_penalty"], lower_bounds_ns=p.get("lower_bounds_ns"),
        scale_mode=scale_mode, on_publish=on_publish)
    for eid, inp, stamp in trace["events"]:
        current["eid"] = eid
        policy.add(inp, (stamp, eid))
    return callbacks, policy.counters
