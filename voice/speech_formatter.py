"""
SentenceAccumulator - turns a trickle of LLM text chunks into sentences
ready to be spoken.

Emma's reply arrives over SSE one word-fragment at a time. Feeding those
raw chunks straight to the TTS engine would make her speak word-by-word
with an awkward pause after every fragment. This accumulator buffers the
stream and cuts it into sentence-sized pieces on punctuation boundaries:
as soon as a sentence is complete (and long enough to be worth speaking)
it's handed off for synthesis, so she starts talking while the reply is
still being generated.

Rules:
  - split on . ! ? : ... and newlines (closing quotes/parens stick to the sentence)
  - [TOOL:...] directives are removed - they're actions, not speech
  - stray markdown symbols (*, `, #, _) are stripped for clean reading
  - a sentence shorter than `min_chars` is held back and merged with the
    next one, so a fragment like "Oct" never gets spoken on its own
  - a sentence longer than `max_chars` is hard-split on a space, bounding
    how long a single CPU synthesis call takes
"""
from __future__ import annotations

import re

_TOOL_DIRECTIVE_RE = re.compile(r"\[TOOL:[^\]]*\]")
_MARKDOWN_RE = re.compile(r"[*_`#>|]")
_BOUNDARY_RE = re.compile(r"[.!?…:]+[\"')}\]]*|\n+")


class SentenceAccumulator:
    def __init__(self, min_chars: int = 15, max_chars: int = 320):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._pending = ""

    def feed(self, chunk: str) -> list[str]:
        """Feed one raw text chunk; returns any complete sentences it finished."""
        if not chunk:
            return []

        pieces, closed_last = self._split(self._filter(chunk))
        out: list[str] = []
        for i, piece in enumerate(pieces):
            self._pending += piece
            closed = (i < len(pieces) - 1) or closed_last

            # Nothing's been punctuated for a long time - hard-split on a
            # space so a single synthesis call stays bounded.
            while len(self._pending) >= self.max_chars:
                cut = self._pending.rfind(" ", 0, self.max_chars)
                if cut <= 0:
                    cut = self.max_chars
                sentence = self._pending[:cut].strip()
                self._pending = self._pending[cut:].lstrip()
                if sentence:
                    out.append(sentence)

            if closed and len(self._pending.strip()) >= self.min_chars:
                sentence = self._pending.strip()
                if sentence:
                    out.append(sentence)
                self._pending = ""

        return out

    def flush(self) -> str:
        """Return whatever is left at the end of the stream, then reset."""
        tail = self._pending.strip()
        self._pending = ""
        return tail

    @staticmethod
    def _filter(raw: str) -> str:
        text = _TOOL_DIRECTIVE_RE.sub(" ", raw)
        return _MARKDOWN_RE.sub("", text)

    @staticmethod
    def _split(text: str) -> tuple[list[str], bool]:
        """
        Cut `text` on sentence boundaries. Returns the pieces and whether
        the final piece was itself a completed sentence. The (possibly
        empty) leftover tail without a boundary keeps streaming text
        flowing even mid-sentence without emitting a fragment.
        """
        pieces: list[str] = []
        pos = 0
        for m in _BOUNDARY_RE.finditer(text):
            pieces.append(text[pos:m.end()])
            pos = m.end()
        closed_last = bool(pieces)
        tail = text[pos:]
        if tail.strip():
            pieces.append(tail)
            closed_last = False
        return pieces, closed_last