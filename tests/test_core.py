import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from late_core import interval, parse_records, parse_detail, parse_applications, matching, Journal


class CoreTests(unittest.TestCase):
    def test_empty_padding_rows_are_ignored_but_incomplete_students_raise(self):
        def table(body):
            return '<table id="contentTable"><tbody>'+body+'</tbody></table>'
        padding = '<tr>'+('<td>&nbsp;</td>'*9)+'</tr><tr><td colspan="9"> </td></tr>'
        real = '<tr><td><input name="ids" value="r1"></td><td><a href="/sahManage/a/apply/nightLateRecord/detail?id=r1">001</a></td><td>测试甲</td><td>学院</td><td>班级</td><td>2025</td><td>2026-10-27 23:48:24</td><td>进</td><td>无申请</td></tr>'
        self.assertEqual([r['record_id'] for r in parse_records(table(real+padding),'2026-10-27','2026-10-27')],['r1'])
        self.assertEqual(parse_records(table(padding),'2026-10-27','2026-10-27'),[])
        app_padding='<tr>'+('<td>&nbsp;</td>'*13)+'</tr>'
        self.assertEqual(parse_applications(table(app_padding)),[])
        with self.assertRaises(ValueError):
            parse_records(table(real.replace('<input name="ids" value="r1">','')),'2026-10-27','2026-10-27')

    def test_midnight_and_term_boundary(self):
        self.assertEqual(interval('2026-10-24 23:45:12'), ('2026-10-24 23:15:12', '2026-10-25 00:15:12'))
        self.assertEqual(interval('2026-08-30 00:07:00', '2026-08-30 00:00:00', '2027-01-15 23:59:59'), ('2026-08-30 00:00:00', '2026-08-30 01:00:00'))
        self.assertEqual(interval('2027-01-15 23:50:00', '2026-08-30 00:00:00', '2027-01-15 23:59:59'), ('2027-01-15 22:59:59', '2027-01-15 23:59:59'))
        with self.assertRaises(ValueError):
            interval('2026-08-01 12:00:00', '2026-08-30 00:00:00', None)

    def test_filter_by_date_status_direction_and_deduplicate(self):
        def row(rid, time, direction='进', state='无申请'):
            return f'<tr><td><input name="ids" value="{rid}"></td><td><a href="/sahManage/a/apply/nightLateRecord/detail?id={rid}">001</a></td><td>示例</td><td>学院</td><td>班级</td><td>2025</td><td>{time}</td><td>{direction}</td><td>{state}</td></tr>'
        html = '<table id="contentTable"><tbody>' + row('a', '2026-10-24 23:30:12')*2 + row('b', '2026-10-25 01:00:00') + row('c', '2026-10-24 23:00:00', state='审核不通过') + row('d', '2026-10-24 23:00:00', direction='出') + '</tbody></table>'
        records = parse_records(html, '2026-10-24', '2026-10-24')
        self.assertEqual([r['record_id'] for r in records], ['a', 'c'])
        self.assertEqual(records[1]['source_status'], '审核不通过')
        self.assertEqual(records[0]['studentId'], '001')

    def test_detail_identity_and_empty_required(self):
        html = '<form id="inputForm">' + ''.join(f'<div class="control-group"><label for="{k}"></label><div class="controls">{v}</div></div>' for k,v in dict(studentId='001',username='示例',departName='学院',specialtyName='专业',naturalclassName='班级',grade='2025',lateDate='2026-10-24 23:30:12',applyStatus='无申请',ioFlag='进').items()) + '</form>'
        self.assertEqual(parse_detail(html, '001')['specialtyName'], '专业')
        with self.assertRaises(ValueError):
            parse_detail(html, '002')
        with self.assertRaises(ValueError):
            parse_detail(html.replace('>专业<','><'), '001')

    def test_match_uses_full_id_time_and_type(self):
        html = '<table id="contentTable"><tbody><tr><td><input name="ids" value="new1"></td><td><a>001</a></td><td>示例</td><td>学院</td><td>班级</td><td>2025</td><td>2026-10-24  23:00:12</td><td>2026-10-25 00:00:12</td><td>待审核</td><td>学习原因</td><td>补录</td><td>2026-10-28 00:00:00</td><td></td></tr></tbody></table>'
        apps = parse_applications(html)
        plan = dict(studentId='001', username='示例', beginDate='2026-10-24 23:00:12', endDate='2026-10-25 00:00:12')
        self.assertEqual([r['id'] for r in matching(apps, plan)], ['new1'])
        self.assertEqual(matching(apps, dict(plan, studentId='002')), [])
        self.assertEqual(matching(apps, dict(plan, endDate='2026-10-25 01:00:12')), [])

    def test_unknown_save_is_durable_and_blocks_retries(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            j = Journal(Path(td) / 'journal.db')
            j.begin('r1', {'studentId': '001'})
            j.close()
            j = Journal(Path(td) / 'journal.db')
            self.assertEqual(j.get('r1')['state'], 'saving')
            with self.assertRaises(ValueError):
                j.begin('r1', {'studentId': '001'})
            j.update('r1', 'saved', 'new1')
            j.update('r1', 'done', 'new1')
            self.assertEqual(j.get('r1')['application_id'], 'new1')
            j.close()

if __name__ == '__main__':
    unittest.main()
