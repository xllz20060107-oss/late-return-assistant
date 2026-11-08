import sys, unittest, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from late_core import rejected_application, Journal, parse_records

class RejectedTests(unittest.TestCase):
    def plan(self):
        return dict(record_id="r",studentId="2023",username="测试",departName="学院",lateDate="2026-10-24 23:30:12")
    def app(self,**changes):
        a=dict(id="old",studentId="2023",username="测试",departName="学院",status="审核不通过",beginDate="2026-10-15 00:00:00",endDate="2027-01-15 00:00:00")
        a.update(changes);return a
    def test_only_unique_rejected_matching_identity_and_actual_time(self):
        p=self.plan();a=self.app()
        self.assertEqual(rejected_application([a,self.app(id="approved",status="审核通过")],p),a)
        for change in (dict(status="待审核"),dict(username="别人"),dict(studentId="999"),dict(departName="别院"),dict(endDate="2026-10-23 00:00:00")):
            with self.subTest(change=change),self.assertRaises(ValueError): rejected_application([self.app(**change)],p)
        with self.assertRaises(ValueError):rejected_application([a,self.app(id="second")],p)
    def test_fresh_revalidation_cannot_silently_switch_rejected_id_or_interval(self):
        p=dict(self.plan(),rejected_application=self.app())
        for change in (dict(id="new"),dict(beginDate="2026-10-16 00:00:00"),dict(status="审核通过")):
            with self.subTest(change=change),self.assertRaises(ValueError):rejected_application([self.app(**change)],p)
    def test_journal_delete_phase_survives_restart_and_cannot_be_rebegun(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            path=Path(td)/"journal.db";j=Journal(path)
            j.begin("r",dict(self.plan(),rejected_application=self.app()),state="deleting")
            j.close();j=Journal(path)
            self.assertEqual(j.get("r")["state"],"deleting")
            with self.assertRaises(ValueError): j.begin("r",self.plan())
            j.update("r","deleted");j.update("r","saving")
            self.assertEqual(j.get("r")["state"],"saving");j.close()
    def test_same_rejected_application_is_reserved_across_other_return_records(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            path=Path(td)/'journal.db';j=Journal(path)
            plan=dict(self.plan(),rejected_application=self.app())
            j.begin('r',plan,state='deleting');j.close();j=Journal(path)
            try:
                with self.assertRaises(ValueError):j.begin('another',dict(plan,record_id='another'),state='deleting')
                self.assertIsNone(j.get('another'))
                self.assertEqual(j.rejected_owner('old'),'r')
            finally:j.close()

    def test_record_parser_keeps_rejected_status(self):
        html='<table id="contentTable"><tbody><tr><td><input name="ids" value="r"></td><td><a href="/detail?id=r">2023</a></td><td>测试</td><td>学院</td><td>班级</td><td>2025</td><td>2026-10-24 23:30:12</td><td>进</td><td>审核不通过</td></tr></tbody></table>'
        result=parse_records(html,"2026-10-24","2026-10-24")
        self.assertEqual(result[0]["source_status"],"审核不通过")

class DeleteFlowTests(unittest.TestCase):
    def test_delete_then_replace_and_approve_only_once(self):
        from test_batch_flow import BatchFlowTests
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            b=BatchFlowTests().harness(Path(td));p=BatchFlowTests().plans()[0]
            target=dict(p,id='rejected-old',status='审核不通过')
            p.update(source_status='审核不通过',rejected_application=dict(target))
            b.apps=[target];clicks=[]
            b.detail=lambda plan:dict(plan,applyStatus='审核不通过' if any(a['id']=='rejected-old' for a in b.apps) else '无申请',ioFlag='进')
            b.rejected_page=lambda plan:object()
            def delete(frame,plan,target):
                self.assertEqual(b.journal.get(plan['record_id'])['state'],'deleting')
                clicks.append(target['id']);b.apps=[a for a in b.apps if a['id']!=target['id']]
            b.confirm_delete=delete
            b.execute([p]);self.assertEqual(clicks,['rejected-old'])
            self.assertEqual(b.journal.get(p['record_id'])['state'],'done')
            self.assertEqual([a['status'] for a in b.apps],['审核通过'])
            b.execute([p]);self.assertEqual(clicks,['rejected-old']);b.close()
    def test_uncertain_delete_never_repeated_and_no_replacement_saved(self):
        from test_batch_flow import BatchFlowTests
        from late_browser import SchoolBrowser
        for raises in (False,True):
            with self.subTest(raises=raises),tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
                b=BatchFlowTests().harness(Path(td));p=BatchFlowTests().plans()[0]
                target=dict(p,id='old',status='审核不通过')
                p.update(source_status='审核不通过',rejected_application=dict(target));b.apps=[target]
                b.detail=lambda plan:dict(plan,applyStatus='审核不通过',ioFlag='进')
                b.rejected_page=lambda plan:object();clicks=[]
                def delete(*args):
                    clicks.append('delete')
                    if raises: raise ValueError('connection lost')
                b.confirm_delete=delete
                with self.assertRaises(ValueError): b.execute([p])
                self.assertEqual(b.journal.get(p['record_id'])['state'],'deleting')
                with self.assertRaises(ValueError): b.execute([p])
                self.assertEqual(clicks,['delete']);self.assertEqual(len(b.apps),1);b.close()

class DeleteControlTests(unittest.TestCase):
    def test_bulk_control_clears_other_selection_and_confirms_exact_target(self):
        from test_batch_flow import BatchFlowTests
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            b=BatchFlowTests().harness(Path(td));p=BatchFlowTests().plans()[0]
            target=dict(p,id='old',status='审核不通过');p['rejected_application']=target
            selected={'other','header'};clicked=[];handlers={}
            cells=['<input name="ids" value="old">',p['studentId'],p['username'],p['departName'],p['naturalclassName'],p['grade'],p['beginDate'],p['endDate'],'审核不通过','做实验','正常','2026-10-28 10:00:00','']
            html='<tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr>'
            class Box:
                def __init__(self,key):self.key=key
                def uncheck(self):selected.discard(self.key)
                def check(self):selected.add(self.key)
            class Boxes:
                def count(self):return 3
                def nth(self,i):return Box(['header','other','old'][i])
            class Selection:
                def evaluate_all(self,_):return sorted(selected)
            class Row:
                def count(self):return 1
                def filter(self,**kwargs):return self
                def evaluate(self,_):return html
                def locator(self,_):return Box('old')
            class Dialog:
                type='confirm';message='请确认是否删除选中项？'
                def accept(self):clicked.append('confirmed')
                def dismiss(self):raise AssertionError('unexpected dismiss')
            class Button:
                def count(self):return 1
                def filter(self,**kwargs):return self
                def get_attribute(self,_):return "return doAction('deleteSelectedAll','','请确认是否删除选中项？')"
                def click(self):
                    self_selected=sorted(selected)
                    if self_selected!=['old']:raise AssertionError(self_selected)
                    clicked.append('delete');handlers['dialog'](Dialog())
            class Frame:
                def locator(self,sel):
                    if sel=='#contentTable tbody tr':return Row()
                    if sel=='#searchForm input[type="checkbox"]':return Boxes()
                    if sel=='#searchForm input[name="ids"]:checked':return Selection()
                    if sel=='button':return Button()
                    return Box('old')
                def expect_navigation(self,**kwargs):return nullcontext()
            class Page:
                def on(self,key,fn):handlers[key]=fn
                def remove_listener(self,key,fn):handlers.pop(key)
            b.page=Page()
            b.confirm_delete(Frame(),p,target)
            self.assertEqual(selected,{'old'});self.assertEqual(clicked,['delete','confirmed']);self.assertEqual(handlers,{})
            b.page=None;b.close()

if __name__=='__main__':unittest.main()


