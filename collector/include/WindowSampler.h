#pragma once

#include "FeatureWindow.h"

#include <Windows.h>

class WindowSampler {
public:
    [[nodiscard]] WindowContext sample(HWND foreground = nullptr) const;
};
