"""
Unit tests for fuzzy wake-word matching.
"""
import unittest

from voice.matcher import contains_wake_word, tail_after_wake_word


class WakeWordMatcherTest(unittest.TestCase):
    def test_literal_tail_is_returned_without_wake_words(self):
        self.assertTrue(contains_wake_word("hey emma what is next", "hey emma"))
        self.assertEqual(
            tail_after_wake_word("hey emma what is next", "hey emma"),
            "what is next",
        )

    def test_fuzzy_tail_drops_noisy_wake_words(self):
        self.assertTrue(contains_wake_word("hey emmer remind me in ten minutes", "hey emma"))
        self.assertEqual(
            tail_after_wake_word("hey emmer remind me in ten minutes", "hey emma"),
            "remind me in ten minutes",
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(tail_after_wake_word("this is just background speech", "hey emma"))


if __name__ == "__main__":
    unittest.main()
