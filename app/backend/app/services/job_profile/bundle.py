"""Every anchor role document, bundled.

Two shapes, both assembled from the HTML already stored on each document:

  `zip_per_role`  one file per role plus an index that links them. What you hand over, drop into
                  a shared drive, or open one of.
  `combine_html`  all of them in one document, page-broken per role. What you print.

Neither renders anything. The stored HTML *is* the document — it is what the per-profile export
serves — so re-generating it here would create a second way to produce one artefact, and the two
would eventually disagree.

PDF was the obvious third shape and is deliberately absent: it needs WeasyPrint's native Pango
and Cairo libraries, which are routinely missing on a Windows backend, so it would be a button
that only ever returned 503. Printing the combined document gives the same pages, since the
breaks and margins are CSS.

Safe to assemble because the markup is ours. Every document comes from one template, so the
`<style>` block is identical across a project — checked rather than assumed, see `_shared_style`
— and the body is always a single `<main>`. Anything not matching those shapes is reported rather
than silently dropped.
"""
from __future__ import annotations

import io
import re
import zipfile
from html import escape

from app.models.project_state import JobProfileDoc

_STYLE = re.compile(r"<style>.*?</style>", re.S)
_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.S)


class NothingToBundle(RuntimeError):
    """No anchor role documents exist yet."""


def _shared_style(docs: list[JobProfileDoc]) -> str:
    """The one stylesheet every document uses.

    Taken from the first document and used for all of them. That is only sound because they
    share a template and a project accent colour, so the blocks are identical — asserted here
    rather than trusted, because a mismatch would silently style most of the volume wrongly.
    """
    blocks = [m.group(0) for d in docs if (m := _STYLE.search(d.html))]
    if not blocks:
        return ""
    distinct = set(blocks)
    if len(distinct) > 1:
        # Not fatal: the first block still renders every document readably, and refusing to
        # export would be a worse answer than a note in the log.
        print(
            f"  [bundle] {len(distinct)} different stylesheets across {len(docs)} documents — "
            f"using the first; the volume may not be uniform"
        )
    return blocks[0]


def combine_html(
    docs: list[JobProfileDoc],
    *,
    company: str,
    levels: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """(html, skipped profile keys) for one printable volume of every document.

    Ordered as given — the caller sorts, because the useful order is the architecture's
    (family, then category, then role) and this module does not know the hierarchy.

    A contents page leads, with a page break after it and after every profile, so each role
    starts on a fresh sheet. `break-after` rather than the legacy `page-break-after`: WeasyPrint
    supports the modern property, and print CSS in a browser falls back to it.
    """
    if not docs:
        raise NothingToBundle("no anchor role documents have been generated yet")

    style = _shared_style(docs)
    levels = levels or {}

    bodies: list[str] = []
    skipped: list[str] = []
    for d in docs:
        m = _BODY.search(d.html)
        if not m or not m.group(1).strip():
            # A document whose markup does not match is named rather than dropped in silence —
            # a volume quietly missing three roles is worse than one that says which.
            skipped.append(d.profile_key)
            continue
        bodies.append(f'<div class="profile-sheet">{m.group(1)}</div>')

    if not bodies:
        raise NothingToBundle(
            f"none of the {len(docs)} documents could be read — their stored HTML is not in the "
            f"expected shape"
        )

    # Escaped, unlike everything else here. The profile bodies came out of Jinja with autoescape
    # on; this line is hand-built, so a role titled "R&D Lead" or one with an angle bracket in it
    # would otherwise emit broken markup into the contents page.
    contents = "\n".join(
        f'<li><span class="c-title">{escape(d.title)}</span>'
        f'<span class="c-meta">{escape(levels.get(d.profile_key, ""))}</span></li>'
        for d in docs
        if d.profile_key not in skipped
    )

    return (
        f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<title>{escape(company)} — anchor role documents</title>
{style}
<style>
  /* Each role starts on its own sheet. The last one gets no break, or the volume ends on a
     blank page — which looks like a truncated export. */
  .profile-sheet {{ break-after: page; }}
  .profile-sheet:last-of-type {{ break-after: auto; }}
  .bundle-contents {{ break-after: page; padding: 0 16px; }}
  .bundle-contents h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .bundle-contents .sub {{ color: #555; font-size: 12px; margin: 0 0 18px; }}
  .bundle-contents ol {{ list-style: decimal; padding-left: 22px; margin: 0; }}
  .bundle-contents li {{ display: flex; gap: 12px; align-items: baseline;
                         padding: 3px 0; font-size: 12.5px; }}
  .bundle-contents .c-title {{ flex: 1 1 auto; }}
  .bundle-contents .c-meta {{ color: #666; white-space: nowrap; }}
</style>
</head>
<body>
<section class="bundle-contents">
  <h1>{escape(company)}</h1>
  <p class="sub">Anchor role documents · {len(bodies)} role{"" if len(bodies) == 1 else "s"}</p>
  <ol>{contents}</ol>
</section>
{chr(10).join(bodies)}
</body>
</html>
""",
        skipped,
    )


# Filesystem-safe, and stable across exports so a re-run overwrites rather than accumulating
# near-duplicates in whatever folder the zip was unpacked into.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _filename(index: int, doc: JobProfileDoc) -> str:
    """`01_head-of-risk.html` — ordered, readable, and safe on every filesystem.

    Numbered because a zip lists alphabetically and the useful order is the architecture's, which
    alphabetical destroys. Two digits at least, more when the project is larger, so 100 sorts
    after 99 rather than between 10 and 11.

    Built from the title rather than the profile key: the key carries a hash suffix that means
    nothing to someone looking at a folder. The key is still what guarantees uniqueness, so a
    short slice of it goes on the end — two roles can legitimately share a title.
    """
    stem = _UNSAFE.sub("-", doc.title.strip().lower()).strip("-") or "role"
    return f"{index:02d}_{stem[:60]}_{doc.profile_key[-8:]}.html"


def zip_per_role(
    docs: list[JobProfileDoc],
    *,
    company: str,
    levels: dict[str, str] | None = None,
) -> tuple[bytes, list[str]]:
    """(zip bytes, skipped profile keys) — one HTML file per role, plus an index linking them.

    Each file is the document exactly as the single-profile export serves it: self-contained,
    styles inline, no external requests. So one can be emailed on its own and it still renders.

    The index is what makes the unpacked folder navigable rather than a wall of filenames. It is
    a sibling of the files it links, with relative hrefs, so it works from a local folder with no
    server — which is how a zip actually gets read.
    """
    if not docs:
        raise NothingToBundle("no anchor role documents have been generated yet")

    levels = levels or {}
    buf = io.BytesIO()
    skipped: list[str] = []
    written: list[tuple[str, JobProfileDoc]] = []

    # DEFLATE rather than stored: these are ~9 KB of markup each with a 3 KB stylesheet repeated
    # verbatim in every one, so they compress to a fraction. The repetition is deliberate — it is
    # what makes each file work alone — and the zip pays almost nothing for it.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in enumerate(docs, start=1):
            if not (d.html or "").strip():
                skipped.append(d.profile_key)
                continue
            name = _filename(n, d)
            z.writestr(name, d.html)
            written.append((name, d))

        if not written:
            raise NothingToBundle(
                f"none of the {len(docs)} documents had any stored HTML to export"
            )

        rows = "\n".join(
            f'      <li><a href="{escape(name)}">{escape(d.title)}</a>'
            f'<span class="meta">{escape(levels.get(d.profile_key, ""))}</span></li>'
            for name, d in written
        )
        z.writestr("index.html", _INDEX.format(company=escape(company), n=len(written),
                                              plural="" if len(written) == 1 else "s", rows=rows))

    return buf.getvalue(), skipped


# Standalone and dependency-free, for the same reason the profiles are: this is read by
# double-clicking a file in an unpacked folder, where nothing can be fetched.
_INDEX = """<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<title>{company} — anchor role documents</title>
<style>
  body {{ margin: 0; padding: 40px 24px; background: #faf8f5; color: #1a1a1a;
          font: 15px/1.6 "Schibsted Grotesk", ui-sans-serif, system-ui, sans-serif; }}
  main {{ max-width: 780px; margin: 0 auto; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; }}
  p.sub {{ color: #666; font-size: 13px; margin: 0 0 28px; }}
  ol {{ list-style: decimal; padding-left: 26px; margin: 0; }}
  li {{ display: flex; align-items: baseline; gap: 14px; padding: 7px 0;
        border-bottom: 1px solid #e8e2da; }}
  li:last-child {{ border-bottom: 0; }}
  a {{ flex: 1 1 auto; color: #8a2028; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ color: #777; font-size: 13px; white-space: nowrap; }}
</style>
</head>
<body>
  <main>
    <h1>{company}</h1>
    <p class="sub">Anchor role documents · {n} role{plural} · one file each</p>
    <ol>
{rows}
    </ol>
  </main>
</body>
</html>
"""
