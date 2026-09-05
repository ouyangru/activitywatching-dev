package com.ouyangru.activitytimeline;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.format.DateFormat;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import java.net.URI;
import java.util.Date;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int REQUEST_NOTIFICATIONS = 1001;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private SettingsStore settings;
    private EditText serverUrlInput;
    private EditText tokenInput;
    private EditText deviceIdInput;
    private CheckBox monitoringEnabled;
    private TextView permissionStatus;
    private TextView runtimeStatus;
    private Button syncButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        settings = new SettingsStore(this);

        serverUrlInput = findViewById(R.id.serverUrlInput);
        tokenInput = findViewById(R.id.tokenInput);
        deviceIdInput = findViewById(R.id.deviceIdInput);
        monitoringEnabled = findViewById(R.id.monitoringEnabled);
        permissionStatus = findViewById(R.id.permissionStatus);
        runtimeStatus = findViewById(R.id.runtimeStatus);
        syncButton = findViewById(R.id.syncButton);

        serverUrlInput.setText(settings.serverUrl());
        tokenInput.setText(settings.apiToken());
        deviceIdInput.setText(settings.deviceId(this));
        monitoringEnabled.setChecked(settings.monitoringEnabled());

        findViewById(R.id.grantPermissionButton).setOnClickListener(view -> UsageAccess.openSettings(this));
        findViewById(R.id.saveButton).setOnClickListener(view -> saveAndConfigure());
        findViewById(R.id.testButton).setOnClickListener(view -> testConnection(view));
        syncButton.setOnClickListener(view -> syncNow());
        findViewById(R.id.batteryButton).setOnClickListener(view -> requestBatteryExemption());

        refreshStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (settings != null) {
            refreshStatus();
        }
    }

    private void saveAndConfigure() {
        String error = validateInputs();
        if (error != null) {
            Toast.makeText(this, error, Toast.LENGTH_LONG).show();
            return;
        }
        boolean enabled = monitoringEnabled.isChecked();
        settings.saveConnection(
            serverUrlInput.getText().toString(),
            tokenInput.getText().toString(),
            deviceIdInput.getText().toString(),
            enabled
        );
        Scheduler.configure(this, enabled);
        if (enabled) {
            requestNotificationPermissionIfNeeded();
            CollectorService.start(this);
            Scheduler.syncNow(this);
        } else {
            CollectorService.stop(this);
        }
        Toast.makeText(this, enabled ? "已启动后台采集" : "已停止后台采集", Toast.LENGTH_SHORT).show();
        refreshStatus();
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33
            && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    private void requestBatteryExemption() {
        PowerManager powerManager = getSystemService(PowerManager.class);
        if (powerManager == null) {
            Toast.makeText(this, "无法读取电池优化状态", Toast.LENGTH_SHORT).show();
            return;
        }
        if (powerManager.isIgnoringBatteryOptimizations(getPackageName())) {
            Toast.makeText(this, "已在电池优化白名单中，请再到系统设置确认后台运行权限", Toast.LENGTH_LONG).show();
            return;
        }
        try {
            Intent intent = new Intent(
                Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:" + getPackageName())
            );
            startActivity(intent);
        } catch (Exception ignored) {
            Toast.makeText(this, "请到 系统设置 → 电池 中手动允许本应用完全后台行为", Toast.LENGTH_LONG).show();
        }
    }

    private void syncNow() {
        String error = validateInputs();
        if (error != null) {
            Toast.makeText(this, error, Toast.LENGTH_LONG).show();
            return;
        }
        if (!UsageAccess.isGranted(this)) {
            Toast.makeText(this, "请先授予使用情况访问权限", Toast.LENGTH_LONG).show();
            UsageAccess.openSettings(this);
            return;
        }
        settings.saveConnection(
            serverUrlInput.getText().toString(),
            tokenInput.getText().toString(),
            deviceIdInput.getText().toString(),
            true
        );
        monitoringEnabled.setChecked(true);
        Scheduler.configure(this, true);
        CollectorService.start(this);
        Scheduler.syncNow(this);
        runtimeStatus.setText("已提交立即同步任务，请稍后刷新状态或查看网页时间线。\n当前待发送：" + pendingCount() + " 条");
    }

    private void testConnection(View button) {
        String error = validateInputs();
        if (error != null) {
            Toast.makeText(this, error, Toast.LENGTH_LONG).show();
            return;
        }
        button.setEnabled(false);
        runtimeStatus.setText("正在通过局域网连接后端…");
        String url = normalizeUrl(serverUrlInput.getText().toString());
        executor.execute(() -> {
            try {
                String response = ApiClient.health(url);
                runOnUiThread(() -> runtimeStatus.setText("连接成功：" + response));
            } catch (Exception exception) {
                String message = exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
                runOnUiThread(() -> runtimeStatus.setText("连接失败：" + message + "\n请检查电脑 IP、8765 端口、防火墙和后端监听地址。"));
            } finally {
                runOnUiThread(() -> button.setEnabled(true));
            }
        });
    }

    private String validateInputs() {
        String rawUrl = serverUrlInput.getText().toString().trim();
        try {
            URI uri = URI.create(rawUrl);
            if (!("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme())) || uri.getHost() == null) {
                return "后端地址必须是完整的 http:// 或 https:// 地址";
            }
        } catch (Exception ignored) {
            return "后端地址格式不正确，例如 http://192.168.1.20:8765";
        }
        String deviceId = deviceIdInput.getText().toString().trim();
        if (deviceId.isEmpty() || deviceId.length() > 128) {
            return "设备名称长度必须为 1—128 个字符";
        }
        return null;
    }

    private static String normalizeUrl(String value) {
        String normalized = value.trim();
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }

    private void refreshStatus() {
        boolean granted = UsageAccess.isGranted(this);
        permissionStatus.setText(granted ? "✓ 已授予使用情况访问权限" : "✗ 尚未授予使用情况访问权限");
        syncButton.setEnabled(granted);
        long lastSync = settings.lastSync();
        String syncedAt = lastSync == 0L
            ? "尚未成功上传"
            : "最近上传：" + DateFormat.format("yyyy-MM-dd HH:mm:ss", new Date(lastSync));
        runtimeStatus.setText(settings.lastStatus() + "\n" + syncedAt + "\n当前待发送：" + pendingCount() + " 条");
    }

    private int pendingCount() {
        try (QueueDatabase queue = new QueueDatabase(this)) {
            return queue.pendingCount();
        }
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }
}
