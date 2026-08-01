import json
from pathlib import Path

BASE = Path(r"C:\Users\matt_\OneDrive\Desktop\JAStudio\jobMatching\backend\data")

texts = json.loads((BASE / "embeddings" / "taxonomy_texts_full.json").read_text(encoding="utf-8"))
print("texts:", type(texts).__name__, len(texts))
if isinstance(texts, dict):
    k = list(texts)[:2]
    print("  sample keys:", k)
    print("  sample value:", str(texts[k[0]])[:200])
else:
    print("  sample:", str(texts[0])[:200])

codes = json.loads((BASE / "embeddings" / "taxonomy_spec_codes_full.json").read_text(encoding="utf-8"))
print("\ncodes:", type(codes).__name__, len(codes))
print("  sample:", (list(codes.items())[:2] if isinstance(codes, dict) else codes[:3]))

lv = json.loads((BASE / "taxonomy" / "levelJson.txt").read_text(encoding="utf-8"))
print("\nlevels:", type(lv).__name__)
if isinstance(lv, dict):
    print("  keys:", list(lv)[:8])
    first = lv[list(lv)[0]]
    print("  first value:", json.dumps(first)[:300])
else:
    for s in lv[:3]:
        print("  stream:", s.get("careerStream"), [x.get("code") for x in s.get("levels", [])])
