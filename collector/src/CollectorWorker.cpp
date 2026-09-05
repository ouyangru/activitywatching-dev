#include "CollectorWorker.h"

#include <algorithm>
#include <chrono>
#include <fstream>
#include <utility>

using namespace std::chrono_literals;

CollectorWorker::CollectorWorker(
    std::wstring device_id,
    std::filesystem::path state_path,
    std::wstring server_url,
    std::wstring api_token,
    std::filesystem::path queue_path,
    std::size_t batch_size,
    std::size_t max_queue
)
    : uploader_(std::move(server_url), std::move(api_token), std::move(queue_path), batch_size, max_queue),
      device_id_(std::move(device_id)),
      state_path_(std::move(state_path)),
      sequence_(load_sequence()) {}

CollectorWorker::~CollectorWorker() {
    stop();
}

void CollectorWorker::start() {
    std::lock_guard lock(mutex_);
    if (started_) return;
    started_ = true;
    thread_ = std::thread(&CollectorWorker::run, this);
}

void CollectorWorker::submit(FeatureSnapshot snapshot) {
    {
        std::lock_guard lock(mutex_);
        if (stopping_) return;
        pending_.push_back(std::move(snapshot));
    }
    condition_.notify_one();
}

void CollectorWorker::stop() {
    {
        std::lock_guard lock(mutex_);
        if (!started_ || stopping_) return;
        stopping_ = true;
    }
    condition_.notify_one();
    if (thread_.joinable()) thread_.join();
    started_ = false;
}

void CollectorWorker::run() {
    using clock = std::chrono::steady_clock;
    // The backend marks a device online when a heartbeat arrives within 120s,
    // so report every 60s. Heartbeat failures use their own backoff and never
    // delay (or get delayed by) data uploads.
    constexpr auto kHeartbeatInterval = 60s;
    auto next_upload_attempt = clock::now();
    auto next_heartbeat = clock::now();
    unsigned int consecutive_failures = 0;
    unsigned int heartbeat_failures = 0;

    for (;;) {
        std::deque<FeatureSnapshot> snapshots;
        bool should_stop = false;
        {
            std::unique_lock lock(mutex_);
            if (pending_.empty() && !stopping_) {
                // Wake up for the earliest of: heartbeat due, upload retry due.
                auto deadline = next_heartbeat;
                if (uploader_.pending_count() > 0) deadline = std::min(deadline, next_upload_attempt);
                condition_.wait_until(lock, deadline, [this] { return stopping_ || !pending_.empty(); });
            }
            snapshots.swap(pending_);
            should_stop = stopping_;
        }

        // Persist newly captured windows before attempting any network work.
        // Even when WinHTTP blocks, the hook/message thread remains independent.
        for (auto& snapshot : snapshots) persist(std::move(snapshot));

        auto now = clock::now();
        if (!should_stop && now >= next_heartbeat) {
            if (uploader_.post_heartbeat(device_id_)) {
                heartbeat_failures = 0;
                next_heartbeat = now + kHeartbeatInterval;
            } else {
                const auto exponent = std::min(heartbeat_failures, 3U);
                ++heartbeat_failures;
                next_heartbeat = now + std::chrono::seconds(std::min(300U, 60U * (1U << exponent)));
            }
        }

        now = clock::now();
        if (!should_stop && uploader_.pending_count() > 0 && now >= next_upload_attempt) {
            bool upload_ok = true;
            std::size_t batches_sent = 0;
            while (uploader_.pending_count() > 0 && batches_sent < 3) {
                upload_ok = uploader_.flush();
                if (!upload_ok) break;
                ++batches_sent;

                // New snapshots always take priority over draining an old backlog.
                std::lock_guard lock(mutex_);
                if (!pending_.empty()) break;
            }

            if (upload_ok) {
                consecutive_failures = 0;
                next_upload_attempt = clock::now();
            } else {
                const auto exponent = std::min(consecutive_failures, 6U);
                const auto delay_seconds = std::min(300U, 5U * (1U << exponent));
                ++consecutive_failures;
                next_upload_attempt = clock::now() + std::chrono::seconds(delay_seconds);
            }
        }

        // On shutdown every in-memory snapshot is already durable. Do not make
        // the user wait for an unreachable backend before the process exits.
        if (should_stop) break;
    }
}

void CollectorWorker::persist(FeatureSnapshot snapshot) {
    FeatureWindow feature;
    feature.device_id = device_id_;
    feature.sequence = sequence_++;
    feature.start_time = snapshot.start_time;
    feature.duration_ms = snapshot.duration_ms;
    feature.context = window_sampler_.sample(snapshot.foreground_window);
    feature.interaction.key_count = snapshot.input.keys;
    feature.interaction.mouse_click_count = snapshot.input.clicks;
    feature.interaction.scroll_count = snapshot.input.scrolls;
    feature.interaction.clipboard_copy_count = snapshot.clipboard_events.size();
    feature.interaction.clipboard_paste_count = snapshot.input.pastes;
    feature.interaction.idle_ms = snapshot.idle_ms;
    feature.interaction.clipboard_events = std::move(snapshot.clipboard_events);

    // Store the next sequence before queueing the event. A crash may create a
    // harmless gap, but can never reuse an already uploaded id.
    save_sequence(sequence_);
    uploader_.enqueue(feature);
}

std::uint64_t CollectorWorker::load_sequence() const {
    std::wifstream input(state_path_);
    std::uint64_t value = 0;
    if (input >> value) return value;
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count()) * 100;
}

void CollectorWorker::save_sequence(std::uint64_t value) const {
    std::error_code error;
    std::filesystem::create_directories(state_path_.parent_path(), error);
    std::wofstream output(state_path_, std::ios::trunc);
    output << value;
}

