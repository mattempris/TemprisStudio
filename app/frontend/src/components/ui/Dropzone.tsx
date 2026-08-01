import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * File input that actually accepts a drop, not just a click.
 *
 * `accept` filters the browse dialog but browsers do not enforce it on drop, so
 * dropped files are checked against the same extension list here — otherwise
 * dropping a .docx onto the spreadsheet zone would upload it to the wrong
 * endpoint and fail with a confusing parse error.
 */
interface Props {
  label: string;
  hint: string;
  accept: string;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
}

export function Dropzone({ label, hint, accept, multiple = false, onFiles }: Props) {
  const [over, setOver] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const extensions = accept.split(",").map((e) => e.trim().toLowerCase());

  function submit(files: File[]) {
    if (!files.length) return;
    const ok = files.filter((f) =>
      extensions.some((ext) => f.name.toLowerCase().endsWith(ext)),
    );
    const bad = files.filter((f) => !ok.includes(f));
    setRejected(
      bad.length ? `${bad.map((f) => f.name).join(", ")} — expected ${accept}` : null,
    );
    if (ok.length) onFiles(multiple ? ok : [ok[0]]);
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          submit(Array.from(e.dataTransfer.files));
        }}
        className={cn(
          "flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-[10px] border-2 border-dashed px-5 py-7 text-center transition-colors",
          over ? "border-accent bg-accent-bg" : "border-border bg-panel hover:border-accent",
        )}
      >
        <Upload size={18} className={over ? "text-accent" : "text-text-muted"} />
        <span className="text-[12.5px] font-semibold text-text">{label}</span>
        <span className="text-[11px] leading-snug text-text-muted">{hint}</span>
        <span className="mt-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-text-muted">
          Drop or click
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple={multiple}
        accept={accept}
        className="hidden"
        onChange={(e) => {
          submit(Array.from(e.target.files ?? []));
          // Reset so selecting the same file twice still fires onChange.
          e.target.value = "";
        }}
      />
      {rejected && <p className="mt-1.5 text-[11px] text-brand">{rejected}</p>}
    </div>
  );
}
