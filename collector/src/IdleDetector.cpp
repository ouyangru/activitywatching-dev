#include "IdleDetector.h"

#include <Windows.h>

std::uint64_t IdleDetector::idle_milliseconds() const {
    LASTINPUTINFO info{};
    info.cbSize = sizeof(info);
    if (!GetLastInputInfo(&info)) {
        return 0;
    }
    // Both values are DWORD ticks. Unsigned subtraction intentionally handles
    // the roughly 49-day GetTickCount wraparound.
    return static_cast<std::uint64_t>(GetTickCount() - info.dwTime);
}
