"""Focused correction tests runnable with unittest (no web server required)."""
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.database import Database
from backend.app.offline import apply_offline
from backend.app.schemas import OfflineActivityRequest
from backend.app.analyzer import build_insights


class OfflineCorrectionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.db')
        self.zone = ZoneInfo('Asia/Shanghai')
        self.row = dict(id=1, device_id='pc', platform='windows',
                        start_time='2026-09-05T01:00:00Z', end_time='2026-09-05T02:00:00Z',
                        category='空闲', behavior='空闲', purpose='空闲', process='Code.exe',
                        description='无交互', manual_override=0, key_count=12,
                        mouse_click_count=6, scroll_count=3, window_count=60, interruptions_json='[]')

    def tearDown(self):
        self.temp.cleanup()

    def test_partial_study_work_annotations_preserve_duration_and_counters(self):
        for category, start, end in [('学习','01:00','01:40'),('工作','01:40','01:50')]:
            payload = OfflineActivityRequest(start_time=f'2026-09-05T{start}:00Z',
                end_time=f'2026-09-05T{end}:00Z', category=category)
            self.db.add_offline_annotation(payload.start_time.isoformat(), payload.end_time.isoformat(), category, '', [])
        rows = apply_offline(self.db, [self.row], self.zone)
        self.assertEqual([r['category'] for r in rows], ['学习','工作','空闲'])
        self.assertEqual([r['purpose'] for r in rows], ['学习','工作','空闲'])
        self.assertEqual(sum(r['key_count'] for r in rows), 12)
        self.assertEqual(sum(r['window_count'] for r in rows), 60)
        self.assertEqual(self.row['category'], '空闲')
        self.assertTrue(all(r['manual_override'] for r in rows[:2]))
        insights = build_insights(rows, self.zone)
        # Offline study/work does not add 50 minutes to the idle IDE's usage.
        self.assertEqual(insights['apps'][0]['seconds'], 600)

    def test_removal_restores_observation_and_active_device_wins(self):
        ident = self.db.add_offline_annotation(self.row['start_time'], self.row['end_time'], '学习', '', [])
        active = dict(self.row, category='工作', purpose='工作', behavior='编程')
        self.assertEqual(apply_offline(self.db, [active], self.zone)[0]['category'], '工作')
        self.db.delete_offline_annotation(ident)
        restored = apply_offline(self.db, [self.row], self.zone)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['category'], '空闲')

    def test_lifestyle_purpose_and_latest_annotation_wins(self):
        self.db.add_offline_annotation(self.row['start_time'], self.row['end_time'], '学习', '', [])
        self.db.add_offline_annotation(self.row['start_time'], self.row['end_time'], '运动', '', [])
        result = apply_offline(self.db, [self.row], self.zone)[0]
        self.assertEqual((result['category'], result['purpose']), ('运动','生活事务'))

    def test_schema_still_rejects_invalid_intervals(self):
        for category in ['学习', '工作', '娱乐', '睡眠']:
            value=OfflineActivityRequest(start_time='2026-09-05T01:00:00Z',end_time='2026-09-05T02:00:00Z',category=category)
            self.assertFalse(value.remember)
        with self.assertRaises(ValueError):
            OfflineActivityRequest(start_time='2026-09-05T02:00:00Z',end_time='2026-09-05T01:00:00Z',category='学习')
        with self.assertRaises(ValueError):
            OfflineActivityRequest(start_time='2026-09-05T01:00:00',end_time='2026-09-05T02:00:00',category='工作')

if __name__ == '__main__':
    unittest.main()
