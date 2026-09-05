#pragma once

#include <cstdint>

class IdleDetector {
public:
    [[nodiscard]] std::uint64_t idle_milliseconds() const;
};

