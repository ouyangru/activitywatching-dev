from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).parents[1]
ANDROID = ROOT / "android"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def test_android_manifest_requests_only_mvp_permissions():
    manifest = ElementTree.parse(ANDROID / "app/src/main/AndroidManifest.xml").getroot()
    permissions = {
        node.attrib[f"{ANDROID_NS}name"]
        for node in manifest.findall("uses-permission")
    }

    assert permissions == {
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.PACKAGE_USAGE_STATS",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_SPECIAL_USE",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
    }


def test_android_manifest_declares_special_use_foreground_collector():
    manifest = ElementTree.parse(ANDROID / "app/src/main/AndroidManifest.xml").getroot()
    services = manifest.findall("application/service")
    assert len(services) == 1
    service = services[0]
    assert service.attrib[f"{ANDROID_NS}name"] == ".CollectorService"
    assert service.attrib[f"{ANDROID_NS}foregroundServiceType"] == "specialUse"
    assert service.find("property") is not None


def test_android_collector_ignores_system_ui_and_launcher_packages():
    collector = (ANDROID / "app/src/main/java/com/ouyangru/activitytimeline/UsageCollector.java").read_text("utf-8")

    assert "com.android.systemui" in collector
    assert "com.oppo.launcher" in collector
    assert "com.vivo.launcher" in collector
    assert ".inputmethod" in collector
    # 真实应用（相机、拨号）不应被过滤
    assert "com.android.camera" not in collector


def test_cleartext_http_is_debug_only():
    main_manifest = ElementTree.parse(ANDROID / "app/src/main/AndroidManifest.xml").getroot()
    debug_manifest = ElementTree.parse(ANDROID / "app/src/debug/AndroidManifest.xml").getroot()

    assert main_manifest.find("application").attrib[f"{ANDROID_NS}usesCleartextTraffic"] == "false"
    assert debug_manifest.find("application").attrib[f"{ANDROID_NS}usesCleartextTraffic"] == "true"


def test_android_collector_has_offline_queue_and_bounded_periodic_work():
    scheduler = (ANDROID / "app/src/main/java/com/ouyangru/activitytimeline/Scheduler.java").read_text("utf-8")
    queue = (ANDROID / "app/src/main/java/com/ouyangru/activitytimeline/QueueDatabase.java").read_text("utf-8")
    feature = (ANDROID / "app/src/main/java/com/ouyangru/activitytimeline/FeatureEvent.java").read_text("utf-8")

    assert "15," in scheduler and "TimeUnit.MINUTES" in scheduler
    assert "MAX_PENDING_EVENTS = 20_000" in queue
    assert '.put("platform", "android")' in feature
    assert "MAX_DURATION_MS = 300_000L" in feature
