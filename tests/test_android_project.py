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
    }


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
