# Test fixtures

**`hris_with_headcount.csv`** — a small HRIS extract with a headcount column.

The headcount analytics in the skills, task and architecture views are all
conditional on a headcount column being mapped, and the larger sample datasets
(`../../../../banking jobs.csv`, the sample JD files) do not have one — so this is
the only fixture that exercises that path.

Its column names are deliberately non-obvious — `Position Name`, `Role Summary`,
`Employee Grade`, `No. of Post Holders`, plus a decoy `Cost Centre` — because
that is the case `services/ingestion/column_mapping.py` exists to handle:
header-name matching alone cannot tell `No. of Post Holders` from `Cost Centre`,
and only the cell values disambiguate. Total headcount is 178 across 5 roles.
