cmake_minimum_required(VERSION 3.16)
project(steam-headless C)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(X11 REQUIRED COMPONENTS Xfixes)

add_library(xfixes-shim SHARED src/xfixes-shim/xfixes-shim.c)
set_target_properties(xfixes-shim PROPERTIES
  OUTPUT_NAME xfixes-shim
  POSITION_INDEPENDENT_CODE ON
)
target_compile_options(xfixes-shim PRIVATE -fno-strict-aliasing)
target_link_libraries(xfixes-shim PRIVATE dl)

add_executable(probe src/xfixes-shim/probe.c)
target_link_libraries(probe PRIVATE X11::X11 X11::Xfixes)

add_library(region-confine-trace SHARED src/region-confine-trace/region-confine-trace.c)
set_target_properties(region-confine-trace PROPERTIES
  OUTPUT_NAME region-confine-trace
  POSITION_INDEPENDENT_CODE ON
)
target_link_libraries(region-confine-trace PRIVATE dl m)
