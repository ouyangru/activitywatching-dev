#pragma once

#include <filesystem>
#include <string>

// Lightweight append-only diagnostic log for a background tray application.
// Every line is flushed on write so the log survives crashes and hangs; the
// file is truncated once it exceeds ~2 MB so it can never grow unbounded.
// The collector is a silent background process - without this log there is
// no way to tell "working" from "frozen" after the fact.
namespace diagnostics {

void initialize(std::filesystem::path log_path);
void write(const std::string& message);
// Truncate the log file (opened with _SH_DENYNO, so this is safe while the
// worker thread keeps appending).
void clear();

}  // namespace diagnostics
