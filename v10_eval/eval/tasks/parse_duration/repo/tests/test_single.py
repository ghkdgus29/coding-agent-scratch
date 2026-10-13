import unittest
from durations import parse_duration


class TestSingle(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("2h"), 7200)
