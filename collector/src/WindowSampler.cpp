#include "WindowSampler.h"

#include <Windows.h>

#include <filesystem>
#include <string>
#include <vector>

WindowContext WindowSampler::sample(HWND foreground) const {
    WindowContext result{L"Unknown.exe", L""};
    if (!foreground) foreground = GetForegroundWindow();
    if (!foreground) {
        return result;
    }

    const int title_length = GetWindowTextLengthW(foreground);
    if (title_length > 0) {
        std::vector<wchar_t> buffer(static_cast<std::size_t>(title_length) + 1);
        const int copied = GetWindowTextW(foreground, buffer.data(), static_cast<int>(buffer.size()));
        if (copied > 0) {
            result.window_title.assign(buffer.data(), static_cast<std::size_t>(copied));
        }
    }

    DWORD process_id = 0;
    GetWindowThreadProcessId(foreground, &process_id);
    if (!process_id) {
        return result;
    }

    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id);
    if (!process) {
        return result;
    }

    std::vector<wchar_t> path_buffer(32768);
    DWORD path_length = static_cast<DWORD>(path_buffer.size());
    if (QueryFullProcessImageNameW(process, 0, path_buffer.data(), &path_length)) {
        result.process = std::filesystem::path(std::wstring(path_buffer.data(), path_length)).filename().wstring();
    }
    CloseHandle(process);
    return result;
}
