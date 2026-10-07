cmake_minimum_required(VERSION 3.16)
project(steam-headless C)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_library(region-confine-trace SHARED src/region-confine-trace/region-confine-trace.c)
set_target_properties(region-confine-trace PROPERTIES
  OUTPUT_NAME region-confine-trace
  POSITION_INDEPENDENT_CODE ON
)
target_link_libraries(region-confine-trace PRIVATE dl m)
