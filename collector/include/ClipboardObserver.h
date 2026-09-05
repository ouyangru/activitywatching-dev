#pragma once

#include "FeatureWindow.h"

#include <Windows.h>

#include <mutex>
#include <vector>

class ClipboardObserver {
public:
    bool start(HWND window);
    void stop(HWND window);
    void on_update();
    [[nodiscard]] std::vector<ClipboardMetadata> take_events();

private:
    std::mutex mutex_;
    std::vector<ClipboardMetadata> events_;
};

