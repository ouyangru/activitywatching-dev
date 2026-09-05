package com.ouyangru.activitytimeline;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import java.util.Locale;

final class SettingsStore {
    private static final String PREFS = "activity_timeline";
    private static final String SERVER_URL = "server_url";
    private static final String API_TOKEN = "api_token";
    private static final String DEVICE_ID = "device_id";
    private static final String MONITORING_ENABLED = "monitoring_enabled";
    private static final String NEXT_SEQUENCE = "next_sequence";
    private static final String QUERY_CURSOR = "query_cursor";
    private static final String CURRENT_PACKAGE = "current_package";
    private static final String CURRENT_LABEL = "current_label";
    private static final String CURRENT_START = "current_start";
    private static final String SCREEN_INTERACTIVE = "screen_interactive";
    private static final String IDLE_START = "idle_start";
    private static final String LAST_STATUS = "last_status";
    private static final String LAST_SYNC = "last_sync";

    private final SharedPreferences preferences;

    SettingsStore(Context context) {
        preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    String serverUrl() {
        return preferences.getString(SERVER_URL, "");
    }

    String apiToken() {
        return preferences.getString(API_TOKEN, "");
    }

    String deviceId(Context context) {
        String saved = preferences.getString(DEVICE_ID, "");
        if (saved != null && !saved.isBlank()) {
            return saved;
        }
        String androidId = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID);
        String suffix = androidId == null ? "phone" : androidId.substring(Math.max(0, androidId.length() - 8));
        return "android-" + suffix.toLowerCase(Locale.ROOT);
    }

    void saveConnection(String serverUrl, String apiToken, String deviceId, boolean enabled) {
        String normalizedUrl = serverUrl.trim();
        while (normalizedUrl.endsWith("/")) {
            normalizedUrl = normalizedUrl.substring(0, normalizedUrl.length() - 1);
        }
        preferences.edit()
            .putString(SERVER_URL, normalizedUrl)
            .putString(API_TOKEN, apiToken.trim())
            .putString(DEVICE_ID, deviceId.trim())
            .putBoolean(MONITORING_ENABLED, enabled)
            .apply();
    }

    boolean monitoringEnabled() {
        return preferences.getBoolean(MONITORING_ENABLED, false);
    }

    synchronized long nextSequence() {
        long value = preferences.getLong(NEXT_SEQUENCE, 1L);
        preferences.edit().putLong(NEXT_SEQUENCE, value + 1L).commit();
        return value;
    }

    CollectorState collectorState(long now) {
        long cursor = preferences.getLong(QUERY_CURSOR, 0L);
        if (cursor <= 0L || cursor > now) {
            return new CollectorState(Math.max(0L, now - 86_400_000L), null, null, 0L, true, 0L);
        }
        String currentPackage = preferences.getString(CURRENT_PACKAGE, null);
        String currentLabel = preferences.getString(CURRENT_LABEL, null);
        return new CollectorState(
            cursor,
            currentPackage == null || currentPackage.isBlank() ? null : currentPackage,
            currentLabel,
            preferences.getLong(CURRENT_START, 0L),
            preferences.getBoolean(SCREEN_INTERACTIVE, true),
            preferences.getLong(IDLE_START, 0L)
        );
    }

    void saveCollectorState(CollectorState state) {
        SharedPreferences.Editor editor = preferences.edit()
            .putLong(QUERY_CURSOR, state.cursor)
            .putLong(CURRENT_START, state.currentStart)
            .putBoolean(SCREEN_INTERACTIVE, state.screenInteractive)
            .putLong(IDLE_START, state.idleStart);
        if (state.currentPackage == null) {
            editor.remove(CURRENT_PACKAGE).remove(CURRENT_LABEL);
        } else {
            editor.putString(CURRENT_PACKAGE, state.currentPackage)
                .putString(CURRENT_LABEL, state.currentLabel == null ? state.currentPackage : state.currentLabel);
        }
        editor.commit();
    }

    void setRuntimeStatus(String status, boolean synced) {
        SharedPreferences.Editor editor = preferences.edit().putString(LAST_STATUS, status);
        if (synced) {
            editor.putLong(LAST_SYNC, System.currentTimeMillis());
        }
        editor.apply();
    }

    String lastStatus() {
        return preferences.getString(LAST_STATUS, "尚未执行采集");
    }

    long lastSync() {
        return preferences.getLong(LAST_SYNC, 0L);
    }

    static final class CollectorState {
        long cursor;
        String currentPackage;
        String currentLabel;
        long currentStart;
        boolean screenInteractive;
        long idleStart;

        CollectorState(
            long cursor,
            String currentPackage,
            String currentLabel,
            long currentStart,
            boolean screenInteractive,
            long idleStart
        ) {
            this.cursor = cursor;
            this.currentPackage = currentPackage;
            this.currentLabel = currentLabel;
            this.currentStart = currentStart;
            this.screenInteractive = screenInteractive;
            this.idleStart = idleStart;
        }
    }
}
