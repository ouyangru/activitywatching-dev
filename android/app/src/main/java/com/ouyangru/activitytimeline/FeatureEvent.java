package com.ouyangru.activitytimeline;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.time.Instant;

final class FeatureEvent {
    private static final long MAX_DURATION_MS = 300_000L;

    final long sequence;
    final long startTime;
    final int durationMs;
    final String process;
    final String title;
    final int idleMs;

    FeatureEvent(long sequence, long startTime, int durationMs, String process, String title, int idleMs) {
        this.sequence = sequence;
        this.startTime = startTime;
        this.durationMs = durationMs;
        this.process = process;
        this.title = title;
        this.idleMs = idleMs;
    }

    static void appendChunks(
        java.util.List<FeatureEvent> target,
        SettingsStore settings,
        long start,
        long end,
        String process,
        String title,
        boolean idle
    ) {
        long cursor = start;
        while (cursor < end) {
            int duration = (int) Math.min(MAX_DURATION_MS, end - cursor);
            if (duration > 0) {
                target.add(new FeatureEvent(
                    settings.nextSequence(),
                    cursor,
                    duration,
                    process,
                    title,
                    idle ? duration : 0
                ));
            }
            cursor += duration;
        }
    }

    JSONObject toJson(String deviceId) throws JSONException {
        JSONObject context = new JSONObject()
            .put("process", process)
            .put("window_title", title);
        JSONObject interaction = new JSONObject()
            .put("key_count", 0)
            .put("mouse_click_count", 0)
            .put("scroll_count", 0)
            .put("idle_ms", idleMs)
            .put("clipboard_copy_count", 0)
            .put("clipboard_paste_count", 0)
            .put("clipboard_events", new JSONArray());
        return new JSONObject()
            .put("platform", "android")
            .put("device_id", deviceId)
            .put("sequence", sequence)
            .put("start_time", Instant.ofEpochMilli(startTime).toString())
            .put("duration_ms", durationMs)
            .put("context", context)
            .put("interaction", interaction);
    }
}
