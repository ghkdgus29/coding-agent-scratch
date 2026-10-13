import io
import pathlib
import unittest
from contextlib import redirect_stdout

import shop.orders
from shop.cli import main


class TestRename(unittest.TestCase):
    def test_new_name(self):
        self.assertEqual(shop.orders.compute_total([(2.0, 3)]), 6.0)

    def test_old_name_gone_everywhere(self):
        self.assertFalse(hasattr(shop.orders, "calc_total"))
        for path in pathlib.Path("shop").glob("*.py"):
            self.assertNotIn("calc_total", path.read_text(), path)

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["10x2"])
        self.assertEqual(buf.getvalue().strip(), "22.0")
