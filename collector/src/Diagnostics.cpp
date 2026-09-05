#include "Diagnostics.h"

#include <Windows.h>

#include <share.h>

#include <chrono>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <mutex>

namespace {
std::mutex log_mutex;
std::filesystem::path log_path;

std::string timestamp() {
    const std::time_t now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm local{};
    localtime_s(&local, &now);
    char buffer[32]{};
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02d %02d:%02d:%02d", local.tm_year + 1900,
                  local.tm_mon + 1, local.tm_mday, local.tm_hour, local.tm_min, local.tm_sec);
    return buffer;
}
}  // namespace

namespace diagnostics {

void initialize(std::filesystem::path path) {
    std::lock_guard lock(log_mutex);
    log_path = std::move(path);
    std::error_code error;
    std::filesystem::create_directories(log_path.parent_path(), error);
}

void write(const std::string& message) {
    std::lock_guard lock(log_mutex);
    if (log_path.empty()) return;

    std::error_code error;
    if (const auto size = std::filesystem::file_size(log_path, error); !error && size > 2 * 1024 * 1024) {
        std::filesystem::remove(log_path, error);
    }

    if (FILE* output = _wfsopen(log_path.c_str(), L"a", _SH_DENYNO)) {
        std::fprintf(output, "%s %s\n", timestamp().c_str(), message.c_str());
        std::fclose(output);
    }
    std::string debug_line = "ActivityTimeline: " + message + "\n";
    OutputDebugStringA(debug_line.c_str());
}

}  // namespace diagnostics
