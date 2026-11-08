import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from late_core import option_index

class OptionTests(unittest.TestCase):
    def test_identical_duplicate_options_can_select_first(self):
        options=[{'value':'','text':'--请选择--'}]+[{'value':'智能制造工程','text':'智能制造工程'}]*3
        self.assertEqual(option_index(options,'智能制造工程'),1)

    def test_same_value_with_different_labels_remains_ambiguous(self):
        with self.assertRaises(ValueError):
            option_index([{'value':'a','text':'甲'},{'value':'a','text':'乙'}],'a')

    def test_grade_uses_given_value_independent_of_student_id(self):
        options=[{'value':'2023','text':'2023'},{'value':'2025','text':'2025'}]
        self.assertEqual(option_index(options,'2025'),1)
        with self.assertRaises(ValueError):
            option_index(options,'2024')

if __name__=='__main__': unittest.main()
