#include "BatchUploader.h"
#include "ClipboardObserver.h"
#include "CollectorWorker.h"
#include "FeatureWindow.h"
#include "IdleDetector.h"
#include "InputAggregator.h"
#include "WindowSampler.h"

#include <Windows.h>
#include <shellapi.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace {
constexpr UINT_PTR kSampleTimer = 1;
constexpr UINT kTrayMessage = WM_APP + 1;
constexpr UINT kTrayId = 100;
constexpr UINT kExitCommand = 200;

struct Options {
    std::wstring server{L"http://localhost:8765"};
    std::wstring token;
    std::uint32_t interval_seconds{10};
    std::size_t batch_size{12};
    std::size_t max_queue{60480};
    bool console{false};
};

struct Application {
    InputAggregator input;
    ClipboardObserver clipboard;
    IdleDetector idle;
    std::unique_ptr<CollectorWorker> worker;
    std::uint32_t interval_seconds{10};
    std::chrono::system_clock::time_point window_start{std::chrono::system_clock::now()};
};

Application* g_application = nullptr;

std::vector<std::wstring> command_line_arguments() {
    int count = 0;
    LPWSTR* values = CommandLineToArgvW(GetCommandLineW(), &count);
    std::vector<std::wstring> result;
    if (values) {
        result.assign(values, values + count);
        LocalFree(values);
    }
    return result;
}

Options parse_options() {
    Options options;
    const auto arguments = command_line_arguments();
    for (std::size_t index = 1; index < arguments.size(); ++index) {
        const auto take_value = [&]() -> std::wstring {
            return index + 1 < arguments.size() ? arguments[++index] : L"";
        };
        if (arguments[index] == L"--server") options.server = take_value();
        else if (arguments[index] == L"--token") options.token = take_value();
        else if (arguments[index] == L"--interval") options.interval_seconds = std::max(1UL, std::stoul(take_value()));
        else if (arguments[index] == L"--batch-size") options.batch_size = std::max<std::size_t>(1, std::stoull(take_value()));
        else if (arguments[index] == L"--max-queue") options.max_queue = std::max<std::size_t>(1, std::stoull(take_value()));
        else if (arguments[index] == L"--console") options.console = true;
    }
    return options;
}

std::filesystem::path data_directory() {
    wchar_t buffer[32768]{};
    const DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, static_cast<DWORD>(std::size(buffer)));
    const std::filesystem::path base = length ? std::filesystem::path(std::wstring(buffer, length)) : std::filesystem::temp_directory_path();
    return base / L"ActivityTimeline";
}

std::wstring device_id() {
    wchar_t name[MAX_COMPUTERNAME_LENGTH + 1]{};
    DWORD size = static_cast<DWORD>(std::size(name));
    if (GetComputerNameW(name, &size)) return L"windows-" + std::wstring(name, size);
    return L"windows-pc";
}

void emit_window() {
    auto& app = *g_application;
    const auto now = std::chrono::system_clock::now();
    const auto counts = app.input.take_snapshot();
    auto clipboard_events = app.clipboard.take_events();

    FeatureSnapshot snapshot;
    snapshot.start_time = app.window_start;
    snapshot.duration_ms = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(now - app.window_start).count());
    snapshot.foreground_window = GetForegroundWindow();
    snapshot.input = counts;
    snapshot.idle_ms = app.idle.idle_milliseconds();
    snapshot.clipboard_events = std::move(clipboard_events);
    app.worker->submit(std::move(snapshot));
    app.window_start = now;
}

void add_tray_icon(HWND window) {
    NOTIFYICONDATAW icon{};
    icon.cbSize = sizeof(icon);
    icon.hWnd = window;
    icon.uID = kTrayId;
    icon.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    icon.uCallbackMessage = kTrayMessage;
    icon.hIcon = LoadIconW(nullptr, IDI_INFORMATION);
    wcscpy_s(icon.szTip, L"行迹 · 隐私汇总采集中");
    Shell_NotifyIconW(NIM_ADD, &icon);
}

void remove_tray_icon(HWND window) {
    NOTIFYICONDATAW icon{};
    icon.cbSize = sizeof(icon);
    icon.hWnd = window;
    icon.uID = kTrayId;
    Shell_NotifyIconW(NIM_DELETE, &icon);
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM w_param, LPARAM l_param) {
    switch (message) {
    case WM_CLIPBOARDUPDATE:
        if (g_application) g_application->clipboard.on_update();
        return 0;
    case WM_TIMER:
        if (w_param == kSampleTimer && g_application) emit_window();
        return 0;
    case WM_COMMAND:
        if (LOWORD(w_param) == kExitCommand) DestroyWindow(window);
        return 0;
    case kTrayMessage:
        if (l_param == WM_RBUTTONUP) {
            POINT point{};
            GetCursorPos(&point);
            HMENU menu = CreatePopupMenu();
            AppendMenuW(menu, MF_STRING, kExitCommand, L"退出采集器");
            SetForegroundWindow(window);
            TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, window, nullptr);
            DestroyMenu(menu);
        }
        return 0;
    case WM_DESTROY:
        if (g_application) {
            KillTimer(window, kSampleTimer);
            g_application->clipboard.stop(window);
            g_application->input.uninstall();
        }
        remove_tray_icon(window);
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcW(window, message, w_param, l_param);
    }
}
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    HANDLE instance_mutex = CreateMutexW(nullptr, TRUE, L"Local\\ActivityTimelineCollector");
    if (!instance_mutex || GetLastError() == ERROR_ALREADY_EXISTS) {
        if (instance_mutex) CloseHandle(instance_mutex);
        return 0;
    }

    const Options options = parse_options();
    if (options.console) {
        AllocConsole();
        FILE* stream = nullptr;
        freopen_s(&stream, "CONOUT$", "w", stdout);
    }

    const auto directory = data_directory();
    Application application;
    application.interval_seconds = options.interval_seconds;
    application.worker = std::make_unique<CollectorWorker>(
        device_id(), directory / L"sequence.txt", options.server, options.token, directory / L"queue.jsonl",
        options.batch_size, options.max_queue);
    application.worker->start();
    g_application = &application;

    const wchar_t* class_name = L"ActivityTimelineCollectorWindow";
    WNDCLASSW window_class{};
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.lpszClassName = class_name;
    if (!RegisterClassW(&window_class)) return 1;

    HWND window = CreateWindowExW(0, class_name, L"Activity Timeline Collector", 0, 0, 0, 0, 0,
                                  HWND_MESSAGE, nullptr, instance, nullptr);
    if (!window) return 2;
    if (!application.input.install()) {
        DestroyWindow(window);
        return 3;
    }
    if (!application.clipboard.start(window)) {
        DestroyWindow(window);
        return 4;
    }
    SetTimer(window, kSampleTimer, options.interval_seconds * 1000, nullptr);
    add_tray_icon(window);

    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    application.worker->stop();
    g_application = nullptr;
    CloseHandle(instance_mutex);
    return static_cast<int>(message.wParam);
}
