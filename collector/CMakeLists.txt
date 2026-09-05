cmake_minimum_required(VERSION 3.20)
project(activity_collector VERSION 0.2.0 LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "activity_collector uses Win32 APIs and must be built on Windows")
endif()

add_executable(activity_collector WIN32
    src/main.cpp
    src/WindowSampler.cpp
    src/InputAggregator.cpp
    src/ClipboardObserver.cpp
    src/IdleDetector.cpp
    src/BatchUploader.cpp
    src/CollectorWorker.cpp
)

target_include_directories(activity_collector PRIVATE include)
target_compile_features(activity_collector PRIVATE cxx_std_20)
target_compile_definitions(activity_collector PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_definitions(activity_collector PRIVATE ACTIVITY_COLLECTOR_VERSION="${PROJECT_VERSION}")
target_link_libraries(activity_collector PRIVATE user32 shell32 winhttp advapi32)

if(MSVC)
    target_compile_options(activity_collector PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(activity_collector PRIVATE -Wall -Wextra -Wpedantic)
    # Static-link the GCC runtime so the exe can start from the HKCU Run key at
    # boot, where PATH does not include the MinGW bin directory.
    target_link_options(activity_collector PRIVATE -municode -static-libgcc -static-libstdc++)
endif()
