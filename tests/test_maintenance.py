import sys
import os
import tempfile
import unittest
import zipfile
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from late_core import Journal
from late_maintenance import cleanup_logs, success_rows, ranking_rows, export_ranking, Maintenance
NS={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root=Path(self.temp.name);self.j=Journal(self.root/'journal.db')
    def tearDown(self): self.j.close();self.temp.cleanup()
    def add(self,rid,sid,appid,state='done'):
        p=dict(studentId=sid,username='测试'+sid,departName='学院',specialtyName='机器人工程',naturalclassName='2025机器人1班',grade='2025',lateDate='2026-09-25 00:01:07',beginDate='2026-09-25 00:00:01',endDate='2026-09-25 01:00:01')
        self.j.begin(rid,p);self.j.update(rid,state,appid)
    def test_cleanup_old_diagnostics_only_and_keeps_ranking_profile_journal(self):
        now=2000000000
        names=['运行日志-old.txt','异常-old.png','保存返回-old.html','执行结果-old.csv','错误详情-old.txt','journal.db','学生补录次数排行榜.xlsx','其他文件.txt']
        for name in names:
            if name!='journal.db': (self.root/name).write_text('keep or remove',encoding='utf-8')
            os.utime(self.root/name,(now-8*86400,now-8*86400))
        recent=self.root/'运行日志-recent.txt';recent.write_text('recent');os.utime(recent,(now-7*86400,now-7*86400))
        profile=self.root/'edge-profile';profile.mkdir();(profile/'运行日志-old.txt').write_text('profile')
        deleted=cleanup_logs(self.root,now=now)
        self.assertEqual(set(deleted),set(names[:5]))
        for name in names[5:]+['运行日志-recent.txt']:self.assertTrue((self.root/name).exists())
        self.assertTrue((profile/'运行日志-old.txt').exists())
    def test_successes_are_permanent_deduplicated_and_ties_rank_1_1_3(self):
        for rid,sid,app in [('r1','001','a1'),('r2','001','a2'),('r3','002','b1'),('r4','002','b2'),('r5','003','c1'),('r6','001','a1')]:self.add(rid,sid,app)
        self.add('r7','001','pending','saving')
        details=success_rows(self.root/'journal.db');ranks=ranking_rows(details)
        self.assertEqual(len(details),5)
        self.assertEqual([(r['rank'],r['studentId'],r['count']) for r in ranks],[(1,'001',2),(1,'002',2),(3,'003',1)])
        cleanup_logs(self.root,now=3000000000)
        self.assertEqual(len(success_rows(self.root/'journal.db')),5)
    def test_xlsx_counts_ids_dates_and_no_formula_injection(self):
        self.add('r1','001','a1');self.add('r2','001','a2');self.add('r3','002','b1')
        target=export_ranking(self.root/'journal.db',self.root/'学生补录次数排行榜.xlsx')
        with zipfile.ZipFile(target) as z:
            self.assertIsNone(z.testzip())
            sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            values={c.get('r'):c for c in sheet.findall('.//x:sheetData/x:row/x:c',NS)}
            self.assertEqual(values['B7'].get('t'),'inlineStr')
            self.assertEqual(values['B7'].find('x:is/x:t',NS).text,'001')
            self.assertEqual(values['H7'].find('x:v',NS).text,'2')
            self.assertIsNotNone(values['I7'].find('x:v',NS))
            self.assertEqual(sheet.find('x:autoFilter',NS).get('ref'),'A6:J8')
            self.assertIsNotNone(sheet.find('.//x:pane',NS))
    def test_locked_excel_preserves_previous_file_and_success_counts(self):
        self.add('r1','001','a1')
        target=export_ranking(self.root/'journal.db',self.root/'学生补录次数排行榜.xlsx')
        original=target.read_bytes();self.add('r2','001','a2')
        with patch('late_maintenance.os.replace',side_effect=PermissionError('Excel is open')):
            with self.assertRaises(PermissionError):export_ranking(self.root/'journal.db',target)
        self.assertEqual(target.read_bytes(),original)
        self.assertEqual(len(success_rows(self.root/'journal.db')),2)
        export_ranking(self.root/'journal.db',target)
        self.assertNotEqual(target.read_bytes(),original)
    def test_corrupt_journal_does_not_replace_workbook_with_zero(self):
        target=self.root/'学生补录次数排行榜.xlsx';target.write_bytes(b'previous workbook')
        self.j.db.execute("INSERT INTO actions VALUES ('bad','done','a1','not-json','2026-10-29T12:00:00')");self.j.db.commit()
        with self.assertRaises(ValueError):export_ranking(self.root/'journal.db',target)
        self.assertEqual(target.read_bytes(),b'previous workbook')

    def test_daily_cleanup_and_deferred_export_retry(self):
        clock=[2000000000.0];logs=[]
        m=Maintenance(self.root,logs.append,clock=lambda:clock[0]);m.run(force=True)
        stale=self.root/'运行日志-next.txt';stale.write_text('old');os.utime(stale,(clock[0]-8*86400,clock[0]-8*86400))
        clock[0]+=60;m.run();self.assertTrue(stale.exists())
        self.add('r1','001','a1')
        with patch('late_maintenance.os.replace',side_effect=PermissionError('locked')):m.run(force_export=True)
        self.assertTrue(m.export_pending)
        clock[0]+=301;m.run()
        self.assertFalse(m.export_pending)
        self.assertIsNone(m.last_error)
        self.assertTrue(stale.exists())
        clock[0]+=86400;m.run();self.assertFalse(stale.exists())
        self.assertEqual(len(success_rows(self.root/'journal.db')),1)

    def test_student_name_starting_with_equals_is_literal_text(self):
        self.add('r1','001','a1')
        row=self.j.get('r1');p=json.loads(row['plan']);p['username']='=1+1'
        self.j.db.execute('UPDATE actions SET plan=? WHERE record_id=?',(json.dumps(p),'r1'));self.j.db.commit()
        out=export_ranking(self.root/'journal.db',self.root/'学生补录次数排行榜.xlsx')
        with zipfile.ZipFile(out) as z:
            sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            c=sheet.find(".//x:c[@r='C7']",NS)
            self.assertEqual(c.get('t'),'inlineStr')
            self.assertEqual(c.find('x:is/x:t',NS).text,'=1+1')
            self.assertEqual(sheet.findall('.//x:f',NS),[])

if __name__=='__main__':unittest.main()
