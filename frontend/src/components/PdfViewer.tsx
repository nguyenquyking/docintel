import { ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { Button } from "./ui";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export interface Highlight {
  page: number;
  boxes: number[][]; // [x0, top, x1, bottom] in PDF points, origin top-left
}

/** One page at a time (a 150-page contract stays fast), with evidence boxes drawn over it. */
export default function PdfViewer({ url, page, onPageChange, highlight, pageSizes }: {
  url: string;
  page: number;
  onPageChange: (p: number) => void;
  highlight: Highlight | null;
  pageSizes: Record<number, { width: number; height: number }>;
}) {
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1);
  const [width, setWidth] = useState(640);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(320, el.clientWidth - 32)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const renderWidth = width * scale;
  const size = pageSizes[page];
  const ratio = size ? renderWidth / size.width : 1;
  const boxes = highlight && highlight.page === page ? highlight.boxes : [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2">
        <Button size="sm" variant="ghost" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={page <= 1}>
          <ChevronLeft className="size-4" />
        </Button>
        <span className="tabular text-sm text-slate-600">
          Page{" "}
          <input value={page} onChange={(e) => { const n = Number(e.target.value); if (n >= 1 && n <= numPages) onPageChange(n); }}
            className="w-12 rounded border border-slate-300 px-1 text-center" /> / {numPages || "…"}
        </span>
        <Button size="sm" variant="ghost" onClick={() => onPageChange(Math.min(numPages, page + 1))} disabled={page >= numPages}>
          <ChevronRight className="size-4" />
        </Button>
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => setScale((s) => Math.max(0.5, s - 0.15))}><Minus className="size-4" /></Button>
          <span className="tabular w-10 text-center text-xs text-slate-500">{Math.round(scale * 100)}%</span>
          <Button size="sm" variant="ghost" onClick={() => setScale((s) => Math.min(2.5, s + 0.15))}><Plus className="size-4" /></Button>
        </div>
      </div>
      <div ref={box} className="scroll-thin flex-1 overflow-auto bg-slate-100 p-4">
        <Document file={url} onLoadSuccess={({ numPages: n }) => setNumPages(n)}
          loading={<div className="p-6 text-sm text-slate-500">Loading PDF…</div>}>
          <div className="relative mx-auto w-fit shadow-md">
            <Page pageNumber={page} width={renderWidth} renderTextLayer={false} renderAnnotationLayer={false} />
            {boxes.map((b, i) => (
              <div key={`${page}-${i}`} className="hl-box"
                style={{ left: b[0] * ratio - 2, top: b[1] * ratio - 2, width: (b[2] - b[0]) * ratio + 4, height: (b[3] - b[1]) * ratio + 4 }} />
            ))}
          </div>
        </Document>
      </div>
    </div>
  );
}
