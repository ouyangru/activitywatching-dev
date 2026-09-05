package com.ouyangru.activitytimeline;

import android.content.Context;

import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

public final class SyncWorker extends Worker {
    public SyncWorker(@NonNull Context context, @NonNull WorkerParameters parameters) {
        super(context, parameters);
    }

    @NonNull
    @Override
    public Result doWork() {
        Context context = getApplicationContext();
        SettingsStore settings = new SettingsStore(context);
        if (!settings.monitoringEnabled()) {
            return Result.success();
        }
        if (!UsageAccess.isGranted(context)) {
            settings.setRuntimeStatus("等待授予使用情况访问权限", false);
            return Result.failure();
        }

        try (QueueDatabase queue = new QueueDatabase(context)) {
            int collected = UsageCollector.collect(context, queue);
            ApiClient.UploadResult upload = ApiClient.uploadPending(queue, settings);
            int pending = queue.pendingCount();
            String status = "本次采集 " + collected + " 条；" + upload.message + "；待发送 " + pending + " 条";
            settings.setRuntimeStatus(status, upload.success);
            if (upload.success) {
                return Result.success();
            }
            return upload.shouldRetry ? Result.retry() : Result.failure();
        } catch (SecurityException error) {
            settings.setRuntimeStatus(error.getMessage(), false);
            return Result.failure();
        } catch (Exception error) {
            String message = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
            settings.setRuntimeStatus("采集失败：" + message, false);
            return Result.retry();
        }
    }
}
