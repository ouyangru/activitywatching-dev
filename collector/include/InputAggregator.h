#pragma once

#include <Windows.h>

#include <atomic>
#include <cstdint>

struct InputCounts {
    std::uint64_t keys{};
    std::uint64_t clicks{};
    std::uint64_t scrolls{};
    std::uint64_t pastes{};
};

class InputAggregator {
public:
    bool install();
    void uninstall();
    [[nodiscard]] InputCounts take_snapshot();

private:
    static LRESULT CALLBACK keyboard_proc(int code, WPARAM w_param, LPARAM l_param);
    static LRESULT CALLBACK mouse_proc(int code, WPARAM w_param, LPARAM l_param);

    inline static std::atomic<std::uint64_t> keys_{0};
    inline static std::atomic<std::uint64_t> clicks_{0};
    inline static std::atomic<std::uint64_t> scrolls_{0};
    inline static std::atomic<std::uint64_t> pastes_{0};
    inline static std::atomic<bool> control_down_{false};
    HHOOK keyboard_hook_{};
    HHOOK mouse_hook_{};
};
