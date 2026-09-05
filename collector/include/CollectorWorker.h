#pragma once

#include "BatchUploader.h"
#include "FeatureWindow.h"
#include "InputAggregator.h"
#include "WindowSampler.h"

#include <Windows.h>

#include <chrono>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>

struct FeatureSnapshot {
    std::chrono::system_clock::time_point start_time;
    std::uint64_t duration_ms{};
    HWND foreground_window{};
    InputCounts input;
    std::uint64_t idle_ms{};
    std::vector<ClipboardMetadata> clipboard_events;
};

class CollectorWorker {
public:
    CollectorWorker(
        std::wstring device_id,
        std::filesystem::path state_path,
        std::wstring server_url,
        std::wstring api_token,
        std::filesystem::path queue_path,
        std::size_t batch_size,
        std::size_t max_queue
    );
    ~CollectorWorker();

    CollectorWorker(const CollectorWorker&) = delete;
    CollectorWorker& operator=(const CollectorWorker&) = delete;

    void start();
    void submit(FeatureSnapshot snapshot);
    void stop();

private:
    void run();
    void persist(FeatureSnapshot snapshot);
    [[nodiscard]] std::uint64_t load_sequence() const;
    void save_sequence(std::uint64_t value) const;

    WindowSampler window_sampler_;
    BatchUploader uploader_;
    std::wstring device_id_;
    std::filesystem::path state_path_;
    std::uint64_t sequence_{};

    std::mutex mutex_;
    // Win32 auto-reset event instead of std::condition_variable: the event's
    // signal LATCHES, so a SetEvent() that fires between the worker's
    // "pending_ empty" check and WaitForSingleObject() can never be lost.
    // MinGW's condition_variable timed wait (__gthr_win32_cond_timedwait)
    // was observed hanging indefinitely with steady_clock deadlines, only
    // recovering when a debugger attached.
    HANDLE wake_event_{CreateEventW(nullptr, FALSE, FALSE, nullptr)};
    std::deque<FeatureSnapshot> pending_;
    std::thread thread_;
    bool started_{false};
    bool stopping_{false};
};

