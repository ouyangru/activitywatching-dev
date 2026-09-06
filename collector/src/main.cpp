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

#include <share.h>

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
constexpr UINT_PTR kLogRefreshTimer = 2;
constexpr UINT kTrayMessage = WM_APP + 1;
constexpr UINT kTrayId = 100;
constexpr UINT kExitCommand = 200;
constexpr UINT kAutostartCommand = 201;
constexpr UINT kViewLogCommand = 202;
constexpr UINT kClearLogCommand = 203;
constexpr UINT kOpenLogFolderCommand = 204;
// How much of collector.log the viewer renders in one shot.
constexpr std::uintmax_t kLogTailBytes = 256 * 1024;
constexpr wchar_t kCollectorClassName[] = L"ActivityTimelineCollectorWindow";
constexpr wchar_t kLogClassName[] = L"ActivityTimelineLogWindow";
constexpr wchar_t kRunKey[] = L"Software\\Microsoft\\Windows\\CurrentVersion\\Run";
constexpr wchar_t kAutostartValue[] = L"ActivityTimelineCollector";

struct Options {
    std::wstring server{L"http://localhost:8765"};
    std::wstring token;
    std::uint32_t interval_seconds{10};
    std::size_t batch_size{12};
    std::size_t max_queue{60480};
    bool console{false};
    bool show_log{false};
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

// --- Log viewer window state (all accessed from the message thread only) ---
HINSTANCE g_instance = nullptr;
HWND g_log_window = nullptr;
HWND g_log_edit = nullptr;
HFONT g_log_mono_font = nullptr;
HFONT g_log_ui_font = nullptr;
std::filesystem::path g_log_path;
// Sentinel means "never rendered"; any change (including shrink after the
// 2 MB rotation) triggers a re-render.
std::uintmax_t g_log_last_size = static_cast<std::uintmax_t>(-1);

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
        else if (arguments[index] == L"--show-log" || arguments[index] == L"--log") options.show_log = true;
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
// program path (argv[0]), --console, --show-log/--log and --autostart <value>
// are stripped.
std::wstring autostart_command() {
    std::wstring command = L"\"" + module_path() + L"\"";
    const auto arguments = command_line_arguments();
    for (std::size_t index = 1; index < arguments.size(); ++index) {
        if (arguments[index] == L"--console") continue;
        if (arguments[index] == L"--show-log" || arguments[index] == L"--log") continue;
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
    auto duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - app.window_start).count();
    // After suspend/resume the wall clock jumped while no sampling happened,
    // producing windows that span the whole sleep (hours). The backend caps
    // duration_ms at 300000 and rejects larger values with 422, which wedges
    // the upload queue. Clamp such gap windows; the large idle_ms reported
    // alongside them already tells the server the user was away.
    constexpr std::int64_t kMaxDurationMs = 300000;
    if (duration_ms > kMaxDurationMs) duration_ms = kMaxDurationMs;
    if (duration_ms <= 0) duration_ms = 1;
    snapshot.duration_ms = static_cast<std::uint64_t>(duration_ms);
    snapshot.foreground_window = GetForegroundWindow();
    snapshot.input = counts;
    snapshot.idle_ms = app.idle.idle_milliseconds();
    snapshot.clipboard_events = std::move(clipboard_events);
    app.worker->submit(std::move(snapshot));
    app.window_start = now;
}

// --- Log viewer ------------------------------------------------------------
// A plain Win32 window with a read-only monospace edit control that tails
// collector.log once per second. The worker thread appends via _SH_DENYNO, so
// reading concurrently is safe. Only the last kLogTailBytes are rendered so a
// multi-megabyte log never stalls the UI thread.

std::wstring read_log_tail(const std::filesystem::path& path) {
    FILE* file = _wfsopen(path.c_str(), L"rb", _SH_DENYNO);
    if (!file) return L"(无法打开日志文件)";
    std::string bytes;
    _fseeki64(file, 0, SEEK_END);
    const long long length = _ftelli64(file);
    if (length > 0) {
        const auto read = static_cast<std::size_t>(std::min<long long>(length, kLogTailBytes));
        _fseeki64(file, -static_cast<long long>(read), SEEK_END);
        bytes.resize(read);
        bytes.resize(fread(bytes.data(), 1, read, file));
    }
    std::fclose(file);
    if (bytes.empty()) return L"(暂无日志)";
    // We may have started mid-line; drop everything before the first newline.
    if (bytes.size() >= kLogTailBytes) {
        if (const auto first_line = bytes.find('\n'); first_line != std::string::npos)
            bytes.erase(0, first_line + 1);
    }
    const int wide_length = MultiByteToWideChar(CP_UTF8, 0, bytes.data(), static_cast<int>(bytes.size()),
                                                nullptr, 0);
    std::wstring text(wide_length > 0 ? static_cast<std::size_t>(wide_length) : 0, L'\0');
    if (wide_length > 0)
        MultiByteToWideChar(CP_UTF8, 0, bytes.data(), static_cast<int>(bytes.size()), text.data(), wide_length);
    return text;
}

void refresh_log_view(bool force) {
    if (!g_log_edit) return;
    std::error_code error;
    const auto current = std::filesystem::file_size(g_log_path, error);
    const std::uintmax_t size = error ? 0 : current;
    if (!force && size == g_log_last_size) return;
    g_log_last_size = size;

    // Keep the view pinned to the newest line only if the user was already at
    // the bottom, so scrolling back through history is not fought by refreshes.
    SCROLLINFO scroll{};
    scroll.cbSize = sizeof(scroll);
    scroll.fMask = SIF_POS | SIF_RANGE | SIF_PAGE;
    bool at_bottom = true;
    if (GetScrollInfo(g_log_edit, SB_VERT, &scroll) && scroll.nPage > 0)
        at_bottom = scroll.nPos + static_cast<int>(scroll.nPage) >= scroll.nMax - 2;

    SetWindowTextW(g_log_edit, read_log_tail(g_log_path).c_str());
    if (at_bottom) SendMessageW(g_log_edit, WM_VSCROLL, SB_BOTTOM, 0);
}

void layout_log_window(HWND window) {
    RECT client{};
    GetClientRect(window, &client);
    const int bar_height = 44;
    if (g_log_edit)
        MoveWindow(g_log_edit, 0, 0, client.right, std::max<int>(0, client.bottom - bar_height), TRUE);
    if (HWND clear_button = GetDlgItem(window, kClearLogCommand))
        MoveWindow(clear_button, 10, client.bottom - bar_height + 9, 104, 28, TRUE);
    if (HWND folder_button = GetDlgItem(window, kOpenLogFolderCommand))
        MoveWindow(folder_button, 122, client.bottom - bar_height + 9, 140, 28, TRUE);
    if (HWND path_label = GetDlgItem(window, 1003)) {
        MoveWindow(path_label, 276, client.bottom - bar_height + 15, std::max<int>(0, client.right - 284), 20, TRUE);
        SetWindowTextW(path_label, g_log_path.c_str());
    }
}

LRESULT CALLBACK log_window_proc(HWND window, UINT message, WPARAM w_param, LPARAM l_param) {
    switch (message) {
    case WM_CREATE: {
        const auto* create = reinterpret_cast<CREATESTRUCTW*>(l_param);
        g_log_mono_font = CreateFontW(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                      CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, L"Consolas");
        g_log_ui_font = CreateFontW(-14, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                    CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
                                    L"Microsoft YaHei UI");
        g_log_edit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", nullptr,
                                     WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_HSCROLL | ES_MULTILINE | ES_READONLY |
                                         ES_AUTOVSCROLL | ES_AUTOHSCROLL,
                                     0, 0, 0, 0, window, reinterpret_cast<HMENU>(1000), create->hInstance, nullptr);
        CreateWindowExW(0, L"BUTTON", L"清空日志", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 0, 0, window,
                        reinterpret_cast<HMENU>(kClearLogCommand), create->hInstance, nullptr);
        CreateWindowExW(0, L"BUTTON", L"打开日志文件夹", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 0, 0, window,
                        reinterpret_cast<HMENU>(kOpenLogFolderCommand), create->hInstance, nullptr);
        CreateWindowExW(0, L"STATIC", nullptr, WS_CHILD | WS_VISIBLE | SS_ENDELLIPSIS, 0, 0, 0, 0, window,
                        reinterpret_cast<HMENU>(1003), create->hInstance, nullptr);
        if (g_log_edit) SendMessageW(g_log_edit, WM_SETFONT, reinterpret_cast<WPARAM>(g_log_mono_font), TRUE);
        for (const int id : {static_cast<int>(kClearLogCommand), static_cast<int>(kOpenLogFolderCommand), 1003}) {
            if (HWND control = GetDlgItem(window, id))
                SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(g_log_ui_font), TRUE);
        }
        SetTimer(window, kLogRefreshTimer, 1000, nullptr);
        refresh_log_view(true);
        layout_log_window(window);
        return 0;
    }
    case WM_TIMER:
        if (w_param == kLogRefreshTimer) refresh_log_view(false);
        return 0;
    case WM_SIZE:
        layout_log_window(window);
        return 0;
    case WM_COMMAND:
        switch (LOWORD(w_param)) {
        case kClearLogCommand:
            diagnostics::clear();
            refresh_log_view(true);
            return 0;
        case kOpenLogFolderCommand: {
            std::error_code error;
            if (const auto folder = g_log_path.parent_path(); std::filesystem::exists(folder, error))
                ShellExecuteW(window, L"open", folder.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
            return 0;
        }
        default:
            return 0;
        }
    case WM_DESTROY:
        KillTimer(window, kLogRefreshTimer);
        if (g_log_mono_font) {
            DeleteObject(g_log_mono_font);
            g_log_mono_font = nullptr;
        }
        if (g_log_ui_font) {
            DeleteObject(g_log_ui_font);
            g_log_ui_font = nullptr;
        }
        g_log_edit = nullptr;
        g_log_window = nullptr;
        g_log_last_size = static_cast<std::uintmax_t>(-1);
        return 0;
    default:
        return DefWindowProcW(window, message, w_param, l_param);
    }
}

void show_log_window() {
    if (g_log_window) {
        ShowWindow(g_log_window, SW_RESTORE);
        SetForegroundWindow(g_log_window);
        return;
    }
    g_log_window = CreateWindowExW(0, kLogClassName,
                                   L"行迹采集器 · 日志", WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 900, 540,
                                   nullptr, nullptr, g_instance, nullptr);
    if (g_log_window) ShowWindow(g_log_window, SW_SHOW);
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
        if (LOWORD(w_param) == kViewLogCommand) show_log_window();
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
            AppendMenuW(menu, MF_STRING, kViewLogCommand, L"查看日志");
            AppendMenuW(menu, MF_STRING | (autostart_enabled() ? MF_CHECKED : 0U), kAutostartCommand, L"开机自启");
            AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
            AppendMenuW(menu, MF_STRING, kExitCommand, L"退出采集器");
            SetForegroundWindow(window);
            TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, window, nullptr);
            DestroyMenu(menu);
        }
        if (l_param == WM_LBUTTONDBLCLK) show_log_window();
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

    // "collector --show-log" is a viewer request, not a second collector: if
    // an instance is already running, ask it to surface its log window.
    if (options.show_log) {
        if (HWND existing = FindWindowW(kCollectorClassName, nullptr)) {
            PostMessageW(existing, WM_COMMAND, MAKEWPARAM(kViewLogCommand, 0), 0);
            return 0;
        }
        // No running instance: fall through and start one, opening the viewer.
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
    g_instance = instance;
    g_log_path = directory / L"collector.log";

    WNDCLASSW window_class{};
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.lpszClassName = kCollectorClassName;
    if (!RegisterClassW(&window_class)) return 1;

    WNDCLASSW log_class{};
    log_class.lpfnWndProc = log_window_proc;
    log_class.hInstance = instance;
    log_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    log_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_BTNFACE + 1);
    log_class.lpszClassName = kLogClassName;
    if (!RegisterClassW(&log_class)) return 1;

    HWND window = CreateWindowExW(0, kCollectorClassName, L"Activity Timeline Collector", 0, 0, 0, 0, 0,
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
    if (options.show_log) show_log_window();

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
