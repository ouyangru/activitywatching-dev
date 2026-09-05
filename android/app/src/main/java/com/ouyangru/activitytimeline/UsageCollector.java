package com.ouyangru.activitytimeline;

import android.app.usage.UsageEvents;
import android.app.usage.UsageStatsManager;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;

final class UsageCollector {
    private static final Object COLLECTION_LOCK = new Object();

    private UsageCollector() {}

    static int collect(Context context, QueueDatabase queue) throws Exception {
        synchronized (COLLECTION_LOCK) {
            if (!UsageAccess.isGranted(context)) {
                throw new SecurityException("尚未授予使用情况访问权限");
            }

            long now = System.currentTimeMillis();
            SettingsStore settings = new SettingsStore(context);
            SettingsStore.CollectorState state = settings.collectorState(now);
            UsageStatsManager manager = context.getSystemService(UsageStatsManager.class);
            UsageEvents usageEvents = manager.queryEvents(state.cursor, now);
            if (usageEvents == null) {
                throw new IllegalStateException("系统暂时无法返回使用记录，请解锁手机后重试");
            }

            List<FeatureEvent> collected = new ArrayList<>();
            UsageEvents.Event event = new UsageEvents.Event();
            while (usageEvents.hasNextEvent()) {
                usageEvents.getNextEvent(event);
                long time = Math.max(state.cursor, Math.min(now, event.getTimeStamp()));
                switch (event.getEventType()) {
                    case UsageEvents.Event.ACTIVITY_RESUMED:
                        onAppResumed(context, settings, state, collected, event.getPackageName(), time);
                        break;
                    case UsageEvents.Event.SCREEN_NON_INTERACTIVE:
                        onScreenOff(settings, state, collected, time);
                        break;
                    case UsageEvents.Event.SCREEN_INTERACTIVE:
                        onScreenOn(settings, state, collected, time);
                        break;
                    default:
                        // Paused events are intentionally ignored. During an in-app Activity transition,
                        // Android can pause the old page after resuming the new page; treating that as an
                        // app exit would create false gaps.
                        break;
                }
            }

            if (state.screenInteractive) {
                finishCurrentAppChunk(settings, state, collected, now);
            } else {
                finishIdleChunk(settings, state, collected, now);
            }
            state.cursor = now;

            queue.enqueue(collected, settings.deviceId(context));
            settings.saveCollectorState(state);
            return collected.size();
        }
    }

    private static void onAppResumed(
        Context context,
        SettingsStore settings,
        SettingsStore.CollectorState state,
        List<FeatureEvent> target,
        String packageName,
        long time
    ) {
        if (packageName == null || packageName.isBlank()) {
            return;
        }
        if (!state.screenInteractive) {
            finishIdleChunk(settings, state, target, time);
            state.screenInteractive = true;
        }
        if (packageName.equals(state.currentPackage)) {
            if (state.currentStart <= 0L) {
                state.currentStart = time;
            }
            return;
        }
        finishCurrentAppChunk(settings, state, target, time);
        state.currentPackage = truncate(packageName, 260);
        state.currentLabel = truncate(resolveAppLabel(context, packageName), 2048);
        state.currentStart = time;
    }

    private static void finishCurrentAppChunk(
        SettingsStore settings,
        SettingsStore.CollectorState state,
        List<FeatureEvent> target,
        long end
    ) {
        if (state.currentPackage != null && state.currentStart > 0L && end > state.currentStart) {
            FeatureEvent.appendChunks(
                target,
                settings,
                state.currentStart,
                end,
                state.currentPackage,
                state.currentLabel == null ? state.currentPackage : state.currentLabel,
                false
            );
        }
        state.currentStart = end;
    }

    private static void finishIdleChunk(
        SettingsStore settings,
        SettingsStore.CollectorState state,
        List<FeatureEvent> target,
        long end
    ) {
        if (state.idleStart > 0L && end > state.idleStart) {
            FeatureEvent.appendChunks(
                target,
                settings,
                state.idleStart,
                end,
                "__screen_off__",
                "手机屏幕关闭",
                true
            );
        }
        state.idleStart = end;
    }

    private static void finishCurrentAppAndClear(
        SettingsStore settings,
        SettingsStore.CollectorState state,
        List<FeatureEvent> target,
        long end
    ) {
        finishCurrentAppChunk(settings, state, target, end);
        state.currentPackage = null;
        state.currentLabel = null;
        state.currentStart = 0L;
    }

    private static void onScreenOff(
        SettingsStore settings,
        SettingsStore.CollectorState state,
        List<FeatureEvent> target,
        long time
    ) {
        if (!state.screenInteractive) {
            finishIdleChunk(settings, state, target, time);
            return;
        }
        finishCurrentAppAndClear(settings, state, target, time);
        state.screenInteractive = false;
        state.idleStart = time;
    }

    private static void onScreenOn(
        SettingsStore settings,
        SettingsStore.CollectorState state,
        List<FeatureEvent> target,
        long time
    ) {
        if (state.screenInteractive) {
            return;
        }
        finishIdleChunk(settings, state, target, time);
        state.screenInteractive = true;
        state.currentStart = 0L;
    }

    private static String resolveAppLabel(Context context, String packageName) {
        PackageManager packageManager = context.getPackageManager();
        try {
            ApplicationInfo info;
            if (Build.VERSION.SDK_INT >= 33) {
                info = packageManager.getApplicationInfo(
                    packageName,
                    PackageManager.ApplicationInfoFlags.of(0)
                );
            } else {
                @SuppressWarnings("deprecation")
                ApplicationInfo legacyInfo = packageManager.getApplicationInfo(packageName, 0);
                info = legacyInfo;
            }
            CharSequence label = packageManager.getApplicationLabel(info);
            return label == null || label.toString().isBlank() ? packageName : label.toString();
        } catch (PackageManager.NameNotFoundException ignored) {
            return packageName;
        }
    }

    private static String truncate(String value, int maxLength) {
        return value.length() <= maxLength ? value : value.substring(0, maxLength);
    }
}
