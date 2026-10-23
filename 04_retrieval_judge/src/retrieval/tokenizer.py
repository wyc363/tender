from __future__ import annotations

import re
import unicodedata

import jieba


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text).strip()


class ChineseTokenizer:
    def __init__(self, custom_terms=()):
        self.engine = jieba.Tokenizer()
        for term in dict.fromkeys(custom_terms):
            normalized = normalize(term)
            if normalized:
                self.engine.add_word(normalized, freq=1_000_000)

    def tokenize(self, text: str) -> list[str]:
        return [token.strip() for token in self.engine.cut(normalize(text), HMM=False)
                if token.strip()]
