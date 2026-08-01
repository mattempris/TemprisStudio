"""3rd-party job taxonomy — loading and embedding.

instructions.txt step 11: "Enable matching of job profiles into 3rd party
taxonomy (use functionality in ./jobMatching)".

What is reused from `jobMatching/` and what is not, and why:

  REUSED — the taxonomy DATA (Job Catalogue.csv, levelJson.txt) and the matching
  ALGORITHM (embed → cosine shortlist → rerank → assign career level).

  NOT reused — its model stack. That app embeds with JobBERT-v3, reranks with
  Voyage AI and levels with Azure OpenAI, which would mean a fourth embedding
  model plus two more sets of credentials alongside this app's jobQWEN +
  Anthropic. Its precomputed taxonomy embeddings are JobBERT-v3 vectors, so they
  cannot be compared against jobQWEN vectors regardless — one side has to be
  re-embedded either way. Re-embedding the 5,659 specializations with jobQWEN is
  a one-time local GPU cost, keeps a single model stack, and uses the embedding
  model that was actually fine-tuned on job data.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# The reference data lives in the sibling jobMatching project rather than being
# duplicated into this app — it's ~9MB of client-licensed catalogue.
JOBMATCHING_DATA = Path(__file__).resolve().parents[5] / "jobMatching" / "backend" / "data"
CATALOGUE_CSV = JOBMATCHING_DATA / "taxonomy" / "Job Catalogue.csv"
LEVELS_JSON = JOBMATCHING_DATA / "taxonomy" / "levelJson.txt"


class TaxonomyUnavailable(FileNotFoundError):
    pass


# One spec can list a dozen near-identical typical titles ("Vendor Relations
# Manager", "Outsourcing Vendor Relations Manager", ...). Each becomes its own
# index row, so cap the explosion — past a handful they stop adding recall.
MAX_TITLE_VARIANTS = 12


@dataclass
class Specialization:
    """One matchable unit: a specialization, independent of career level.

    The catalogue has one row per (specialization x career level), so 25,035 rows
    collapse to 2,910 specializations. Matching is done at this level and the
    career level assigned separately — matching against all 25k rows would make
    level a matching signal rather than a judgement.

    `available_levels` spans career streams: a spec typically offers both a
    management ladder (M2-M5) and a professional one (P1-P5), and which ladder a
    profile belongs on is exactly what the level call has to decide.
    """

    code: str
    title: str
    family_title: str
    sub_family_title: str
    industries: list[str] = field(default_factory=list)  # atomic, not the raw joined string
    typical_titles: list[str] = field(default_factory=list)
    available_levels: list[tuple[str, str]] = field(default_factory=list)  # (code, title)

    def variant_texts(self) -> list[str]:
        """One text per typical title, each carrying the taxonomy path.

        This is how the reference indexes it, and it matters: collapsing a
        spec's titles into one string averages a dozen near-synonyms into a
        blurred centroid, so an input job matching one title exactly scores no
        better than one matching none. Separate vectors + max-pooling keeps the
        exact hit sharp.
        """
        path = f"{self.family_title} | {self.sub_family_title} | {self.title}"
        titles = self.typical_titles[:MAX_TITLE_VARIANTS] or [self.title]
        return [f"{t} | {path}" for t in titles]


@dataclass
class CareerLevel:
    stream: str
    code: str
    name: str
    description: str


def _level_code_from_title(career_level_title: str) -> str | None:
    """'Executive Tier 1 (ET1)' -> 'ET1'. The catalogue stores the display title;
    levelJson stores the code."""
    if "(" in career_level_title and ")" in career_level_title:
        return career_level_title.rsplit("(", 1)[1].split(")", 1)[0].strip()
    return career_level_title.strip() or None


def _atoms(raw: str) -> list[str]:
    """'Healthcare,Retail' -> ['Healthcare', 'Retail'].

    The Industry column holds a comma-joined SET, not a single value — 19 real
    industries appear in 81 combinations. Matching the raw string would drop
    every multi-industry spec from a single-industry filter.
    """
    return [a.strip() for a in raw.split(",") if a.strip()]


def load_specializations(industries: list[str] | None = None) -> list[Specialization]:
    """Load and dedupe the catalogue to specialization level.

    `industries` filters as the reference does — a client is matched against
    their own industries plus Cross Industry, not the entire catalogue. A spec
    is kept if ANY of its industries is wanted.
    """
    if not CATALOGUE_CSV.exists():
        raise TaxonomyUnavailable(
            f"job catalogue not found at {CATALOGUE_CSV}. The 3rd-party taxonomy "
            "lives in the sibling jobMatching project; check it is present."
        )

    wanted = {i.strip().lower() for i in industries} if industries else None
    if wanted is not None:
        wanted.add("cross industry")  # always in scope, per the reference

    by_code: dict[str, Specialization] = {}
    with CATALOGUE_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            spec_industries = _atoms(row.get("Industry") or "")
            if wanted is not None and not any(i.lower() in wanted for i in spec_industries):
                continue
            code = (row.get("Specialization Code") or "").strip()
            if not code:
                continue

            spec = by_code.get(code)
            if spec is None:
                spec = Specialization(
                    code=code,
                    title=(row.get("Specialization Title") or "").strip(),
                    family_title=(row.get("Family Title") or "").strip(),
                    sub_family_title=(row.get("Sub Family Title") or "").strip(),
                    industries=spec_industries,
                )
                by_code[code] = spec

            # Typical titles differ per career level (a manager row lists
            # "...Manager", a professional row "...Specialist"), so accumulate
            # across rows rather than taking only the first.
            for title in _atoms(row.get("Typical Titles") or ""):
                if title not in spec.typical_titles:
                    spec.typical_titles.append(title)

            level_title = (row.get("Career Level Title") or "").strip()
            level_code = _level_code_from_title(level_title)
            if level_code and not any(c == level_code for c, _ in spec.available_levels):
                spec.available_levels.append((level_code, level_title))

    return list(by_code.values())


def load_career_levels() -> dict[str, CareerLevel]:
    """Level code -> definition, flattened across career streams."""
    import json

    if not LEVELS_JSON.exists():
        raise TaxonomyUnavailable(f"level definitions not found at {LEVELS_JSON}")

    out: dict[str, CareerLevel] = {}
    for stream in json.loads(LEVELS_JSON.read_text(encoding="utf-8")):
        stream_name = stream.get("careerStream", "")
        for level in stream.get("levels", []):
            code = (level.get("code") or "").strip()
            if code:
                out[code] = CareerLevel(
                    stream=stream_name,
                    code=code,
                    name=(level.get("name") or "").strip(),
                    description=(level.get("description") or "").strip(),
                )
    return out


def list_industries() -> list[str]:
    """The 19 atomic industries, not the 81 comma-joined combinations."""
    if not CATALOGUE_CSV.exists():
        raise TaxonomyUnavailable(f"job catalogue not found at {CATALOGUE_CSV}")
    seen: set[str] = set()
    with CATALOGUE_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            seen.update(_atoms(row.get("Industry") or ""))
    return sorted(seen)


def cosine_shortlist(
    profile_vecs: np.ndarray,
    variant_vecs: np.ndarray,
    variant_offsets: np.ndarray,
    top_n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Top-N SPECIALIZATIONS per profile, max-pooled over their title variants.

    Both sides are L2-normalized by EmbeddingService, so a dot product is cosine
    similarity. A spec scores as its single best-matching title rather than its
    average, so a spec listing one exact-hit title among eleven unrelated ones
    isn't penalised for being broadly named.

    `variant_offsets` is the reduceat index — variants are stored grouped by
    spec, so offsets[i] is where spec i's variants begin.

    Returns (spec_indices, scores), each (n_profiles, top_n).
    """
    sims = profile_vecs @ variant_vecs.T                       # (n_prof, n_variants)
    spec_sims = np.maximum.reduceat(sims, variant_offsets, axis=1)  # (n_prof, n_specs)

    top_n = min(top_n, spec_sims.shape[1])
    idx = np.argpartition(-spec_sims, top_n - 1, axis=1)[:, :top_n]
    # argpartition doesn't order within the selection, so sort the shortlist
    rows = np.arange(spec_sims.shape[0])[:, None]
    order = np.argsort(-spec_sims[rows, idx], axis=1)
    idx = idx[rows, order]
    return idx, spec_sims[rows, idx]
