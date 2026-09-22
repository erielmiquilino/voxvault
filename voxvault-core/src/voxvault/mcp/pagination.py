"""Building one page, under two independent ceilings.

There are two limits and they are not interchangeable: a count of items, and a
count of characters. A meeting of two hours can blow the character budget in
forty segments or survive two hundred short ones, so only enforcing the item
count would still hand an agent a response that swamps its context.

The rule that matters most: **nothing is ever truncated silently.** A response
cut without saying so would have the agent reason about half a meeting while
believing it read the whole thing, and nothing downstream could tell.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

#: Page size when the caller asks for none.
DEFAULT_PAGE_SIZE = 200
#: Hard ceiling on the page size a caller may ask for.
MAX_PAGE_SIZE = 1000
#: Characters of text in one response, whatever the item count.
MAX_RESPONSE_CHARS = 60_000

#: Appended to an item that had to be cut, so the cut is visible in the text
#: itself and not only in a flag the reader might not look at.
TRUNCATION_MARK = " […truncado]"

T = TypeVar("T")


@dataclass(slots=True)
class Page:
    items: list[Any] = field(default_factory=list)
    has_more: bool = False
    next_cursor: str = ""
    truncated_items: int = 0

    def as_dict(self) -> dict:
        payload: dict[str, Any] = {
            "itens": self.items,
            "ha_mais": self.has_more,
        }
        if self.has_more and self.next_cursor:
            payload["proximo_cursor"] = self.next_cursor
        if self.truncated_items:
            payload["itens_truncados"] = self.truncated_items
            payload["aviso"] = (
                f"{self.truncated_items} item(ns) tiveram o texto truncado por "
                f"excederem sozinhos o limite de {MAX_RESPONSE_CHARS} caracteres."
            )
        return payload


def clamp_page_size(requested: int | None) -> int:
    if requested is None or requested <= 0:
        return DEFAULT_PAGE_SIZE
    return min(int(requested), MAX_PAGE_SIZE)


def build_page(
    source: Iterable[T],
    *,
    render: Callable[[T], dict],
    text_of: Callable[[dict], str],
    cursor_of: Callable[[T], str],
    page_size: int | None = None,
    max_chars: int = MAX_RESPONSE_CHARS,
) -> Page:
    """Consume from ``source`` until a ceiling is hit, then stop cleanly.

    ``source`` must be a lazy iterator over one item *beyond* what fits, so
    that "is there more" is answered by looking rather than by counting the
    whole set first.
    """
    limit = clamp_page_size(page_size)
    page = Page()
    used = 0

    for item in source:
        if len(page.items) >= limit:
            page.has_more = True
            break

        rendered = render(item)
        text = text_of(rendered)

        if len(text) > max_chars:
            # A single item larger than the whole budget. Truncate it and
            # advance: omitting it would lose content, and refusing it would
            # wedge the pagination on an item it can never get past.
            keep = max_chars - len(TRUNCATION_MARK)
            _replace_text(rendered, text[:keep] + TRUNCATION_MARK)
            rendered["truncado"] = True
            page.items.append(rendered)
            page.truncated_items += 1
            page.next_cursor = cursor_of(item)
            page.has_more = True
            return page

        if used + len(text) > max_chars and page.items:
            # Ending here keeps every item in the page whole.
            page.has_more = True
            break

        page.items.append(rendered)
        used += len(text)
        page.next_cursor = cursor_of(item)

    if not page.has_more:
        page.next_cursor = ""
    return page


def _replace_text(rendered: dict, replacement: str) -> None:
    for key in ("texto", "conteudo", "recorte"):
        if key in rendered:
            rendered[key] = replacement
            return
    rendered["texto"] = replacement


def paginate_text(
    content: str, offset: int, *, max_chars: int = MAX_RESPONSE_CHARS
) -> tuple[str, int | None]:
    """Cut one window out of a long string, by character offset.

    Used for reading a note whole: its ordering is position in the content,
    which is the only total ordering a single string has.
    """
    if offset < 0:
        offset = 0
    window = content[offset:offset + max_chars]
    next_offset = offset + len(window)
    return window, (next_offset if next_offset < len(content) else None)
