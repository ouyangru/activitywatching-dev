#include "InputAggregator.h"

bool InputAggregator::install() {
    const HINSTANCE module = GetModuleHandleW(nullptr);
    keyboard_hook_ = SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_proc, module, 0);
    mouse_hook_ = SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, module, 0);
    if (keyboard_hook_ == nullptr || mouse_hook_ == nullptr) {
        uninstall();
        return false;
    }
    return true;
}

void InputAggregator::uninstall() {
    if (keyboard_hook_) {
        UnhookWindowsHookEx(keyboard_hook_);
        keyboard_hook_ = nullptr;
    }
    if (mouse_hook_) {
        UnhookWindowsHookEx(mouse_hook_);
        mouse_hook_ = nullptr;
    }
}

InputCounts InputAggregator::take_snapshot() {
    return {
        keys_.exchange(0, std::memory_order_relaxed),
        clicks_.exchange(0, std::memory_order_relaxed),
        scrolls_.exchange(0, std::memory_order_relaxed),
        pastes_.exchange(0, std::memory_order_relaxed),
    };
}

LRESULT CALLBACK InputAggregator::keyboard_proc(int code, WPARAM w_param, LPARAM l_param) {
    if (code == HC_ACTION) {
        const auto* keyboard = reinterpret_cast<const KBDLLHOOKSTRUCT*>(l_param);
        const bool key_down = w_param == WM_KEYDOWN || w_param == WM_SYSKEYDOWN;
        const bool key_up = w_param == WM_KEYUP || w_param == WM_SYSKEYUP;
        const bool is_control = keyboard->vkCode == VK_CONTROL || keyboard->vkCode == VK_LCONTROL ||
                                keyboard->vkCode == VK_RCONTROL;
        if (is_control && key_down) control_down_.store(true, std::memory_order_relaxed);
        if (is_control && key_up) control_down_.store(false, std::memory_order_relaxed);
        if (key_down) {
            keys_.fetch_add(1, std::memory_order_relaxed);
            if (keyboard->vkCode == 'V' && control_down_.load(std::memory_order_relaxed)) {
                pastes_.fetch_add(1, std::memory_order_relaxed);
            }
        }
    }
    return CallNextHookEx(nullptr, code, w_param, l_param);
}

LRESULT CALLBACK InputAggregator::mouse_proc(int code, WPARAM w_param, LPARAM l_param) {
    (void)l_param;
    if (code == HC_ACTION) {
        if (w_param == WM_LBUTTONDOWN || w_param == WM_RBUTTONDOWN || w_param == WM_MBUTTONDOWN ||
            w_param == WM_XBUTTONDOWN) {
            clicks_.fetch_add(1, std::memory_order_relaxed);
        } else if (w_param == WM_MOUSEWHEEL || w_param == WM_MOUSEHWHEEL) {
            scrolls_.fetch_add(1, std::memory_order_relaxed);
        }
    }
    return CallNextHookEx(nullptr, code, w_param, l_param);
}
