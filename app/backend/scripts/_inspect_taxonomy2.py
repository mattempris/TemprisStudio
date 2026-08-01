import csv, json
from collections import Counter
from pathlib import Path

BASE = Path(r"C:\Users\matt_\OneDrive\Desktop\JAStudio\jobMatching\backend\data")

rows = list(csv.DictReader((BASE / "taxonomy" / "Job Catalogue.csv").open(encoding="utf-8-sig", newline="")))
print("rows:", len(rows))

for key in ["Specialization Code", "Sub Family Code", "Job Code"]:
    print(f"  unique {key}: {len(set(r[key] for r in rows))}")

pairs = {(r["Specialization Code"], r["Industry"]) for r in rows}
print("  unique (spec, industry):", len(pairs))
pairs2 = {(r["Specialization Code"], r["Career Stream Title"]) for r in rows}
print("  unique (spec, stream):", len(pairs2))
print("  unique (spec, industry, stream):",
      len({(r["Specialization Code"], r["Industry"], r["Career Stream Title"]) for r in rows}))

codes = json.loads((BASE / "embeddings" / "taxonomy_spec_codes_full.json").read_text(encoding="utf-8"))
texts = json.loads((BASE / "embeddings" / "taxonomy_texts_full.json").read_text(encoding="utf-8"))
print("\nreference index:", len(codes), "rows,", len(set(codes)), "unique codes")
c = Counter(codes)
print("  most repeated:", c.most_common(3))

# what distinguishes the repeats?
target = c.most_common(1)[0][0]
idxs = [i for i, x in enumerate(codes) if x == target]
print(f"\n  all texts for {target}:")
for i in idxs:
    print("   ", texts[str(i)])

print(f"\n  catalogue rows for {target}:")
for r in rows:
    if r["Specialization Code"] == target:
        print("   ", r["Industry"], "|", r["Career Level Title"], "|", r["Typical Titles"][:60])

print("\nindustry values are multi-valued:")
atoms = set()
for r in rows:
    for a in r["Industry"].split(","):
        if a.strip():
            atoms.add(a.strip())
print(" ", len(atoms), sorted(atoms))
