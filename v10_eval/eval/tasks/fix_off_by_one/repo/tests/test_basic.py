import unittest
from pagination import paginate


class TestBasic(unittest.TestCase):
    def test_invalid_page(self):
        with self.assertRaises(ValueError):
            paginate([1, 2, 3], 0)
