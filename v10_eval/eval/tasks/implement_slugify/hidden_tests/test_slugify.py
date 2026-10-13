import unittest
from text_utils import slugify


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_accents(self):
        self.assertEqual(slugify("Café Crème brûlée"), "cafe-creme-brulee")

    def test_collapse_and_trim(self):
        self.assertEqual(slugify("  --A  &&  B--  "), "a-b")

    def test_max_length_no_trailing_dash(self):
        self.assertEqual(slugify("abc def ghi", max_length=4), "abc")

    def test_empty(self):
        self.assertEqual(slugify("!!!"), "n-a")
