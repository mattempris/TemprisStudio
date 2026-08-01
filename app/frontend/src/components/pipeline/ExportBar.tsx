import { useEffect, useState } from "react";
import { Download, FileSpreadsheet } from "lucide-react";
import { Button } from "../ui/Button";

/**
 * Export surface for the whole project.
 *
 * The workbook is the primary action — a consultant wants one file with every
 * sheet, not eight downloads. Individual CSVs are secondary, listed with their
 * row counts, and only datasets that actually have rows are offered: a download
 * that produces an empty file reads as a bug.
 */

interface DatasetInfo {
  key: string;
  name: string;
  rows: number;
  columns: number;
}

interface Props {
  manifest: () => Promise<{ datasets: DatasetInfo[] }>;
  workbookUrl: string;
  csvUrl: (dataset: string) => string;
}

export function ExportBar({ manifest, workbookUrl, csvUrl }: Props) {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);

  useEffect(() => {
    void manifest()
      .then((m) => setDatasets(m.datasets))
      .catch(() => setDatasets([]));
  }, [manifest]);

  if (!datasets.length) return null;

  const totalRows = datasets.reduce((a, d) => a + d.rows, 0);

  return (
    <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[12.5px] font-semibold text-text">Export</p>
          <p className="text-[11.5px] text-text-muted">
            {datasets.length} datasets, {totalRows.toLocaleString()} rows
          </p>
        </div>
        <Button variant="primary" onClick={() => window.open(workbookUrl, "_blank")}>
          <span className="flex items-center gap-1.5">
            <FileSpreadsheet size={12} /> Download full workbook (.xlsx)
          </span>
        </Button>
      </div>
      <ul className="mt-2.5 flex flex-wrap gap-1.5 border-t border-border pt-2.5">
        {datasets.map((d) => (
          <li key={d.key}>
            <a
              href={csvUrl(d.key)}
              className="flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[11px] font-semibold text-text-secondary transition-colors hover:border-accent-border hover:text-accent"
            >
              <Download size={10} />
              {d.name}
              <span className="tabular-nums text-text-muted">{d.rows}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
