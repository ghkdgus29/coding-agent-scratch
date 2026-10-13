import unittest
from config import load_config


class TestConfig(unittest.TestCase):
    def test_types(self):
        cfg = load_config({"workers": "8", "name": "svc", "ratio": "0.25", "unknown": "x"})
        self.assertEqual(cfg["workers"], 8)
        self.assertEqual(cfg["name"], "svc")
        self.assertEqual(cfg["ratio"], 0.25)
        self.assertNotIn("unknown", cfg)

    def test_defaults(self):
        self.assertFalse(load_config({})["debug"])
