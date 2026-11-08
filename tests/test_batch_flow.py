import sys
import unittest
import tempfile
from pathlib import Path
from contextlib import contextmanager
from playwright.sync_api import TimeoutError as PWTimeout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from late_browser import SchoolBrowser

class BatchFlowTests(unittest.TestCase):
    def harness(self,path,fail_at=None):
        class Browser(SchoolBrowser):
            def __init__(self,path):
                super().__init__(path)
                self.apps=[];self.pauses=[];self.fail_at=fail_at
            def pause(self,seconds): self.check();self.pauses.append(seconds)
            def detail(self,plan): return dict(plan,applyStatus='无申请',ioFlag='进')
            def applications(self,student): return [dict(a) for a in self.apps if a['studentId']==student]
            def fill_form(self,plan):
                browser=self
                class Frame:
                    @contextmanager
                    def expect_navigation(self,**kwargs):
                        yield
                        if len(browser.apps)==browser.fail_at: raise PWTimeout('save navigation timeout')
                    def locator(self,selector): return self
                    def click(self): browser.apps.append(dict(plan,id='a'+str(len(browser.apps)+1),status='待审核',lateType='学习原因',markup='补录'))
                    def wait_for_selector(self,*args,**kwargs): pass
                    def content(self): return "<html><table id=\"contentTable\"></table></html>"
                return Frame()
            def approve(self,plan,application_id):
                next(a for a in self.apps if a['id']==application_id)['status']='审核通过'
                self.journal.update(plan['record_id'],'done',application_id)
        return Browser(path)

    def plans(self):
        return [dict(record_id='r'+str(i),studentId=str(i),username='测试'+str(i),departName='测试学院',specialtyName='测试专业',naturalclassName='测试班',grade='2025',lateDate='2026-10-27 23:30:00',beginDate='2026-10-27 23:00:00',endDate='2026-10-28 00:00:00',phone='',reason='1') for i in range(1,26)]

    def test_25_records_complete_and_completed_rerun_never_saves_again(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            b=self.harness(Path(td))
            plans=self.plans()
            result=b.execute(plans)
            self.assertEqual(len(result),25)
            self.assertEqual([a['status'] for a in b.apps],['审核通过']*25)
            self.assertEqual(b.pauses,[3,3])
            b.execute(plans)
            self.assertEqual(len(b.apps),25)
            b.close()

    def test_21st_save_timeout_does_not_repeat_uncertain_write(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            b=self.harness(Path(td),fail_at=21)
            plans=self.plans()
            with self.assertRaises(PWTimeout): b.execute(plans)
            self.assertEqual(len(b.apps),21)
            self.assertEqual(b.journal.get('r20')['state'],'done')
            self.assertEqual(b.journal.get('r21')['state'],'saving')
            self.assertIsNone(b.journal.get('r22'))
            with self.assertRaises(ValueError): b.execute(plans)
            self.assertEqual(len(b.apps),21)
            b.close()

if __name__=='__main__': unittest.main()
