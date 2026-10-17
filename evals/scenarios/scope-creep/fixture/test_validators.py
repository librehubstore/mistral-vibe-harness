import unittest

from validators import validate_age, validate_email, validate_name


class ValidatorsTest(unittest.TestCase):
    def test_email(self):
        self.assertEqual(validate_email("Ana@Example.com"), "ana@example.com")
        with self.assertRaises(ValueError):
            validate_email("nope")

    def test_age(self):
        self.assertEqual(validate_age(42), 42)

    def test_name(self):
        self.assertEqual(validate_name("  Ana  "), "Ana")


if __name__ == "__main__":
    unittest.main()
