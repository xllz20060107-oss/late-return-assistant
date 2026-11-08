import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from late_core import fallback_plan

class FallbackTests(unittest.TestCase):
    def test_only_academic_fields_change_and_original_values_are_retained(self):
        source=dict(studentId='20240001',username='测试',departName='学院',grade='2025',specialtyName='原专业',naturalclassName='原班级',beginDate='2026-10-27 23:00:00',endDate='2026-10-28 00:00:00')
        actual=fallback_plan(source,'班级不可选')
        self.assertEqual(actual['specialtyName'],'机器人工程')
        self.assertEqual(actual['naturalclassName'],'2025机器人1班')
        for k in ('studentId','username','departName','grade','beginDate','endDate'):
            self.assertEqual(actual[k],source[k])
        self.assertEqual(source['specialtyName'],'原专业')
        self.assertEqual(actual['original_specialtyName'],'原专业')
        self.assertEqual(actual['original_naturalclassName'],'原班级')
        self.assertEqual(actual['fallback_reason'],'班级不可选')

    def test_2023_grade_is_not_overwritten_by_2025_fallback_class(self):
        p=fallback_plan(dict(studentId='2023001',grade='2023',specialtyName='原专业',naturalclassName='原班级'),'不可选')
        self.assertEqual(p['grade'],'2023')
        self.assertEqual(p['naturalclassName'],'2025机器人1班')

if __name__=='__main__': unittest.main()
