import unittest
from pagination import page_count, paginate


class TestPagination(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(paginate(list(range(25)), 1), list(range(10)))

    def test_last_partial_page(self):
        self.assertEqual(paginate(list(range(25)), 3), [20, 21, 22, 23, 24])

    def test_page_count(self):
        self.assertEqual(page_count(list(range(25))), 3)
        self.assertEqual(page_count(list(range(20))), 2)
        self.assertEqual(page_count([]), 0)
