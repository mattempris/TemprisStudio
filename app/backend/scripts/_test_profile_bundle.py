"""Every anchor role document as one volume. Offline — no network, no PDF renderer.

The volume is assembled by lifting the body out of each stored document and wrapping the lot in
one shell. That is only safe because the markup is ours, so the tests here are the things that
would make it unsafe: a nested document leaking through, a hand-built contents page emitting raw
markup from a role title, and a document silently vanishing because its HTML did not match.

PDF rendering is deliberately not exercised. It needs WeasyPrint's native libraries, which are
absent on a Windows backend — so this covers everything up to the renderer, and the renderer's
own absence is covered by the 503 path in the route.

Run:  python scripts/_test_profile_bundle.py
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.project_state import JobProfileDoc
from app.services.job_profile import bundle

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))


NOW = datetime.now(timezone.utc)
STYLE = "<style>.card{color:#111}@page{margin:18mm}</style>"


def doc(key: str, title: str, *, body: str | None = None, style: str = STYLE) -> JobProfileDoc:
    inner = body if body is not None else f'<main class="page"><article>{title} body</article></main>'
    return JobProfileDoc(
        profile_key=key,
        profile_cluster_id=0,
        clustering_version=1,
        title=title,
        content={},
        html=f"<!doctype html><html><head>{style}</head><body>{inner}</body></html>",
        generated_at=NOW,
    )


print("\nOne shell, one stylesheet, one sheet per role")
docs = [doc("a", "Analyst"), doc("b", "Engineer"), doc("c", "Manager")]
html, skipped = bundle.combine_html(docs, company="Acme", levels={"a": "Coordinator"})
check("nothing skipped", skipped == [])
check("one sheet per document", html.count('<div class="profile-sheet">') == 3)
check("every body made it", all(f"{d.title} body" in html for d in docs))
# The failure this guards: lifting a body without stripping its wrapper leaves a document
# inside a document, which no renderer handles predictably.
for tag, want in (("<!doctype", 1), ("<html", 1), ("</html>", 1), ("<body", 1), ("</body>", 1)):
    check(f"exactly {want} {tag}", html.lower().count(tag) == want, str(html.lower().count(tag)))
check("the shared stylesheet appears once", html.count(".card{color:#111}") == 1)
check("and the bundle's own rules are separate", html.count("<style") == 2)

print("\nEach role starts on a fresh sheet, and the volume does not end on a blank one")
check("a page break per sheet", "break-after: page" in html)
check("the last sheet does not break", ".profile-sheet:last-of-type { break-after: auto; }" in html)
check("the contents page breaks too",
      bool(re.search(r"\.bundle-contents \{[^}]*break-after: page", html)))

print("\nA contents page listing every role that made it")
check("one row per document", html.count('class="c-title"') == 3)
check("the level travels with the row", "Coordinator" in html)
check("a role with no evaluated level still gets a row", html.count('class="c-meta"') == 3)
check("the count is stated", "3 roles" in html)
check("and reads as singular for one", "1 role<" in bundle.combine_html([doc("a", "Solo")], company="A")[0])

print("\nThe hand-built contents page escapes what it interpolates")
# Everything else here came out of Jinja with autoescape on. This line did not, so a title with
# an ampersand or an angle bracket in it would emit broken markup — or worse.
# A body that does NOT echo the title, so the only place the raw form could appear is the
# contents page — which is the thing being tested.
danger = bundle.combine_html(
    [doc("x", 'R&D <Lead> "Chief"', body="<main>safe body</main>")],
    company="Smith & Sons <Ltd>",
    levels={"x": "VP & Head"},
)[0]
check("an ampersand in a title", "R&amp;D" in danger)
check("angle brackets in a title", "&lt;Lead&gt;" in danger and "<Lead>" not in danger)
check("an ampersand in the company name", "Smith &amp; Sons" in danger)
check("angle brackets in the company name", "&lt;Ltd&gt;" in danger)
check("an ampersand in the level", "VP &amp; Head" in danger)

print("\nA document that cannot be read is named, not dropped in silence")
mixed = [doc("good", "Fine"), doc("bad", "Broken", body=""), doc("also", "Also Fine")]
html2, skipped2 = bundle.combine_html(mixed, company="Acme")
# A volume quietly missing three roles is worse than one that says which three.
check("the unreadable one is reported", skipped2 == ["bad"], str(skipped2))
check("the readable ones are still in", html2.count('<div class="profile-sheet">') == 2)
check("and it is left out of the contents", html2.count('class="c-title"') == 2)
check("the count reflects what is actually there", "2 roles" in html2)

print("\nRefusing is better than emitting an empty volume")
try:
    bundle.combine_html([], company="Acme")
    check("no documents raises", False)
except bundle.NothingToBundle as e:
    check("no documents raises", True, str(e)[:52])
try:
    bundle.combine_html([doc("a", "A", body="")], company="Acme")
    check("no READABLE documents raises", False)
except bundle.NothingToBundle as e:
    check("no READABLE documents raises", True, str(e)[:52])

print("\nOrder is the caller's, because only the caller knows the hierarchy")
ordered = [doc("z", "Zulu"), doc("a", "Alpha")]
h3 = bundle.combine_html(ordered, company="Acme")[0]
check("given order is preserved", h3.index("Zulu body") < h3.index("Alpha body"))
check("and the contents agrees with it", h3.index(">Zulu<") < h3.index(">Alpha<"))

print("\nMismatched stylesheets are survivable rather than fatal")
# Taking the first sheet renders every document readably; refusing to export would be worse.
odd = [doc("a", "A"), doc("b", "B", style="<style>.card{color:#f00}</style>")]
h4, sk4 = bundle.combine_html(odd, company="Acme")
check("it still produces a volume", h4.count('<div class="profile-sheet">') == 2 and sk4 == [])
check("using the first stylesheet", ".card{color:#111}" in h4 and ".card{color:#f00}" not in h4)

print("\nA document with no stylesheet at all does not crash the volume")
h5, sk5 = bundle.combine_html([doc("a", "A", style="")], company="Acme")
check("body still present", "A body" in h5)
check("and nothing skipped", sk5 == [])

print("ZIP: one file per role, plus an index that links them")
data, sk = bundle.zip_per_role(
    [doc("aaa11111", "Head of Risk"), doc("bbb22222", "Analyst")],
    company="Acme", levels={"aaa11111": "Director"},
)
z = zipfile.ZipFile(io.BytesIO(data))
check("the archive is not corrupt", z.testzip() is None)
names = z.namelist()
check("one file per role plus the index", len(names) == 3 and "index.html" in names)
check("nothing skipped", sk == [])
roles = [n for n in names if n != "index.html"]
# A zip lists alphabetically and the useful order is the architecture's, which alphabetical
# destroys — hence the numeric prefix.
check("numbered so the given order survives sorting", roles == sorted(roles), str(roles))
check("named from the title, not the opaque key", "head-of-risk" in roles[0])
check("and the key disambiguates two roles sharing a title", roles[0].endswith("aaa11111.html"))
# Each file gets emailed and opened alone, so it cannot depend on its siblings.
one = z.read(roles[0]).decode()
check("a role file is a whole document", one.lstrip().lower().startswith("<!doctype"))
check("with its styles inline", "<style>" in one)
check("and is the stored document verbatim", one == doc("aaa11111", "Head of Risk").html)
# The index is read by double-clicking it in an unpacked folder: relative siblings, no fetching.
idx = z.read("index.html").decode()
check("the index links every role file", all('href="' + n + '"' in idx for n in roles))
check("the level travels into the index", "Director" in idx)
check("the index needs no server", "http://" not in idx and "https://" not in idx)

print("ZIP: escaping, and reporting what it left out")
danger, _ = bundle.zip_per_role([doc("k1", "R&D <Lead>")], company="Smith & Sons")
dz = zipfile.ZipFile(io.BytesIO(danger))
i2 = dz.read("index.html").decode()
check("an ampersand in the index", "R&amp;D" in i2 and "Smith &amp; Sons" in i2)
check("angle brackets in the index", "&lt;Lead&gt;" in i2)
check("the filename carries no unsafe characters",
      all(ch.isalnum() or ch in "._-" for n in dz.namelist() for ch in n))
blank = JobProfileDoc(profile_key="blank", profile_cluster_id=0, clustering_version=1,
                      title="Blank", content={}, html="", generated_at=NOW)
_, sk3 = bundle.zip_per_role([doc("ok", "Fine"), blank], company="A")
check("a document with no stored HTML is named, not dropped", sk3 == ["blank"], str(sk3))
try:
    bundle.zip_per_role([], company="A")
    check("an empty project raises rather than shipping an empty archive", False)
except bundle.NothingToBundle:
    check("an empty project raises rather than shipping an empty archive", True)

print("\nPASS\n" if ok else "\nFAIL\n")
sys.exit(0 if ok else 1)
