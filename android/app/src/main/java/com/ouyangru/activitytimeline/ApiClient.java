package com.ouyangru.activitytimeline;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;

final class ApiClient {
    private static final int CONNECT_TIMEOUT_MS = 5_000;
    private static final int READ_TIMEOUT_MS = 12_000;

    private ApiClient() {}

    static String health(String serverUrl) throws IOException {
        HttpURLConnection connection = open(serverUrl + "/api/v1/health", "GET", "");
        int code = connection.getResponseCode();
        String body = readBody(connection, code);
        connection.disconnect();
        if (code != 200) {
            throw new IOException("健康检查返回 HTTP " + code + ": " + body);
        }
        return body;
    }

    static UploadResult uploadPending(QueueDatabase queue, SettingsStore settings) {
        String serverUrl = settings.serverUrl();
        if (serverUrl.isBlank()) {
            return UploadResult.permanentFailure("尚未填写后端地址");
        }
        int uploaded = 0;
        try {
            for (int page = 0; page < 10; page++) {
                List<QueueDatabase.PendingEvent> batch = queue.loadBatch(100);
                if (batch.isEmpty()) {
                    return UploadResult.success(uploaded);
                }
                JSONArray events = new JSONArray();
                for (QueueDatabase.PendingEvent pending : batch) {
                    events.put(new JSONObject(pending.payload));
                }
                byte[] body = new JSONObject().put("events", events)
                    .toString()
                    .getBytes(StandardCharsets.UTF_8);

                HttpURLConnection connection = open(
                    serverUrl + "/api/v1/events/batch",
                    "POST",
                    settings.apiToken()
                );
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(body.length);
                connection.setDoOutput(true);
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(body);
                }
                int code = connection.getResponseCode();
                String responseBody = readBody(connection, code);
                connection.disconnect();

                if (code == 200) {
                    queue.deleteBatch(batch);
                    uploaded += batch.size();
                    continue;
                }
                if (code == 401 || code == 403 || (code >= 400 && code < 500)) {
                    return UploadResult.permanentFailure("上传被拒绝，HTTP " + code + ": " + responseBody);
                }
                return UploadResult.retry("后端暂时不可用，HTTP " + code);
            }
            return UploadResult.success(uploaded);
        } catch (Exception error) {
            return UploadResult.retry("局域网连接失败：" + safeMessage(error));
        }
    }

    private static HttpURLConnection open(String url, String method, String token) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
        connection.setReadTimeout(READ_TIMEOUT_MS);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");
        if (token != null && !token.isBlank()) {
            connection.setRequestProperty("Authorization", "Bearer " + token);
        }
        return connection;
    }

    private static String readBody(HttpURLConnection connection, int code) throws IOException {
        InputStream stream = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
        if (stream == null) {
            return "";
        }
        StringBuilder result = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null && result.length() < 2_000) {
                result.append(line);
            }
        }
        return result.toString();
    }

    private static String safeMessage(Exception error) {
        String message = error.getMessage();
        return message == null || message.isBlank() ? error.getClass().getSimpleName() : message;
    }

    static final class UploadResult {
        final boolean success;
        final boolean shouldRetry;
        final int uploaded;
        final String message;

        private UploadResult(boolean success, boolean shouldRetry, int uploaded, String message) {
            this.success = success;
            this.shouldRetry = shouldRetry;
            this.uploaded = uploaded;
            this.message = message;
        }

        static UploadResult success(int uploaded) {
            return new UploadResult(true, false, uploaded, "已上传 " + uploaded + " 条记录");
        }

        static UploadResult retry(String message) {
            return new UploadResult(false, true, 0, message);
        }

        static UploadResult permanentFailure(String message) {
            return new UploadResult(false, false, 0, message);
        }
    }
}
