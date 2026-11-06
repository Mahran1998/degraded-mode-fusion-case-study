cmake_minimum_required(VERSION 3.16)
project(at_oracle CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# No CMAKE_BUILD_TYPE on purpose: assert() / RCUTILS_ASSERT stay active in the oracle.

if(NOT MF_HEADERS)
  message(FATAL_ERROR "Pass -DMF_HEADERS=<dir containing message_filters/sync_policies/approximate_time.h>")
endif()
if(NOT EXISTS "${MF_HEADERS}/message_filters/sync_policies/approximate_time.h")
  message(FATAL_ERROR "approximate_time.h not found under ${MF_HEADERS}")
endif()
file(SHA256 "${MF_HEADERS}/message_filters/sync_policies/approximate_time.h" AT_SHA)
message(STATUS "Oracle uses approximate_time.h sha256=${AT_SHA}")

find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(message_filters REQUIRED)

add_executable(at_oracle oracle.cpp)
# BEFORE: the exact 4.11.12 headers take precedence over the base image's installed copy.
target_include_directories(at_oracle BEFORE PRIVATE "${MF_HEADERS}")
target_link_libraries(at_oracle rclcpp::rclcpp message_filters::message_filters ${sensor_msgs_TARGETS})
