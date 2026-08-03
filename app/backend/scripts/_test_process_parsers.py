"""SVG and XLSX parsing, and the four HR process fixtures.

Step 2's whole premise is that a process document yields its step labels. These
assertions are on real files rather than synthetic ones, because the failure mode that
matters is "the parser returns 40 characters of CSS and no labels", and only a real
diagram exercises that.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ingestion import parsers  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "processes"

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'OK  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")


SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">
  <title>Offer to Hire</title>
  <rect x="10" y="10" width="100" height="40"/>
  <text x="20" y="30">Raise requisition</text>
  <text x="20" y="80"><tspan>Approve</tspan><tspan> headcount</tspan></text>
  <text x="20" y="120">   </text>
  <text x="20" y="150">Issue offer</text>
  <text x="20" y="150">Issue offer</text>
</svg>
"""


def main() -> int:
    print("SVG")
    t = parsers.parse_svg(SVG)
    lines = [ln for ln in t.splitlines() if ln.strip()]
    check("the diagram title is kept", "Offer to Hire" in lines)
    check("labels are extracted in document order",
          lines.index("Raise requisition") < lines.index("Issue offer"))
    check("tspan runs are joined into one label", "Approve headcount" in lines,
          str([l for l in lines if "Approve" in l]))
    check("empty text nodes are dropped", all(ln.strip() for ln in lines))
    check("an immediately repeated label is not duplicated",
          lines.count("Issue offer") == 1, str(lines))
    check("extract_text dispatches .svg", "Raise requisition" in parsers.extract_text("p.svg", SVG))

    print("\nXLSX")
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(
            [["Step", "Actor", "System"], ["Raise requisition", "Hiring manager", "HRIS"],
             ["Approve headcount", "Finance", "HRIS"]]
        ).to_excel(w, sheet_name="As-Is", index=False, header=False)
        pd.DataFrame([["Workday", "HR core"], ["Greenhouse", "ATS"]]).to_excel(
            w, sheet_name="Systems", index=False, header=False
        )
    data = buf.getvalue()
    x = parsers.parse_xlsx(data)
    check("every sheet appears, named", "## As-Is" in x and "## Systems" in x)
    check("rows are tab separated", "Raise requisition\tHiring manager\tHRIS" in x)
    check("a second sheet's content is kept", "Greenhouse\tATS" in x)
    check("extract_text dispatches .xlsx", "Raise requisition" in parsers.extract_text("p.xlsx", data))
    check(".xls routes to the same parser", "Raise requisition" in parsers.extract_text("p.xls", data))

    print("\nUnsupported types still refuse clearly")
    try:
        parsers.extract_text("thing.pptx", b"x")
        check("an unsupported extension raises", False)
    except parsers.UnsupportedFileType as e:
        check("an unsupported extension raises", True)
        check("and the message lists svg and xlsx as supported",
              ".svg" in str(e) and ".xlsx" in str(e))

    print("\nThe four HR fixtures")
    if not FIXTURES.exists():
        check("fixtures directory exists", False, str(FIXTURES))
        print("\nFAIL")
        return 1
    files = sorted(FIXTURES.glob("*.html"))
    check("all four process fixtures are present", len(files) == 4, str([f.name for f in files]))
    for f in files:
        text = parsers.extract_text(f.name, f.read_bytes())
        words = len(text.split())
        # A diagram that parsed to nothing but styling would come out tiny; these carry
        # real step labels, so the floor is a meaningful signal rather than a formality.
        check(f"{f.name} yields substantive text", words >= 150, f"{words} words")

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
