import unittest

from price import format_price


class FormatPriceTest(unittest.TestCase):
    def test_default_is_euro(self):
        self.assertEqual(format_price(12.5), "12.50 €")


if __name__ == "__main__":
    unittest.main()
