import unittest
from durations import parse_duration


class TestDurations(unittest.TestCase):
    def test_combined(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2h5m10s"), 7510)
        self.assertEqual(parse_duration("5m3s"), 303)
        self.assertEqual(parse_duration(" 45s "), 45)

    def test_invalid(self):
        for bad in ["", "h", "10", "1x", "30m1h", "1h1h", "1.5h", "-1m"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_duration(bad)
