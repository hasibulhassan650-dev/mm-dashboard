"use client";
import * as XLSX from "xlsx";

interface Props {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data?: Record<string, any>[];
  /** Several named sheets in one workbook. When given, `data` is ignored.
   *  Used by the schedule export, which ships the monthly table and the
   *  individual coupons and maturities behind it, so any figure in the summary
   *  can be traced to the securities paying it. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  sheets?: { name: string; rows: Record<string, any>[] }[];
  filename: string;
  label?: string;
}

export default function DownloadButton({ data, sheets, filename, label = "Download Excel" }: Props) {
  function download() {
    const wb = XLSX.utils.book_new();
    if (sheets?.length) {
      for (const s of sheets) {
        // Excel rejects a sheet name over 31 chars or containing []:*?/\
        const name = s.name.replace(/[[\]:*?/\\]/g, " ").slice(0, 31);
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(s.rows), name);
      }
    } else {
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(data ?? []), "Data");
    }
    XLSX.writeFile(wb, `${filename}.xlsx`);
  }
  return (
    <button
      onClick={download}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 11px", borderRadius: 7,
        fontSize: 11.5, fontWeight: 500, fontFamily: "var(--sans)",
        background: "var(--accent-soft)", color: "var(--accent)",
        border: "1px solid color-mix(in oklab, var(--accent) 35%, transparent)",
      }}
    >
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M6 1v7M3 5l3 3 3-3M1 9v1a1 1 0 001 1h8a1 1 0 001-1V9" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
      {label}
    </button>
  );
}
