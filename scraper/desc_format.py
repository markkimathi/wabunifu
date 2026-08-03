"""Turn a job posting's raw content into safe, structured HTML for display,
plus a short plain-text teaser for card previews.

Two independent paths feed the same output shape:

  - `_sanitize_html()` — the source already has real markup (Greenhouse,
    Lever, Ashby, ...): keep a small allowlist of structural/semantic
    tags, strip everything else down to its text (this is what cleans up
    collaborative-editor cruft like Dropbox's per-word `<span
    class="author-d-...">` wrappers for free), and never trust attributes
    beyond a validated `href`.

  - `_format_plain_text()` — the source is flat text (employer
    submissions via the plain <textarea> on /post, or any scraped
    posting where sanitizing turned up no block-level structure at all):
    heuristically detect paragraphs, bullet/numbered lists, and section
    headings, and build the same tag vocabulary by hand. This path only
    ever emits tags it constructs itself around html.escape()'d text, so
    it's safe regardless of what the input contains.

Both paths route text through `_strip_em_dashes` first.
"""
from __future__ import annotations
import html
import re
from bs4 import BeautifulSoup, NavigableString, Tag

MAX_HTML_CHARS = 8000  # accumulated visible-text budget, not raw markup length
TEASER_CHARS = 200

# h1 is deliberately excluded — a posting's own top heading shouldn't
# outrank the page's own <h1> job title (h1s from the source are demoted
# to h2 below rather than dropped).
ALLOWED_TAGS = {"h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "b", "em", "i", "br", "a", "hr"}
# Removed entirely (tag AND content) rather than unwrapped — their
# "content" isn't real page text.
STRIP_ENTIRELY = {"script", "style", "noscript", "iframe", "object", "embed", "svg", "img"}

EM_DASH_RE = re.compile(r"[–—]")  # en dash, em dash


def _strip_em_dashes(text: str) -> str:
    return EM_DASH_RE.sub("-", text)


def _clean_href(href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("http://", "https://", "mailto:")):
        return href
    return None


BLOCK_TAGS = {"p", "ul", "ol", "h2", "h3", "h4"}


def _wrap_loose_top_level_content(soup: BeautifulSoup) -> None:
    """Inline text/tags (a stray <a>, some bare text) sitting directly at
    the top level, not inside any block tag, would otherwise render
    unstyled and un-spaced by the .job-desc CSS, which only targets
    p/h3/ul/etc. Group consecutive non-block top-level nodes and wrap
    each run in a <p>."""
    groups: list[list] = []
    current: list = []
    for child in list(soup.children):
        if isinstance(child, Tag) and child.name in BLOCK_TAGS:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(child)
    if current:
        groups.append(current)

    for nodes in groups:
        has_content = any(
            (isinstance(n, NavigableString) and n.strip()) or isinstance(n, Tag)
            for n in nodes
        )
        if not has_content:
            for n in nodes:
                n.extract()
            continue
        p = soup.new_tag("p")
        nodes[0].insert_before(p)
        for n in nodes:
            p.append(n.extract())


def _sanitize_html(raw: str) -> str | None:
    """Returns sanitized HTML, or None if no block-level structure
    survived (signals the caller to fall back to plain-text formatting)."""
    soup = BeautifulSoup(raw, "html.parser")

    for tag in soup.find_all(STRIP_ENTIRELY):
        tag.decompose()

    for tag in soup.find_all(["h1", "h5", "h6"]):
        tag.name = "h2" if tag.name == "h1" else "h4"

    for tag in soup.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        if tag.name == "a":
            href = _clean_href(tag.get("href"))
            if not href:
                tag.unwrap()
                continue
            tag.attrs = {"href": href, "target": "_blank", "rel": "noopener nofollow"}
        else:
            tag.attrs = {}

    # Drop elements left empty by the unwrapping above (e.g. a <p> that
    # only ever held styling with no real text).
    for tag in soup.find_all(["p", "li", "h2", "h3", "h4"]):
        if not tag.get_text(strip=True):
            tag.decompose()

    # Collapse runs of consecutive <br> into a single one.
    for br in soup.find_all("br"):
        nxt = br.find_next_sibling()
        while nxt and getattr(nxt, "name", None) == "br":
            following = nxt.find_next_sibling()
            nxt.decompose()
            nxt = following

    for node in soup.find_all(string=True):
        if isinstance(node, NavigableString):
            node.replace_with(_strip_em_dashes(str(node)))

    _wrap_loose_top_level_content(soup)

    if not soup.find(["p", "ul", "ol", "h2", "h3", "h4"]):
        return None

    # Cap by walking elements and stopping once accumulated visible text
    # passes the budget, so a long posting is trimmed at an element
    # boundary rather than cutting a tag in half.
    kept: list[str] = []
    total = 0
    for child in list(soup.children):
        if isinstance(child, NavigableString):
            if not child.strip():
                continue
            kept.append(str(child))
            total += len(child)
        else:
            kept.append(str(child))
            total += len(child.get_text())
        if total >= MAX_HTML_CHARS:
            break

    out = "".join(kept).strip()
    return out or None


SECTION_HEADER_WORDS = re.compile(
    r"^(responsibilities|requirements|qualifications|about(?: the)?(?: role| team| company| position| job| you)?"
    r"|what you.?ll (?:do|bring|need|learn)|who you are|nice to have|preferred(?: qualifications| skills)?"
    r"|benefits|perks|compensation(?: (?:and|&) benefits)?|salary|why (?:join|work with) us|the role|the team|the opportunity"
    r"|minimum qualifications|skills(?:,| &| and)? experience|skills and requirements"
    r"|what we.?re looking for|day.to.day|key responsibilities|our stack|tech stack|how to apply|equal opportunity)s?:?$",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^\s*(?:([-*•‣◦])|(\d+[.)]))\s+(.*)$")
URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip().rstrip(":")
    if not stripped or len(stripped) > 60:
        return False
    if SECTION_HEADER_WORDS.match(stripped):
        return True
    if stripped[-1:] in ".,;":
        return False
    words = stripped.split()
    if not words:
        return False
    if stripped.isupper() and len(words) <= 8:
        return True
    if len(words) <= 8 and all(w[0].isupper() for w in words if w[:1].isalpha()):
        return True
    return False


def _linkify(escaped_text: str) -> str:
    return URL_RE.sub(
        lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener nofollow">{m.group(1)}</a>',
        escaped_text,
    )


def _format_plain_text(raw: str) -> str:
    text = _strip_em_dashes(raw.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")

    blocks: list[str] = []
    para_lines: list[str] = []
    list_items: list[str] = []
    list_tag = "ul"

    def flush_para():
        if para_lines:
            joined = " ".join(l.strip() for l in para_lines if l.strip())
            if joined:
                blocks.append(f"<p>{_linkify(html.escape(joined))}</p>")
            para_lines.clear()

    def flush_list():
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{_linkify(html.escape(i))}</li>" for i in list_items)
            blocks.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()
            list_tag = "ul"

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_para()
            flush_list()
            continue
        bullet_match = BULLET_RE.match(raw_line)
        if bullet_match:
            flush_para()
            this_tag = "ol" if bullet_match.group(2) else "ul"
            if list_items and this_tag != list_tag:
                flush_list()  # marker type changed mid-run — start a new list
            list_tag = this_tag
            list_items.append(bullet_match.group(3).strip())
            continue
        flush_list()
        if _looks_like_heading(line):
            flush_para()
            blocks.append(f"<h3>{html.escape(line.rstrip(':'))}</h3>")
            continue
        para_lines.append(line)

    flush_para()
    flush_list()

    out = "".join(blocks)
    if len(out) > MAX_HTML_CHARS:
        acc: list[str] = []
        total = 0
        for b in blocks:
            acc.append(b)
            total += len(b)
            if total >= MAX_HTML_CHARS:
                break
        out = "".join(acc)
    return out


def format_description(raw: str, is_html: bool) -> tuple[str, str]:
    """Single entry point. Returns (desc_html, desc_text) — safe
    structured HTML for the full details page, and a short plain-text
    teaser for card previews."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""

    desc_html = _sanitize_html(raw) if is_html else None
    if desc_html is None:
        # Either genuinely plain text, or "HTML" with no real block
        # structure — reformat from the visible text either way.
        plain_source = BeautifulSoup(raw, "html.parser").get_text("\n") if is_html else raw
        desc_html = _format_plain_text(plain_source)

    teaser_source = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
    teaser_source = re.sub(r"\s+", " ", teaser_source).strip()
    if len(teaser_source) > TEASER_CHARS:
        teaser_source = teaser_source[:TEASER_CHARS].rsplit(" ", 1)[0] + "…"

    return desc_html, teaser_source
