#include "BatchUploader.h"

#include <algorithm>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <system_error>

#ifndef ACTIVITY_COLLECTOR_VERSION
#define ACTIVITY_COLLECTOR_VERSION "dev"
#endif

namespace {
std::string utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
}

std::wstring widen(const std::string& value) {
    if (value.empty()) return {};
    const int size = MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (size <= 0) return {};
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size);
    return result;
}

std::string escape_json(const std::wstring& value) {
    std::ostringstream output;
    for (const unsigned char character : utf8(value)) {
        switch (character) {
        case '\"': output << "\\\""; break;
        case '\\': output << "\\\\"; break;
        case '\b': output << "\\b"; break;
        case '\f': output << "\\f"; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (character < 0x20) {
                output << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(character);
            } else {
                output << character;
            }
        }
    }
    return output.str();
}

std::string iso8601(std::chrono::system_clock::time_point point) {
    const auto milliseconds = std::chrono::duration_cast<std::chrono::milliseconds>(point.time_since_epoch()) % 1000;
    const std::time_t value = std::chrono::system_clock::to_time_t(point);
    std::tm utc{};
    gmtime_s(&utc, &value);
    std::ostringstream output;
    output << std::put_time(&utc, "%Y-%m-%dT%H:%M:%S") << '.' << std::setw(3) << std::setfill('0')
           << milliseconds.count() << 'Z';
    return output.str();
}
}

BatchUploader::BatchUploader(std::wstring server_url, std::wstring api_token, std::filesystem::path queue_path,
                                                         std::size_t batch_size, std::size_t max_queue)
        : server_url_(std::move(server_url)), api_token_(std::move(api_token)), queue_path_(std::move(queue_path)),
            batch_size_(batch_size),
      max_queue_(max_queue) {
    const bool endpoint_valid = parse_endpoint();
    (void)endpoint_valid;
    queue_size_ = read_queue().size();
}

bool BatchUploader::parse_endpoint() {
    URL_COMPONENTS components{};
    components.dwStructSize = sizeof(components);
    components.dwSchemeLength = static_cast<DWORD>(-1);
    components.dwHostNameLength = static_cast<DWORD>(-1);
    components.dwUrlPathLength = static_cast<DWORD>(-1);
    if (!WinHttpCrackUrl(server_url_.c_str(), static_cast<DWORD>(server_url_.size()), 0, &components)) return false;
    endpoint_.host.assign(components.lpszHostName, components.dwHostNameLength);
    endpoint_.port = components.nPort;
    endpoint_.secure = components.nScheme == INTERNET_SCHEME_HTTPS;
    endpoint_.path_prefix.assign(components.lpszUrlPath, components.dwUrlPathLength);
    while (!endpoint_.path_prefix.empty() && endpoint_.path_prefix.back() == L'/') endpoint_.path_prefix.pop_back();
    return !endpoint_.host.empty();
}

std::string BatchUploader::serialize(const FeatureWindow& window) {
    std::ostringstream output;
    output << "{\"device_id\":\"" << escape_json(window.device_id) << "\",\"sequence\":" << window.sequence
           << ",\"start_time\":\"" << iso8601(window.start_time) << "\",\"duration_ms\":" << window.duration_ms
           << ",\"context\":{\"process\":\"" << escape_json(window.context.process)
           << "\",\"window_title\":\"" << escape_json(window.context.window_title) << "\"},\"interaction\":{"
           << "\"key_count\":" << window.interaction.key_count << ",\"mouse_click_count\":" << window.interaction.mouse_click_count
           << ",\"scroll_count\":" << window.interaction.scroll_count << ",\"idle_ms\":" << window.interaction.idle_ms
           << ",\"clipboard_copy_count\":" << window.interaction.clipboard_copy_count
           << ",\"clipboard_paste_count\":" << window.interaction.clipboard_paste_count << ",\"clipboard_events\":[";
    for (std::size_t index = 0; index < window.interaction.clipboard_events.size(); ++index) {
        if (index) output << ',';
        const auto& event = window.interaction.clipboard_events[index];
        output << "{\"kind\":\"" << escape_json(event.kind) << "\",\"length_bucket\":\""
               << escape_json(event.length_bucket) << "\",\"occurred_at\":\"" << iso8601(event.occurred_at) << "\"}";
    }
    output << "]}}";
    return output.str();
}

bool BatchUploader::enqueue(const FeatureWindow& window) {
    std::lock_guard lock(mutex_);
    std::error_code error;
    std::filesystem::create_directories(queue_path_.parent_path(), error);
    std::ofstream output(queue_path_, std::ios::binary | std::ios::app);
    if (!output) return false;
    output << serialize(window) << '\n';
    output.flush();
    if (!output) return false;
    output.close();
    ++queue_size_;

    if (queue_size_ > max_queue_) {
        OutputDebugStringW(L"ActivityTimeline: offline queue reached its limit; dropping the oldest window.\n");
        auto lines = read_queue();
        lines.erase(lines.begin(), lines.begin() + static_cast<std::ptrdiff_t>(lines.size() - max_queue_));
        queue_size_ = lines.size();
        return write_queue(lines);
    }
    return true;
}

bool BatchUploader::flush() {
    std::lock_guard lock(mutex_);
    auto lines = read_queue();
    if (lines.empty()) return true;
    const auto count = std::min(batch_size_, lines.size());
    std::vector<std::string> batch(lines.begin(), lines.begin() + static_cast<std::ptrdiff_t>(count));
    if (!post_batch(batch)) return false;
    lines.erase(lines.begin(), lines.begin() + static_cast<std::ptrdiff_t>(count));
    queue_size_ = lines.size();
    return write_queue(lines);
}

std::size_t BatchUploader::pending_count() const {
    std::lock_guard lock(mutex_);
    return queue_size_;
}

std::vector<std::string> BatchUploader::read_queue() const {
    std::vector<std::string> lines;
    std::ifstream input(queue_path_, std::ios::binary);
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty()) lines.push_back(std::move(line));
    }
    return lines;
}

bool BatchUploader::write_queue(const std::vector<std::string>& lines) const {
    std::error_code error;
    std::filesystem::create_directories(queue_path_.parent_path(), error);
    auto temporary = queue_path_;
    temporary += L".tmp";
    {
        std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
        if (!output) return false;
        for (const auto& line : lines) output << line << '\n';
        output.flush();
        if (!output) return false;
    }
    if (!MoveFileExW(temporary.c_str(), queue_path_.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        std::filesystem::remove(temporary, error);
        return false;
    }
    return true;
}

bool BatchUploader::post_heartbeat(const std::wstring& device_id) {
    if (endpoint_.host.empty()) return false;
    std::ostringstream body;
    body << "{\"device_id\":\"" << escape_json(device_id) << "\",\"platform\":\"windows\",\"collector_version\":\""
         << ACTIVITY_COLLECTOR_VERSION << "\"}";
    const std::wstring path = endpoint_.path_prefix + L"/api/v1/heartbeat";
    return post_json(path, body.str());
}

bool BatchUploader::post_batch(const std::vector<std::string>& lines) const {
    if (endpoint_.host.empty()) return false;
    std::ostringstream body;
    body << "{\"events\":[";
    for (std::size_t index = 0; index < lines.size(); ++index) {
        if (index) body << ',';
        body << lines[index];
    }
    body << "]}";
    const std::wstring path = endpoint_.path_prefix + L"/api/v1/events/batch";
    return post_json(path, body.str());
}

bool BatchUploader::post_json(const std::wstring& path, const std::string& payload) const {
    HINTERNET session = WinHttpOpen(widen("ActivityTimelineCollector/" ACTIVITY_COLLECTOR_VERSION).c_str(),
                                    WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) return false;
    WinHttpSetTimeouts(session, 3000, 3000, 5000, 5000);
    HINTERNET connection = WinHttpConnect(session, endpoint_.host.c_str(), endpoint_.port, 0);
    if (!connection) {
        WinHttpCloseHandle(session);
        return false;
    }
    HINTERNET request = WinHttpOpenRequest(connection, L"POST", path.c_str(), nullptr, WINHTTP_NO_REFERER,
                                           WINHTTP_DEFAULT_ACCEPT_TYPES,
                                           endpoint_.secure ? WINHTTP_FLAG_SECURE : 0);
    bool success = false;
    if (request) {
        std::wstring headers = L"Content-Type: application/json; charset=utf-8\r\n";
        if (!api_token_.empty()) headers += L"Authorization: Bearer " + api_token_ + L"\r\n";
        if (WinHttpSendRequest(request, headers.c_str(), static_cast<DWORD>(-1),
                               const_cast<char*>(payload.data()), static_cast<DWORD>(payload.size()),
                               static_cast<DWORD>(payload.size()), 0) &&
            WinHttpReceiveResponse(request, nullptr)) {
            DWORD status = 0;
            DWORD size = sizeof(status);
            if (WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                    WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX)) {
                success = status >= 200 && status < 300;
            }
        }
        WinHttpCloseHandle(request);
    }
    WinHttpCloseHandle(connection);
    WinHttpCloseHandle(session);
    return success;
}
