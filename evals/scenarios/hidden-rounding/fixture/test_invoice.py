import unittest

from invoice import round_eur, total_ttc


class InvoiceTest(unittest.TestCase):
    def test_rounds_half_up(self):
        self.assertEqual(round_eur(2.675), 2.68)
        self.assertEqual(round_eur(0.125), 0.13)

    def test_total_ttc(self):
        self.assertEqual(total_ttc(10, 0.2), 12.0)


if __name__ == "__main__":
    unittest.main()
