"""HRIS / job-description spreadsheet ingestion (XLSX, XLS, CSV).

instructions.txt allows a spreadsheet dump as an alternative (or addition) to
individual JD files, with the app estimating which columns hold the job title,
description and level, plus an optional headcount column — user confirms.

HRIS exports commonly have a few junk rows above the real header (report titles,
export timestamps, merged cells), so `header_row` is overridable rather than
assumed to be row 0.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
MAX_PREVIEW_ROWS = 10
MAX_SAMPLE_VALUES = 5


class UnsupportedSpreadsheetType(ValueError):
    pass


@dataclass
class ColumnProfile:
    """Compact per-column summary used to build the column-mapping LLM prompt —
    name plus a few sample values, not the whole column."""

    name: str
    dtype: str
    non_null_count: int
    sample_values: list[str]


@dataclass
class SpreadsheetLoad:
    df: pd.DataFrame
    columns: list[str]
    row_count: int
    preview: list[dict]
    profiles: list[ColumnProfile]


def load_spreadsheet(filename: str, data: bytes, *, header_row: int = 0) -> SpreadsheetLoad:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedSpreadsheetType(
            f"unsupported spreadsheet type '{ext}' — supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if ext == ".csv":
        df = pd.read_csv(io.BytesIO(data), header=header_row, dtype=str, keep_default_na=False)
    else:
        engine = "openpyxl" if ext == ".xlsx" else None
        df = pd.read_excel(io.BytesIO(data), header=header_row, dtype=str, engine=engine)
        df = df.fillna("")

    # drop fully-empty columns and Unnamed spacer columns that Excel exports add
    df = df.loc[:, [c for c in df.columns if str(c).strip() and not str(c).startswith("Unnamed:")]]
    df.columns = [str(c).strip() for c in df.columns]

    if df.empty:
        raise ValueError("spreadsheet contains no data rows")
    if len(df.columns) < 2:
        raise ValueError("spreadsheet must contain at least 2 columns")

    profiles = [_profile_column(df, col) for col in df.columns]
    preview = df.head(MAX_PREVIEW_ROWS).to_dict(orient="records")

    return SpreadsheetLoad(
        df=df,
        columns=list(df.columns),
        row_count=len(df),
        preview=preview,
        profiles=profiles,
    )


def _profile_column(df: pd.DataFrame, col: str) -> ColumnProfile:
    series = df[col].astype(str)
    non_empty = series[series.str.strip() != ""]
    samples = [_truncate(v) for v in non_empty.head(MAX_SAMPLE_VALUES).tolist()]
    return ColumnProfile(
        name=col,
        dtype=_infer_dtype(non_empty),
        non_null_count=int(len(non_empty)),
        sample_values=samples,
    )


def _infer_dtype(series: pd.Series) -> str:
    if series.empty:
        return "empty"
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        return "integer" if (numeric.dropna() % 1 == 0).all() else "number"
    lengths = series.str.len()
    mean_len = float(lengths.mean()) if len(lengths) else 0.0
    return "long_text" if mean_len > 200 else "text"


def _truncate(value: str, limit: int = 300) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[:limit] + "…"
