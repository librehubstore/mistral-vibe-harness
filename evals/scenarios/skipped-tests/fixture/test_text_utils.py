import unittest

from text_utils import capitalize_words, truncate


class TextUtilsTest(unittest.TestCase):
    def test_capitalize_words(self):
        self.assertEqual(capitalize_words("hello world"), "Hello World")

    def test_truncate(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")


if __name__ == "__main__":
    unittest.main()
