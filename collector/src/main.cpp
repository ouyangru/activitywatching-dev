#include "BatchUploader.h"
#include "ClipboardObserver.h"
#include "CollectorWorker.h"
#include "Diagnostics.h"
#include "FeatureWindow.h"
#include "IdleDetector.h"
#include "InputAggregator.h"
#include "WindowSampler.h"

#include <Windows.h>
#include <shellapi.h>
#include <shlobj.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#ifndef ACTIVITY_COLLECTOR_VERSION
#define ACTIVITY_COLLECTOR_VERSION "dev"
#endif

namespace {
constexpr UINT_PTR kSampleTimer = 1;
constexpr UINT kTrayMessage = WM_APP + 1;
constexpr UINT kTrayId = 100;
constexpr UINT kExitCommand = 200;
constexpr UINT kAutostartCommand = 201;
constexpr wchar_t kRunKey[] = L"Software\\Microsoft\\Windows\\CurrentVersion\\Run";
constexpr wchar_t kAutostartValue[] = L"ActivityTimelineCollector";

struct Options {
    std::wstring server{L"http://localhost:8765"};
    std::wstring token;
    std::uint32_t interval_seconds{10};
    std::size_t batch_size{12};
    std::size_t max_queue{60480};
    bool console{false};
    int autostart_action{0}; // 0 = none, 1 = enable, -1 = disable
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
        else if (arguments[index] == L"--autostart") {
            const auto value = take_value();
            if (value == L"on") options.autostart_action = 1;
            else if (value == L"off") options.autostart_action = -1;
        }
    }
    return options;
}

std::string narrow(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0,
                                         nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr,
                        nullptr);
    return result;
}

std::filesystem::path data_directory() {
    // Prefer the known-folder API over the LOCALAPPDATA environment variable:
    // environment blocks are stripped in some launch contexts (services,
    // scheduled tasks), and the temp-directory fallback can then resolve to a
    // location the user cannot write to, where every ofstream open fails
    // silently.
    PWSTR base = nullptr;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &base))) {
        std::filesystem::path directory = std::filesystem::path(base) / L"ActivityTimeline";
        CoTaskMemFree(base);
        return directory;
    }
    wchar_t buffer[32768]{};
    const DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, static_cast<DWORD>(std::size(buffer)));
    const std::filesystem::path fallback =
        length ? std::filesystem::path(std::wstring(buffer, length)) : std::filesystem::temp_directory_path();
    return fallback / L"ActivityTimeline";
}

std::wstring device_id() {
    wchar_t name[MAX_COMPUTERNAME_LENGTH + 1]{};
    DWORD size = static_cast<DWORD>(std::size(name));
    if (GetComputerNameW(name, &size)) return L"windows-" + std::wstring(name, size);
    return L"windows-pc";
}

std::wstring module_path() {
    wchar_t buffer[32768]{};
    const DWORD length = GetModuleFileNameW(nullptr, buffer, static_cast<DWORD>(std::size(buffer)));
    return length ? std::wstring(buffer, length) : std::wstring();
}

// The autostart command preserves the current --server/--token/... arguments
// so a boot-time launch talks to the same backend as this manual run. The
// program path (argv[0]), --console and --autostart <value> are stripped.
std::wstring autostart_command() {
    std::wstring command = L"\"" + module_path() + L"\"";
    const auto arguments = command_line_arguments();
    for (std::size_t index = 1; index < arguments.size(); ++index) {
        if (arguments[index] == L"--console") continue;
        if (arguments[index].rfind(L"--autostart", 0) == 0) {
            if (index + 1 < arguments.size()) ++index; // skip the on/off value
            continue;
        }
        if (arguments[index].find_first_of(L" \t") != std::wstring::npos) {
            command += L" \"" + arguments[index] + L"\"";
        } else {
            command += L" " + arguments[index];
        }
    }
    return command;
}

bool autostart_enabled() {
    wchar_t buffer[32768]{};
    DWORD size = sizeof(buffer);
    return RegGetValueW(HKEY_CURRENT_USER, kRunKey, kAutostartValue, RRF_RT_REG_SZ, nullptr, buffer, &size) == ERROR_SUCCESS;
}

bool set_autostart(bool enable) {
    if (!enable) {
        const LSTATUS status = RegDeleteKeyValueW(HKEY_CURRENT_USER, kRunKey, kAutostartValue);
        return status == ERROR_SUCCESS || status == ERROR_FILE_NOT_FOUND;
    }
    const std::wstring command = autostart_command();
    return RegSetKeyValueW(HKEY_CURRENT_USER, kRunKey, kAutostartValue, REG_SZ, command.c_str(),
                           static_cast<DWORD>((command.size() + 1) * sizeof(wchar_t))) == ERROR_SUCCESS;
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
        if (LOWORD(w_param) == kAutostartCommand) {
            const bool enable = !autostart_enabled();
            if (set_autostart(enable)) {
                OutputDebugStringW(enable ? L"ActivityTimeline: autostart enabled.\n"
                                          : L"ActivityTimeline: autostart disabled.\n");
            } else {
                MessageBoxW(window, L"修改开机自启注册表项失败。", L"行迹采集器", MB_ICONERROR | MB_OK);
            }
        }
        if (LOWORD(w_param) == kExitCommand) DestroyWindow(window);
        return 0;
    case kTrayMessage:
        if (l_param == WM_RBUTTONUP) {
            POINT point{};
            GetCursorPos(&point);
            HMENU menu = CreatePopupMenu();
            AppendMenuW(menu, MF_STRING | (autostart_enabled() ? MF_CHECKED : 0U), kAutostartCommand, L"开机自启");
            AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
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
    const Options options = parse_options();
    if (options.console) {
        AllocConsole();
        FILE* stream = nullptr;
        freopen_s(&stream, "CONOUT$", "w", stdout);
    }

    // Scriptable autostart management runs before the single-instance check so
    // it keeps working while another instance is already collecting.
    if (options.autostart_action != 0) {
        if (!set_autostart(options.autostart_action > 0)) return 5;
        if (options.console) {
            std::printf("autostart %s\n", options.autostart_action > 0 ? "on" : "off");
        }
        return 0;
    }

    HANDLE instance_mutex = CreateMutexW(nullptr, TRUE, L"Local\\ActivityTimelineCollector");
    if (!instance_mutex || GetLastError() == ERROR_ALREADY_EXISTS) {
        if (instance_mutex) CloseHandle(instance_mutex);
        diagnostics::write("another instance is already running; exiting.");
        return 0;
    }

    const auto directory = data_directory();
    diagnostics::initialize(directory / L"collector.log");
    diagnostics::write(std::string("collector ") + ACTIVITY_COLLECTOR_VERSION +
                       " starting: pid=" + std::to_string(GetCurrentProcessId()) + " server=" + narrow(options.server) +
                       " token=" + (options.token.empty() ? "none" : "set") +
                       " interval=" + std::to_string(options.interval_seconds) + "s state=" +
                       narrow((directory / L"sequence.txt").wstring()) + " queue=" +
                       narrow((directory / L"queue.jsonl").wstring()));
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
    diagnostics::write("collector exiting cleanly");
    CloseHandle(instance_mutex);
    return static_cast<int>(message.wParam);
}
