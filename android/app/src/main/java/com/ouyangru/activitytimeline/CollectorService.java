package com.ouyangru.activitytimeline;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;

/**
 * 前台采集服务：绕开国产 ROM 对纯后台 WorkManager 任务的冻结与限频。
 * 内部每分钟采集一次并即时上传；WorkManager 周期任务保留作为兜底，
 * 即使服务被系统杀死，任务重新调度后仍能按游标补齐断档数据。
 */
public final class CollectorService extends Service {
    static final String ACTION_START = "com.ouyangru.activitytimeline.START";
    private static final String CHANNEL_ID = "activity_timeline_collector";
    private static final int NOTIFICATION_ID = 1001;
    private static final long INTERVAL_MS = 60_000L;

    private HandlerThread workerThread;
    private Handler worker;

    static void start(Context context) {
        Intent intent = new Intent(context, CollectorService.class);
        intent.setAction(ACTION_START);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, CollectorService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        Notification notification = buildNotification("正在记录前台应用");
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        SettingsStore settings = new SettingsStore(this);
        if (!settings.monitoringEnabled()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        startLoop();
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopLoop();
        super.onDestroy();
    }

    private synchronized void startLoop() {
        if (workerThread != null && workerThread.isAlive()) {
            return;
        }
        workerThread = new HandlerThread("collector-loop");
        workerThread.start();
        worker = new Handler(workerThread.getLooper());
        worker.post(this::cycle);
    }

    private synchronized void stopLoop() {
        if (worker != null) {
            worker.removeCallbacksAndMessages(null);
            worker = null;
        }
        if (workerThread != null) {
            workerThread.quitSafely();
            workerThread = null;
        }
    }

    private void cycle() {
        SettingsStore settings = new SettingsStore(this);
        if (!settings.monitoringEnabled()) {
            stopSelf();
            return;
        }
        try (QueueDatabase queue = new QueueDatabase(this)) {
            int collected = UsageCollector.collect(this, queue);
            ApiClient.UploadResult upload = ApiClient.uploadPending(queue, settings);
            int pending = queue.pendingCount();
            settings.setRuntimeStatus(
                "前台服务本次采集 " + collected + " 条；" + upload.message + "；待发送 " + pending + " 条",
                upload.success
            );
            ApiClient.heartbeat(settings.serverUrl(), settings.apiToken(), settings.deviceId(this));
            updateNotification("待发送 " + pending + " 条 · " + upload.message);
        } catch (SecurityException error) {
            settings.setRuntimeStatus("等待授予使用情况访问权限", false);
            updateNotification("等待使用情况访问权限");
        } catch (Exception error) {
            String message = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
            settings.setRuntimeStatus("前台采集失败：" + message, false);
            updateNotification("采集失败，将在下轮重试");
        }
        if (worker != null) {
            worker.postDelayed(this::cycle, INTERVAL_MS);
        }
    }

    private void createChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription(getString(R.string.notification_channel_description));
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private Notification buildNotification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent contentIntent = PendingIntent.getActivity(this, 0, open, flags);
        return new Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_recent_history)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(text)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build();
    }

    private void updateNotification(String text) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify(NOTIFICATION_ID, buildNotification(text));
        }
    }
}
