package com.ouyangru.activitytimeline;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import org.json.JSONException;

import java.util.ArrayList;
import java.util.List;

final class QueueDatabase extends SQLiteOpenHelper {
    private static final String DB_NAME = "activity_timeline_queue.db";
    private static final int DB_VERSION = 1;
    private static final int MAX_PENDING_EVENTS = 20_000;

    QueueDatabase(Context context) {
        super(context, DB_NAME, null, DB_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL(
            "CREATE TABLE pending_events (" +
                "sequence INTEGER PRIMARY KEY," +
                "payload TEXT NOT NULL," +
                "created_at INTEGER NOT NULL)"
        );
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        // Version 1 has no migration yet.
    }

    synchronized void enqueue(List<FeatureEvent> events, String deviceId) throws JSONException {
        if (events.isEmpty()) {
            return;
        }
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            for (FeatureEvent event : events) {
                ContentValues values = new ContentValues();
                values.put("sequence", event.sequence);
                values.put("payload", event.toJson(deviceId).toString());
                values.put("created_at", System.currentTimeMillis());
                db.insertWithOnConflict("pending_events", null, values, SQLiteDatabase.CONFLICT_IGNORE);
            }
            db.execSQL(
                "DELETE FROM pending_events WHERE sequence NOT IN (" +
                    "SELECT sequence FROM pending_events ORDER BY sequence DESC LIMIT " + MAX_PENDING_EVENTS + ")"
            );
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    synchronized List<PendingEvent> loadBatch(int limit) {
        List<PendingEvent> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query(
            "pending_events",
            new String[]{"sequence", "payload"},
            null,
            null,
            null,
            null,
            "sequence ASC",
            Integer.toString(limit)
        )) {
            while (cursor.moveToNext()) {
                result.add(new PendingEvent(cursor.getLong(0), cursor.getString(1)));
            }
        }
        return result;
    }

    synchronized void deleteBatch(List<PendingEvent> events) {
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            for (PendingEvent event : events) {
                db.delete("pending_events", "sequence = ?", new String[]{Long.toString(event.sequence)});
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    synchronized int pendingCount() {
        try (Cursor cursor = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM pending_events", null)) {
            return cursor.moveToFirst() ? cursor.getInt(0) : 0;
        }
    }

    static final class PendingEvent {
        final long sequence;
        final String payload;

        PendingEvent(long sequence, String payload) {
            this.sequence = sequence;
            this.payload = payload;
        }
    }
}
