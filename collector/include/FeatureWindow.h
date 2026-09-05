#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

struct ClipboardMetadata {
    std::wstring kind;
    std::wstring length_bucket;
    std::chrono::system_clock::time_point occurred_at;
};

struct WindowContext {
    std::wstring process;
    std::wstring window_title;
};

struct InteractionSnapshot {
    std::uint64_t key_count{};
    std::uint64_t mouse_click_count{};
    std::uint64_t scroll_count{};
    std::uint64_t clipboard_copy_count{};
    std::uint64_t clipboard_paste_count{};
    std::uint64_t idle_ms{};
    std::vector<ClipboardMetadata> clipboard_events;
};

struct FeatureWindow {
    std::wstring device_id;
    std::uint64_t sequence{};
    std::chrono::system_clock::time_point start_time;
    std::uint64_t duration_ms{};
    WindowContext context;
    InteractionSnapshot interaction;
};

