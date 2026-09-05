#include "ClipboardObserver.h"

#include <algorithm>
#include <string>

namespace {
std::wstring bucket_for(std::size_t length) {
    if (length == 0) return L"empty";
    if (length <= 32) return L"1-32";
    if (length <= 256) return L"33-256";
    if (length <= 2048) return L"257-2048";
    return L"2049+";
}
}

bool ClipboardObserver::start(HWND window) {
    return AddClipboardFormatListener(window) != FALSE;
}

void ClipboardObserver::stop(HWND window) {
    RemoveClipboardFormatListener(window);
}

void ClipboardObserver::on_update() {
    ClipboardMetadata metadata{L"other", L"empty", std::chrono::system_clock::now()};

    if (IsClipboardFormatAvailable(CF_HDROP)) {
        metadata.kind = L"files";
        metadata.length_bucket = L"1-32";
    } else if (IsClipboardFormatAvailable(CF_DIB) || IsClipboardFormatAvailable(CF_BITMAP)) {
        metadata.kind = L"image";
        metadata.length_bucket = L"1-32";
    } else if (IsClipboardFormatAvailable(CF_UNICODETEXT) && OpenClipboard(nullptr)) {
        metadata.kind = L"text";
        const HANDLE handle = GetClipboardData(CF_UNICODETEXT);
        if (handle) {
            // The allocated byte size is sufficient for a privacy-preserving
            // length bucket and avoids scanning potentially huge text buffers.
            const auto bytes = GlobalSize(handle);
            metadata.length_bucket = bucket_for(bytes / sizeof(wchar_t));
        }
        CloseClipboard();
    }

    std::lock_guard lock(mutex_);
    events_.push_back(std::move(metadata));
}

std::vector<ClipboardMetadata> ClipboardObserver::take_events() {
    std::lock_guard lock(mutex_);
    std::vector<ClipboardMetadata> snapshot;
    snapshot.swap(events_);
    return snapshot;
}
