"""Token counting. The report always says which counter it used.

Order of preference:
  1. Anthropic count_tokens API  (exact for Claude; needs ANTHROPIC_API_KEY)
  2. tiktoken cl100k_base        (good proxy; offline once its vocab is cached)
  3. chars / 4                   (labelled "estimate")
Force one with LEANTEXT_TOKENIZER=anthropic|tiktoken|estimate.
"""
from __future__ import annotations

import functools
import os

_MODEL = os.environ.get("LEANTEXT_COUNT_MODEL", "claude-sonnet-5-5")


@functools.lru_cache(maxsize=1)
def _tiktoken():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def _anthropic(text: str) -> int:
    import anthropic

    r = anthropic.Anthropic().messages.count_tokens(
        model=_MODEL, messages=[{"role": "user", "content": text or " "}]
    )
    return r.input_tokens


@functools.lru_cache(maxsize=1)
def backend() -> str:
    forced = os.environ.get("LEANTEXT_TOKENIZER")
    if forced:
        return forced
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401

            return "anthropic"
        except ImportError:
            pass
    try:
        _tiktoken().encode("ok")
        return "tiktoken"
    except Exception:  # noqa: BLE001 - missing package or vocab download blocked
        return "estimate"


def count(text: str) -> int:
    b = backend()
    if b == "anthropic":
        return _anthropic(text)
    if b == "tiktoken":
        return len(_tiktoken().encode(text, disallowed_special=()))
    return max(0, round(len(text) / 4))


def truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    """Cut text to roughly max_tokens on a paragraph boundary, with a visible marker."""
    total = count(text)
    if total <= max_tokens:
        return text, False
    keep = int(len(text) * max_tokens / total * 0.97)
    cut = text.rfind("\n\n", 0, keep)
    cut = cut if cut > keep * 0.6 else keep
    marker = f"\n\n[... leantext: truncated at ~{max_tokens} tokens of {total}; use --max-tokens to raise ...]\n"
    return text[:cut].rstrip() + marker, True
