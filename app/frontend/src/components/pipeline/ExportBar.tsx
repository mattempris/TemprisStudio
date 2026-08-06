import { useEffect, useState } from "react";
import { Download, FileSpreadsheet, FileText } from "lucide-react";
import { Button } from "../ui/Button";

/**
 * Export surface for the whole project.
 *
 * Two primary actions, because they are for different readers. The workbook is the working
 * artefact — a consultant wants one file with every sheet, not eight downloads. The HTML report
 * is the deliverable: a guided read of the finished architecture for the client, with no method
 * in it. Neither substitutes for the other, so neither is buried.
 *
 * Individual CSVs are secondary, listed with their row counts, and only datasets that actually
 * have rows are offered: a download that produces an empty file reads as a bug.
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
  reportUrl: string;
  csvUrl: (dataset: string) => string;
}

export function ExportBar({ manifest, workbookUrl, reportUrl, csvUrl }: Props) {
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
        <span className="flex flex-wrap items-center gap-2">
          <Button variant="primary" onClick={() => window.open(workbookUrl, "_blank")}>
            <span className="flex items-center gap-1.5">
              <FileSpreadsheet size={12} /> Export job architecture (.xlsx)
            </span>
          </Button>
          {/* Opened rather than downloaded: it is meant to be read, and putting a file manager
              between the button and the page is friction for no gain. Saving it is one keypress
              away once it is open. */}
          <Button onClick={() => window.open(reportUrl, "_blank")}>
            <span className="flex items-center gap-1.5">
              <FileText size={12} /> Export HTML report
            </span>
          </Button>
        </span>
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
