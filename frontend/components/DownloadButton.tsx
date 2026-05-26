"use client";
import * as XLSX from "xlsx";

interface Props {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>[];
  filename: string;
  label?: string;
}

export default function DownloadButton({ data, filename, label = "Download Excel" }: Props) {
  function download() {
    const ws = XLSX.utils.json_to_sheet(data);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Data");
    XLSX.writeFile(wb, `${filename}.xlsx`);
  }
  return (
    <button
      onClick={download}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-teal-950 text-teal-400 border border-teal-800/60 hover:bg-teal-900/60 hover:text-teal-300 transition-colors"
    >
      <svg className="w-3 h-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M6 1v7M3 5l3 3 3-3M1 9v1a1 1 0 001 1h8a1 1 0 001-1V9" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
      {label}
    </button>
  );
}
