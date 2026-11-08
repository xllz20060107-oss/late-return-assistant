import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from late_browser import SchoolBrowser
from fixture_site import Site
from unittest.mock import patch
from playwright.sync_api import TimeoutError as PlaywrightTimeout


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.site = Site(); self.site.start()
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.browser = SchoolBrowser(Path(self.temp.name), origin=self.site.origin, headless=True)
        self.browser.open()

    def tearDown(self):
        self.browser.close(); self.site.close(); self.temp.cleanup()

    def test_preview_two_pages_without_writes_then_save_and_approve_one(self):
        plans = self.browser.scan('2026-10-24','2026-10-24',2)
        self.assertEqual([p['studentId'] for p in plans], ['001','002'])
        self.assertEqual(plans[0]['beginDate'],'2026-10-24 23:15:12')
        self.assertEqual(plans[0]['endDate'],'2026-10-25 00:15:12')
        self.assertEqual(self.site.writes, [])
        results = self.browser.execute(plans[:1])
        self.assertEqual(results[0]['result'],'审核通过')
        self.assertEqual(self.site.apps[0]['status'], '审核通过')
        self.assertEqual(self.site.writes[0][1]['phone'],'')
        self.assertEqual(self.site.writes[0][1]['lateType'],'1')
        self.assertEqual(self.site.writes[0][1]['markup'],'2')
        self.browser.execute(plans[:1])
        self.assertEqual(len(self.site.writes),2)

    def test_mismatched_saved_phone_never_approved(self):
        plans = self.browser.scan('2026-10-24','2026-10-24',1)
        self.site.bad_saved=True
        with self.assertRaises(ValueError):
            self.browser.execute(plans)
        self.assertEqual([x[0] for x in self.site.writes], ['save'])

    def test_unknown_save_result_never_repeated(self):
        plans = self.browser.scan('2026-10-24','2026-10-24',1)
        self.site.fail_save_response=True
        with self.assertRaises(Exception):
            self.browser.execute(plans)
        with self.assertRaises(ValueError):
            self.browser.execute(plans)
        self.assertEqual([x[0] for x in self.site.writes], ['save'])

    def test_detail_mismatch_stops_before_writes(self):
        self.site.bad_detail=True
        with self.assertRaises(ValueError):
            self.browser.scan('2026-10-24','2026-10-24',1)
        self.assertEqual(self.site.writes, [])

    def test_25_records_save_and_approve_once_each(self):
        from fixture_site import RECORDS
        records=[dict(RECORDS[0],record_id=f'r{i}',studentId=f'{i:03d}',username=f'测试{i}') for i in range(1,26)]
        with patch('fixture_site.RECORDS',records):
            plans=self.browser.scan('2026-10-24','2026-10-24',25)
            self.assertEqual(len(plans),25)
            results=self.browser.execute(plans)
        self.assertEqual(len(results),25)
        self.assertEqual([a['status'] for a in self.site.apps],['审核通过']*25)
        self.assertEqual(len([w for w in self.site.writes if w[0]=='save']),25)
        self.assertEqual(len([w for w in self.site.writes if w[0]=='audit']),25)
        for p in plans:
            self.assertEqual(self.browser.journal.get(p['record_id'])['state'],'done')

    def test_jbox_confirmation(self):
        self.site.jbox=True
        plans = self.browser.scan('2026-10-24','2026-10-24',1)
        self.assertEqual(self.browser.execute(plans)[0]['result'],'审核通过')

    def test_limit_counts_executable_records_after_existing_coverage(self):
        self.site.apps.append(dict(id='old',studentId='001',username='测试甲',departName='测试学院',specialtyName='测试专业',naturalclassName='测试班',grade='2025',beginDate='2026-10-24 23:00:00',endDate='2026-10-25 00:30:00',status='待审核',lateType='学习原因',markup='正常',createDate='2026-10-24 00:00:00'))
        plans=self.browser.scan('2026-10-24','2026-10-24',1)
        self.assertEqual([p['studentId'] for p in plans],['002'])

    def test_slow_cascade_with_existing_options_waits_for_refresh(self):
        self.site.slow_prefilled=True
        plans=self.browser.scan('2026-10-24','2026-10-24',1)
        self.assertEqual(self.browser.execute(plans)[0]['result'],'审核通过')
        self.assertEqual(self.site.writes[0][1]['chainReady'],'2')

if __name__=='__main__':
    unittest.main()
