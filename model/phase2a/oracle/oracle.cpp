// C++ oracle for the ApproximateTime differential test.
//
// Drives message_filters::Synchronizer<ApproximateTime<...>> directly through
// Synchronizer::add<i>(msg), in the exact arrival order given by the trace,
// and prints every callback the policy emits. No executor, no threads, no
// network: the output is a deterministic function of (trace, header version).
//
// Build against the exact message_filters 4.11.12 headers extracted from
// ghcr.io/openads-project/point_cloud_fusion:latest-slim (see CMakeLists.txt).
//
// Input  (stdin):  trace format described in tracefmt.py
// Output (stdout): TRACE / CB / END / SCALE lines described in tracefmt.py

#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#ifdef AT_STUB
#include "stub_msg.hpp"  // local self-check only (not part of the real oracle)
using Msg = stub::PointCloud2;
#else
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
using Msg = sensor_msgs::msg::PointCloud2;
#endif

#include "message_filters/sync_policies/approximate_time.h"

using MPtr = std::shared_ptr<const Msg>;
namespace sp = message_filters::sync_policies;

template <std::size_t N> struct PolicyFor;
template <> struct PolicyFor<2> { using type = sp::ApproximateTime<Msg, Msg>; };
template <> struct PolicyFor<3> { using type = sp::ApproximateTime<Msg, Msg, Msg>; };
template <> struct PolicyFor<4> { using type = sp::ApproximateTime<Msg, Msg, Msg, Msg>; };
template <> struct PolicyFor<5> { using type = sp::ApproximateTime<Msg, Msg, Msg, Msg, Msg>; };
template <> struct PolicyFor<6> { using type = sp::ApproximateTime<Msg, Msg, Msg, Msg, Msg, Msg>; };
template <> struct PolicyFor<7> { using type = sp::ApproximateTime<Msg, Msg, Msg, Msg, Msg, Msg, Msg>; };
template <> struct PolicyFor<8> { using type = sp::ApproximateTime<Msg, Msg, Msg, Msg, Msg, Msg, Msg, Msg>; };
template <> struct PolicyFor<9> { using type = sp::ApproximateTime<Msg, Msg, Msg, Msg, Msg, Msg, Msg, Msg, Msg>; };

struct Trace {
  std::string name;
  int n = 0;
  uint32_t queue = 0;
  bool max_interval_default = true;
  int64_t max_interval_ns = 0;
  double age_penalty = 0.1;
  std::vector<int64_t> lower_bounds_ns;
  std::vector<std::tuple<int64_t, int, int64_t>> events;  // (event_id, input, stamp_ns)
};

template <class Sync, std::size_t... Is>
void dispatch_add(Sync& sync, int idx, const MPtr& msg, std::index_sequence<Is...>) {
  bool done = false;
  ((idx == static_cast<int>(Is) ? (sync.template add<static_cast<int>(Is)>(msg), done = true) : false), ...);
  if (!done) {
    std::cerr << "oracle: input index out of range: " << idx << "\n";
    std::exit(3);
  }
}

template <std::size_t N>
void run(const Trace& t, std::ostream& out) {
  using Policy = typename PolicyFor<N>::type;
  using Sync = message_filters::Synchronizer<Policy>;

  Sync sync{Policy(t.queue)};
  if (!t.max_interval_default) {
    sync.setMaxIntervalDuration(rclcpp::Duration::from_nanoseconds(t.max_interval_ns));
  }
  sync.setAgePenalty(t.age_penalty);
  for (std::size_t i = 0; i < t.lower_bounds_ns.size() && i < N; ++i) {
    sync.setInterMessageLowerBound(static_cast<int>(i), rclcpp::Duration::from_nanoseconds(t.lower_bounds_ns[i]));
  }

  int64_t current_event = -1;
  std::size_t count = 0;
  std::ostringstream buf;
  // Same callback shape as point_cloud_fusion (main:784-796): a generic lambda over
  // all nine arguments, keeping only the real point-cloud pointers, in slot order.
  sync.registerCallback([&](auto&&... msgs) {
    buf << "CB " << current_event;
    auto emit = [&](auto&& m) {
      using ArgT = std::decay_t<decltype(m)>;
      if constexpr (std::is_same_v<ArgT, MPtr>) {
        buf << ' ' << m->header.frame_id;
      }
    };
    (emit(msgs), ...);
    buf << '\n';
    ++count;
  });

  for (const auto& [eid, input, stamp] : t.events) {
    auto msg = std::make_shared<Msg>();
    msg->header.stamp.sec = static_cast<int32_t>(stamp / 1000000000LL);
    msg->header.stamp.nanosec = static_cast<uint32_t>(stamp % 1000000000LL);
    msg->header.frame_id = std::to_string(eid);
    current_event = eid;
    dispatch_add(sync, input, MPtr(msg), std::make_index_sequence<N>{});
  }
  out << "TRACE " << t.name << '\n' << buf.str() << "END " << t.name << ' ' << count << '\n';
}

void run_any(const Trace& t, std::ostream& out) {
  switch (t.n) {
    case 2: run<2>(t, out); break;
    case 3: run<3>(t, out); break;
    case 4: run<4>(t, out); break;
    case 5: run<5>(t, out); break;
    case 6: run<6>(t, out); break;
    case 7: run<7>(t, out); break;
    case 8: run<8>(t, out); break;
    case 9: run<9>(t, out); break;
    default:
      std::cerr << "oracle: unsupported n=" << t.n << " in " << t.name << "\n";
      std::exit(3);
  }
}

int main() {
  std::ios::sync_with_stdio(false);
  std::string line;
  Trace cur;
  bool in_trace = false;
  std::size_t traces = 0;
  while (std::getline(std::cin, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::istringstream is(line);
    std::string tag;
    if (!(is >> tag) || tag[0] == '#') continue;
    if (tag == "TRACE") {
      cur = Trace{};
      is >> cur.name;
      in_trace = true;
    } else if (tag == "PARAM") {
      std::string key;
      is >> key;
      if (key == "n") {
        is >> cur.n;
      } else if (key == "queue") {
        is >> cur.queue;
      } else if (key == "max_interval_ns") {
        std::string v;
        is >> v;
        cur.max_interval_default = (v == "default");
        if (!cur.max_interval_default) cur.max_interval_ns = std::stoll(v);
      } else if (key == "age_penalty") {
        std::string v;
        is >> v;
        cur.age_penalty = std::stod(v);
      } else if (key == "lower_bounds_ns") {
        int64_t v;
        while (is >> v) cur.lower_bounds_ns.push_back(v);
      } else {
        std::cerr << "oracle: unknown PARAM " << key << "\n";
        return 3;
      }
    } else if (tag == "EV") {
      int64_t eid, stamp;
      int input;
      is >> eid >> input >> stamp;
      cur.events.emplace_back(eid, input, stamp);
    } else if (tag == "END") {
      if (!in_trace) { std::cerr << "oracle: END without TRACE\n"; return 3; }
      run_any(cur, std::cout);
      in_trace = false;
      ++traces;
    } else if (tag == "PROBE_SCALE") {
      // Direct check of rclcpp::Duration::operator*(double), used by ApproximateTime (L753/776/797/805)
      std::string scale_text;
      is >> scale_text;
      const double scale = std::stod(scale_text);
      int64_t ns;
      while (is >> ns) {
        const auto r = rclcpp::Duration::from_nanoseconds(ns) * scale;
        std::cout << "SCALE " << scale_text << ' ' << ns << ' ' << r.nanoseconds() << '\n';
      }
    } else {
      std::cerr << "oracle: unknown line: " << line << "\n";
      return 3;
    }
  }
  std::cerr << "oracle: traces processed = " << traces << "\n";
  return 0;
}
