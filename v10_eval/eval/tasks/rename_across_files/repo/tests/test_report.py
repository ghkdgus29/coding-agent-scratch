import unittest
from shop.report import summary


class TestReport(unittest.TestCase):
    def test_summary(self):
        self.assertEqual(summary([[(10.0, 2)], [(1.5, 2)]]), {"count": 2, "revenue": 23.0})
