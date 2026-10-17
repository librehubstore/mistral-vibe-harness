import unittest

from words import join_words


class WordsTest(unittest.TestCase):
    def test_join(self):
        self.assertEqual(join_words(["a", "b"]), "a b")


if __name__ == "__main__":
    unittest.main()
