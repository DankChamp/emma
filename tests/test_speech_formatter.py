"""
Unit tests for the voice streaming sentence accumulator. Run with:
    python -m unittest discover tests
"""
import unittest

from voice.speech_formatter import SentenceAccumulator


class SentenceAccumulatorTest(unittest.TestCase):
    def test_word_fragments_are_merged_until_sentence_complete(self):
        acc = SentenceAccumulator()
        out = []
        for frag in ["Oct", "op", "uses", " are", " smart", "."]:
            out.extend(acc.feed(frag))
        # Fragments are held until the boundary lands and the sentence is
        # long enough to speak.
        self.assertEqual(out, ["Octopuses are smart."])
        self.assertEqual(acc.flush(), "")

    def test_short_sentence_waits_for_the_next_one(self):
        acc = SentenceAccumulator(min_chars=15)
        acc.feed("Yes?")
        self.assertEqual(acc.feed(" "), [])  # "Yes?" is under min_chars
        out = acc.feed(" Sure, go ahead.")
        self.assertEqual(out, ["Yes? Sure, go ahead."])

    def test_tool_directives_never_spoken(self):
        acc = SentenceAccumulator()
        out = acc.feed("[TOOL:Aqua:research] Let me check that for you.")
        self.assertEqual(out, ["Let me check that for you."])
        self.assertEqual(acc.flush(), "")

    def test_markdown_stripped(self):
        acc = SentenceAccumulator()
        out = acc.feed("**Great** news: the *build* passed. # Done")
        self.assertEqual(out, ["Great news: the build passed."])
        self.assertEqual(acc.flush(), "Done")

    def test_long_unpunctuated_stream_is_hard_split(self):
        acc = SentenceAccumulator(min_chars=5, max_chars=20)
        out = acc.feed("one two three four five six seven eight nine ten eleven twelve")
        self.assertTrue(all(len(s) <= 20 for s in out))
        self.assertTrue(len(out) >= 2, out)
        # The last chunk is still mid-stream when the test stops feeding, so
        # it's left for flush() - which is exactly what an LLM stream end does.
        self.assertEqual(acc.flush(), "eleven twelve")

    def test_flush_returns_remaining_tail(self):
        acc = SentenceAccumulator()
        acc.feed("Still mid-sentence and the stream")
        self.assertEqual(acc.flush(), "Still mid-sentence and the stream")
        # After a flush it's reusable.
        small = SentenceAccumulator(min_chars=5)
        self.assertEqual(small.feed("Hello."), ["Hello."])

    def test_newline_is_a_boundary(self):
        acc = SentenceAccumulator(min_chars=5)
        self.assertEqual(acc.feed("First line\nSecond line"), ["First line"])
        self.assertEqual(acc.flush(), "Second line")

    def test_closing_quote_sticks_to_sentence(self):
        acc = SentenceAccumulator(min_chars=5)
        self.assertEqual(
            acc.feed('She said "hi back." then left.'),
            ['She said "hi back."', "then left."],
        )

    def test_ellipsis_is_a_boundary(self):
        acc = SentenceAccumulator(min_chars=5)
        self.assertEqual(acc.feed("Hmm... let me think."), ["Hmm...", "let me think."])


if __name__ == "__main__":
    unittest.main()