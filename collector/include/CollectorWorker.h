#pragma once

#include "BatchUploader.h"
#include "FeatureWindow.h"
#include "InputAggregator.h"
#include "WindowSampler.h"

#include <Windows.h>

#include <chrono>
#include <condition_variable>
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
    std::condition_variable condition_;
    std::deque<FeatureSnapshot> pending_;
    std::thread thread_;
    bool started_{false};
    bool stopping_{false};
};

