#pragma once

#include "FeatureWindow.h"

#include <Windows.h>
#include <winhttp.h>

#include <cstddef>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

class BatchUploader {
public:
    BatchUploader(std::wstring server_url, std::wstring api_token, std::filesystem::path queue_path,
                  std::size_t batch_size, std::size_t max_queue);
    bool enqueue(const FeatureWindow& window);
    bool flush();
    [[nodiscard]] std::size_t pending_count() const;

    [[nodiscard]] static std::string serialize(const FeatureWindow& window);

private:
    struct Endpoint {
        std::wstring host;
        INTERNET_PORT port{};
        bool secure{};
        std::wstring path_prefix;
    };

    [[nodiscard]] bool parse_endpoint();
    [[nodiscard]] std::vector<std::string> read_queue() const;
    bool write_queue(const std::vector<std::string>& lines) const;
    [[nodiscard]] bool post_batch(const std::vector<std::string>& lines) const;

    std::wstring server_url_;
    std::wstring api_token_;
    std::filesystem::path queue_path_;
    std::size_t batch_size_;
    std::size_t max_queue_;
    std::size_t queue_size_{};
    Endpoint endpoint_;
    mutable std::mutex mutex_;
};
