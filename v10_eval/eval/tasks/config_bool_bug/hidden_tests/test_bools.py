import unittest
from config import load_config


class TestBools(unittest.TestCase):
    def test_false_values(self):
        for v in ["false", "False", "0", "no", "OFF"]:
            self.assertIs(load_config({"debug": v})["debug"], False, v)

    def test_true_values(self):
        for v in ["true", "TRUE", "1", "yes", "on"]:
            self.assertIs(load_config({"debug": v})["debug"], True, v)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            load_config({"debug": "maybe"})
