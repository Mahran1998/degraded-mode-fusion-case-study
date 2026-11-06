# Notice

Copyright 2026 Mahran Rezk. Licensed under the Apache License, Version 2.0 (see `LICENSE`), except as noted below.

- **`monitor/`** contains a patch against
  [`openads-project/point_cloud_fusion`](https://github.com/openads-project/point_cloud_fusion)
  (Apache-2.0, Institute for Automotive Engineering (ika), RWTH Aachen University). The new source files
  in the patch carry the upstream project's copyright header because they were written as a contribution
  to that project. Applying the patch requires the upstream sources.
- **`model/phase2a/at_reference.py`** is a Python port of the `ApproximateTime` policy from
  [`ros2/message_filters`](https://github.com/ros2/message_filters) 4.11.12, which is released under the
  BSD 3-Clause License (Copyright (c) 2008-2013, Willow Garage Inc., and contributors).
  `model/phase2a/oracle/` compiles against the original `message_filters` headers, which are not included here.
- The harness in `harness/` drives the upstream project's published dev container image
  (`ghcr.io/openads-project/point_cloud_fusion:latest-dev`), which is not included here.

OpenADS, ika and RWTH Aachen University are named only to describe the origin of the problem. This
repository is not affiliated with or endorsed by them.
