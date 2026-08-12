"""Zero-dependency local embedding for semantic memory recall.

``CharNgramEmbedder`` hashes character n-grams (CJK) and word tokens (Latin)
into a fixed-dimension vector using the hashing trick. It requires no model,
no network and is deterministic, so the semantic recall path works
out-of-the-box and is fully testable.

It captures shared-substring / near-paraphrase similarity (e.g. "晚上十点之后
请勿推送通知" vs "晚上几点后不要被打扰"). True synonym paraphrase with no
shared characters still needs a trained or LLM embedding model — which plugs
into ``SemanticMemoryIndex`` through the same ``transport`` callable.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Sequence

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]+")
_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*", re.IGNORECASE)


class CharNgramEmbedder:
    """Deterministic bag-of-character-ngram embedder (hashing trick)."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = max(64, int(dimensions))

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        """Satisfy the ``EmbeddingTransport`` callable contract."""
        return self.embed(texts)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            if not token:
                continue
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            bucket = int.from_bytes(digest, "big") % self.dimensions
            vector[bucket] += 1.0
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude > 0:
            return [value / magnitude for value in vector]
        return vector

    @staticmethod
    def exact_similarity(left: str, right: str) -> float:
        """Return collision-free cosine similarity over the local features."""
        left_features = Counter(_tokens(left))
        right_features = Counter(_tokens(right))
        if not left_features or not right_features:
            return 0.0
        dot = sum(
            count * right_features.get(token, 0)
            for token, count in left_features.items()
        )
        left_magnitude = math.sqrt(sum(count * count for count in left_features.values()))
        right_magnitude = math.sqrt(sum(count * count for count in right_features.values()))
        return dot / (left_magnitude * right_magnitude)


def _tokens(text: str) -> list[str]:
    normalized = str(text or "").lower()
    tokens = list(_LATIN_RE.findall(normalized))
    for run in _CJK_RE.findall(normalized):
        tokens.append(run)
        for size in (2, 3):
            tokens.extend(
                run[index : index + size]
                for index in range(len(run) - size + 1)
            )
    return tokens
