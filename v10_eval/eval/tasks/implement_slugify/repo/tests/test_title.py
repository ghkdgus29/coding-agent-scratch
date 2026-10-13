import unittest
from text_utils import title_case


class TestTitle(unittest.TestCase):
    def test_title(self):
        self.assertEqual(title_case("hello world"), "Hello World")
