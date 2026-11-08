import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from late_core import conflict_free_interval, validate_interval, overlapping

class TimeConflictTests(unittest.TestCase):
    def old(self,start,end):
        return dict(studentId='001',status='审核通过',beginDate=start,endDate=end)
    def test_existing_ends_before_late_shift_window_past_it(self):
        old=self.old('2026-09-24 23:30:00','2026-09-25 00:00:00')
        result=conflict_free_interval('2026-09-25 00:01:07',[old],'001')
        self.assertEqual(result,('2026-09-25 00:00:01','2026-09-25 01:00:01'))
        self.assertEqual(overlapping([old],dict(studentId='001',beginDate=result[0],endDate=result[1])),[])
    def test_limited_gap_shortens_interval_and_preserves_late(self):
        apps=[self.old('2026-09-24 23:30:00','2026-09-25 00:00:00'),self.old('2026-09-25 00:10:00','2026-09-25 02:00:00')]
        result=conflict_free_interval('2026-09-25 00:01:07',apps,'001')
        self.assertEqual(result,('2026-09-25 00:00:01','2026-09-25 00:09:59'))
        validate_interval('2026-09-25 00:01:07',*result)
    def test_covering_request_is_never_worked_around(self):
        with self.assertRaises(ValueError):
            conflict_free_interval('2026-09-25 00:01:07',[self.old('2026-09-24 23:30:00','2026-09-25 01:00:00')],'001')
    def test_unrelated_or_rejected_request_does_not_shift(self):
        other=self.old('2026-09-24 23:30:00','2026-09-25 00:00:00');other['studentId']='002'
        rejected=dict(other,studentId='001',status='审核不通过')
        self.assertEqual(conflict_free_interval('2026-09-25 00:01:07',[other,rejected],'001'),('2026-09-24 23:31:07','2026-09-25 00:31:07'))
    def test_term_bounds_and_one_hour_limit_still_apply(self):
        with self.assertRaises(ValueError): validate_interval('2026-09-25 00:01:07','2026-09-25 00:02:00','2026-09-25 01:00:00')
        with self.assertRaises(ValueError): validate_interval('2026-09-25 00:01:07','2026-09-25 00:00:00','2026-09-25 02:00:00')
        with self.assertRaises(ValueError): validate_interval('2026-09-25 00:01:07','2026-09-24 23:31:07','2026-09-25 00:31:07','2026-09-25 00:00:00',None)

class SaveEvidenceTests(unittest.TestCase):
    def test_in_place_save_error_keeps_original_page_without_resubmitting(self):
        import tempfile
        from contextlib import contextmanager
        from playwright.sync_api import TimeoutError as PWTimeout
        from late_browser import SchoolBrowser
        class Page:
            def is_closed(self): return False
        class Frame:
            def __init__(self): self.submissions=0
            @contextmanager
            def expect_navigation(self,**kwargs):
                yield
                raise PWTimeout('no navigation: in-place rejection')
            def locator(self,*args): return self
            def click(self): self.submissions+=1
            def content(self): return '<div class="alert">已有申请时间冲突</div>'
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            b=SchoolBrowser(Path(td));b.page=Page();f=Frame()
            with self.assertRaises(PWTimeout): b.save_once(f,dict(record_id='r1'),set())
            evidence=list(Path(td).glob('保存返回-*.html'))
            self.assertEqual(len(evidence),1)
            self.assertIn('已有申请时间冲突',evidence[0].read_text(encoding='utf-8'))
            self.assertEqual(f.submissions,1)
            b.close()

if __name__=='__main__':unittest.main()
