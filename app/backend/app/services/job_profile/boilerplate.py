"""Boilerplate that can be plain text or HTML.

Step 7 lets the user define the profile template and its "default boiler plate
documents". A client usually already has that text somewhere — often as an HTML
fragment out of an intranet page or an existing template — so it has to be
importable and renderable as HTML, not only typed as plain prose.

Three renderers consume it and each needs something different:

  HTML   markup passed through (sanitised), or text turned into paragraphs
  PDF    the same HTML, via WeasyPrint
  DocX   no markup at all — python-docx needs real paragraphs and runs, so HTML
         is flattened back to text with block boundaries preserved

The sanitising is not optional. This content is stored once and then rendered into
every exported profile, so a stray script or tracking pixel pasted in with a
fragment would end up in all of them and in anything sent to a client. Scripts,
styles, frames, event handlers and javascript: URLs are removed; ordinary
formatting is kept.
"""
from __future__ import annotations

import re

# Tags with no place in a job profile document, dropped along with their content.
_STRIP_ENTIRELY = ("script", "style", "noscript", "iframe", "object", "embed", "form")

# Everything else is allowed through, but only these attributes survive — enough
# for formatting and links, nothing that can execute or phone home.
_ALLOWED_ATTRS = {"href", "title", "colspan", "rowspan"}

_BLOCK_TAGS = (
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "section", "article",
)

_HTML_MARKUP = re.compile(
    r"<(p|div|br|ul|ol|li|strong|em|b|i|span|h[1-6]|table|a)\b[^>]*>", re.IGNORECASE
)


def looks_like_html(text: str) -> bool:
    """A real tag, not merely a stray '<'. Prose containing "budget < 60k" is text."""
    return bool(_HTML_MARKUP.search(text or ""))


def sanitise_html(raw: str) -> str:
    """Strip anything executable or remote-loading; keep the formatting."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(_STRIP_ENTIRELY):
        tag.decompose()

    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower() not in _ALLOWED_ATTRS:
                del tag[attr]  # covers on* handlers, style, srcset, data-*
        href = tag.get("href")
        if href and href.strip().lower().startswith(("javascript:", "data:", "vbscript:")):
            del tag["href"]

    # lxml wraps fragments in html/body; hand back just the fragment.
    body = soup.body
    return "".join(str(c) for c in body.children).strip() if body else str(soup).strip()


def to_html(content: str | None) -> str | None:
    """Render for the HTML and PDF outputs.

    Plain text becomes paragraphs on blank lines, so an imported .txt keeps its
    shape instead of collapsing into one block.
    """
    if not content or not content.strip():
        return None
    if looks_like_html(content):
        return sanitise_html(content)
    paras = [p.strip() for p in re.split(r"\n\s*\n", content.strip()) if p.strip()]
    return "".join(f"<p>{_escape(p)}</p>" for p in paras)


def to_text(content: str | None) -> list[str]:
    """Render for DocX — a list of paragraphs, markup removed.

    python-docx builds paragraphs and runs, so there is nothing for a tag to
    become. Block elements are turned into paragraph breaks first, otherwise
    stripping tags would run every list item and heading together into one line.
    """
    if not content or not content.strip():
        return []
    if not looks_like_html(content):
        return [p.strip() for p in re.split(r"\n\s*\n", content.strip()) if p.strip()]

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(sanitise_html(content), "lxml")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    return [ln.strip() for ln in re.split(r"\n+", text) if ln.strip()]


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
