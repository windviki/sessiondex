"""Tokenization for the FTS5 index.

Strategy (CJK-aware, substring-friendly):

* ASCII/alphanumeric runs are lowercased and indexed as word tokens.
* CJK runs (Han, kana, hangul, CJK punctuation ranges) are indexed as all
  consecutive character bigrams (a 1-char run stays a single token). A phrase
  query over the bigrams of the query run then behaves like a substring match.

The same tokenizer runs at index time and query time, so mixed queries such
as ``搜索 ch011`` work as AND of ``"搜索"`` and ``"ch011"``.
"""
from __future__ import annotations

import re

# Han + extensions, kana, hangul, CJK compat, fullwidth forms, CJK punctuation
_CJK = (
    "\u2e80-\u2eff\u3000-\u303f\u3040-\u30ff\u3130-\u318f\u31a0-\u31ff"
    "\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f"
    "\uff00-\uffef\U00020000-\U0002a6df\U0002a700-\U0002ebe0"
)
_CJK_RUN = re.compile(f"[{_CJK}]+")
_LATIN_RUN = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Return index tokens for *text* (words + CJK bigrams)."""
    tokens: list[str] = []
    pos = 0
    for m in _CJK_RUN.finditer(text):
        tokens.extend(w.lower() for w in _LATIN_RUN.findall(text[pos : m.start()]))
        run = m.group()
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        pos = m.end()
    tokens.extend(w.lower() for w in _LATIN_RUN.findall(text[pos:]))
    return tokens


def index_text(text: str) -> str:
    """Space-joined token stream stored in the FTS5 external-content column."""
    return " ".join(tokenize(text))


def fts_query(query: str) -> str:
    """Build an FTS5 MATCH expression: AND of per-run phrases."""
    parts: list[str] = []
    pos = 0
    for m in _CJK_RUN.finditer(query):
        for word in _LATIN_RUN.findall(query[pos : m.start()]):
            parts.append(f'"{word.lower()}"')
        run = m.group()
        bigrams = [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]
        parts.append('"' + " ".join(bigrams) + '"')
        pos = m.end()
    for word in _LATIN_RUN.findall(query[pos:]):
        parts.append(f'"{word.lower()}"')
    return " ".join(parts)
