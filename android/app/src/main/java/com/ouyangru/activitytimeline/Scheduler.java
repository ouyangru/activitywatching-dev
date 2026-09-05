package com.ouyangru.activitytimeline;

import android.content.Context;

import androidx.work.BackoffPolicy;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import java.util.concurrent.TimeUnit;

final class Scheduler {
    private static final String PERIODIC_WORK = "activity-timeline-periodic-sync";
    private static final String IMMEDIATE_WORK = "activity-timeline-immediate-sync";

    private Scheduler() {}

    static void configure(Context context, boolean enabled) {
        WorkManager manager = WorkManager.getInstance(context);
        if (!enabled) {
            manager.cancelUniqueWork(PERIODIC_WORK);
            manager.cancelUniqueWork(IMMEDIATE_WORK);
            return;
        }

        PeriodicWorkRequest request = new PeriodicWorkRequest.Builder(
            SyncWorker.class,
            15,
            TimeUnit.MINUTES
        ).setBackoffCriteria(
            BackoffPolicy.EXPONENTIAL,
            30,
            TimeUnit.SECONDS
        ).build();
        manager.enqueueUniquePeriodicWork(PERIODIC_WORK, ExistingPeriodicWorkPolicy.UPDATE, request);
    }

    static void syncNow(Context context) {
        OneTimeWorkRequest request = new OneTimeWorkRequest.Builder(SyncWorker.class)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build();
        WorkManager.getInstance(context).enqueueUniqueWork(
            IMMEDIATE_WORK,
            ExistingWorkPolicy.REPLACE,
            request
        );
    }
}
