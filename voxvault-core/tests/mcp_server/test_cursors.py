"""Cursors and page building, as pure logic.

These are the pieces the tool tests exercise indirectly. Testing them directly
is worth it because their failure modes are silent: a cursor accepted across a
changed base, or a page ended without saying so, both produce a plausible
answer that is wrong.
"""

from __future__ import annotations

import pytest

from voxvault.mcp.cursors import (
    STABLE_BASE,
    CursorError,
    CursorInvalidated,
    Tool,
    decode,
    issue,
)
from voxvault.mcp.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_RESPONSE_CHARS,
    build_page,
    clamp_page_size,
    paginate_text,
)


# -- cursors -----------------------------------------------------------

def test_a_cursor_round_trips() -> None:
    raw = issue(Tool.TIMELINE, (1000, "mic", 7), "rev-1")
    cursor = decode(raw, expected=Tool.TIMELINE, base="rev-1")
    assert cursor.position == (1000, "mic", 7)


def test_a_cursor_is_opaque() -> None:
    """Opaque so nobody builds one by hand and relies on its shape."""
    raw = issue(Tool.TIMELINE, (1000, "mic", 7), "rev-1")
    assert "mic" not in raw
    assert "1000" not in raw


def test_another_tools_cursor_is_refused() -> None:
    raw = issue(Tool.MEETINGS, (0, "x"), STABLE_BASE)
    with pytest.raises(CursorError, match="emitido por"):
        decode(raw, expected=Tool.TIMELINE, base=STABLE_BASE)


def test_a_cursor_over_a_changed_base_is_refused() -> None:
    raw = issue(Tool.TIMELINE, (0, "mic", 1), "rev-1")
    with pytest.raises(CursorInvalidated, match="revisao ativa"):
        decode(raw, expected=Tool.TIMELINE, base="rev-2")


def test_the_invalidation_message_says_what_to_do() -> None:
    raw = issue(Tool.NOTE_CONTENT, (0,), "hash-1")
    with pytest.raises(CursorInvalidated) as caught:
        decode(raw, expected=Tool.NOTE_CONTENT, base="hash-2")
    message = str(caught.value)
    assert "nota foi alterada" in message
    assert "Reinicie a leitura" in message


def test_garbage_is_refused_without_crashing() -> None:
    for junk in ("", "nao-e-base64!!", "YWJj", "e30="):
        with pytest.raises(CursorError):
            decode(junk, expected=Tool.TIMELINE, base="rev-1")


def test_listings_use_a_base_that_never_invalidates() -> None:
    """A meeting created mid-listing must not break the read in progress."""
    raw = issue(Tool.MEETINGS, (0, "x"), STABLE_BASE)
    assert decode(raw, expected=Tool.MEETINGS, base=STABLE_BASE).position == (0, "x")


# -- page sizes --------------------------------------------------------

def test_page_size_defaults_and_is_capped() -> None:
    assert clamp_page_size(None) == DEFAULT_PAGE_SIZE
    assert clamp_page_size(0) == DEFAULT_PAGE_SIZE
    assert clamp_page_size(-5) == DEFAULT_PAGE_SIZE
    assert clamp_page_size(50) == 50
    assert clamp_page_size(10_000) == MAX_PAGE_SIZE


# -- building a page ---------------------------------------------------

def _items(count: int, text: str = "ola"):
    return [{"n": n, "texto": text} for n in range(count)]


def _page(items, **kwargs):
    return build_page(
        iter(items),
        render=lambda i: dict(i),
        text_of=lambda r: r["texto"],
        cursor_of=lambda i: f"cursor-{i['n']}",
        **kwargs,
    )


def test_a_short_page_declares_no_continuation() -> None:
    page = _page(_items(3), page_size=10)
    assert len(page.items) == 3
    assert page.has_more is False
    assert page.as_dict().get("proximo_cursor") is None


def test_the_item_limit_ends_the_page_and_declares_more() -> None:
    page = _page(_items(10), page_size=4)
    assert len(page.items) == 4
    assert page.has_more is True
    assert page.as_dict()["proximo_cursor"] == "cursor-3"


def test_the_character_limit_ends_the_page_at_a_whole_item() -> None:
    chunk = "x" * 20_000
    page = _page(_items(10, chunk), page_size=200)
    assert page.has_more is True
    assert all(i["texto"] == chunk for i in page.items), (
        "nenhum item pode ser cortado ao meio pelo limite da pagina"
    )
    assert sum(len(i["texto"]) for i in page.items) <= MAX_RESPONSE_CHARS


def test_an_item_larger_than_the_whole_budget_is_cut_and_declared() -> None:
    giant = "x" * (MAX_RESPONSE_CHARS + 1000)
    page = _page([{"n": 0, "texto": giant}, {"n": 1, "texto": "seguinte"}])

    assert page.truncated_items == 1
    assert page.items[0]["truncado"] is True
    assert len(page.items[0]["texto"]) <= MAX_RESPONSE_CHARS
    assert "truncado" in page.items[0]["texto"]
    assert page.has_more is True
    assert page.as_dict()["proximo_cursor"] == "cursor-0", (
        "o cursor tem de avancar, senao a paginacao trava nesse item para sempre"
    )
    assert "aviso" in page.as_dict()


def test_truncation_is_never_silent() -> None:
    giant = "x" * (MAX_RESPONSE_CHARS + 1000)
    payload = _page([{"n": 0, "texto": giant}]).as_dict()
    assert payload["itens_truncados"] == 1
    assert "aviso" in payload


def test_an_empty_source_is_a_valid_empty_page() -> None:
    page = _page([])
    assert page.items == []
    assert page.has_more is False


# -- text paging -------------------------------------------------------

def test_long_text_is_paged_by_character_offset() -> None:
    content = "a" * (MAX_RESPONSE_CHARS * 2 + 17)

    window, nxt = paginate_text(content, 0)
    assert len(window) == MAX_RESPONSE_CHARS
    assert nxt == MAX_RESPONSE_CHARS

    collected = window
    while nxt is not None:
        window, nxt = paginate_text(content, nxt)
        collected += window

    assert collected == content, "percorrer tudo tem de devolver o texto inteiro"


def test_short_text_has_no_continuation() -> None:
    window, nxt = paginate_text("curto", 0)
    assert window == "curto"
    assert nxt is None


def test_a_negative_offset_is_treated_as_the_start() -> None:
    window, _ = paginate_text("abcdef", -10)
    assert window == "abcdef"
