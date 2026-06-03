"use client";
import * as React from "react";
import * as XLSX from "xlsx";
import { api } from "@/lib/api";
import { Icon } from "@/components/terminal/Icon";

// One-click export of the ENTIRE dataset (full history) as a multi-sheet .xlsx.
export default function ExportAll() {
  const [busy, setBusy] = React.useState(false);

  async function exportAll() {
    if (busy) return;
    setBusy(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      const range = await api.yieldDateRange().catch(() => ({ min: "2007-01-01", max: today, count: 0 }));
      const yFrom = range.min ?? "2007-01-01";
      const yTo = range.max ?? today;

      // Fetch every dataset's FULL history in parallel; each guarded so one
      // failure doesn't sink the whole export.
      const [
        yields, omoTxns, omoOut, cm, fx, refrate, securities, flows, macro, monetary,
      ] = await Promise.all([
        api.yieldsRange(yFrom, yTo).catch(() => []),
        api.omoTransactions(3650).catch(() => []),
        api.omoOutstanding(3650).catch(() => []),
        api.callmoney(0).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null })),
        api.fx(0).catch(() => []),
        api.refrate(0).catch(() => []),
        api.securities().catch(() => []),
        api.flows(120).catch(() => []),
        api.macro().catch(() => ({ series: [], latest: null })),
        api.monetary().catch(() => ({ monthly: [], latest: null, reserve_requirements: { current: null, history: [] } })),
      ]);

      const wb = XLSX.utils.book_new();
      const add = (name: string, rows: Record<string, unknown>[]) => {
        if (rows && rows.length) XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), name.slice(0, 31));
      };

      add("Yield Auctions", yields as unknown as Record<string, unknown>[]);
      add("OMO Transactions", omoTxns as unknown as Record<string, unknown>[]);
      add("OMO Outstanding", omoOut as unknown as Record<string, unknown>[]);
      add("Call Money Daily", cm.daily_summary as unknown as Record<string, unknown>[]);
      add("Call Money Breakdown", cm.latest_breakdown as unknown as Record<string, unknown>[]);
      add("FX Auctions", fx as unknown as Record<string, unknown>[]);
      add("Reference Rates", refrate as unknown as Record<string, unknown>[]);
      add("Securities", securities as unknown as Record<string, unknown>[]);
      add("Cash Flows", flows as unknown as Record<string, unknown>[]);
      add("Reserves & Remittance", macro.series as unknown as Record<string, unknown>[]);
      add("Monetary & Prices", monetary.monthly as unknown as Record<string, unknown>[]);
      add("Reserve Requirements", monetary.reserve_requirements.history as unknown as Record<string, unknown>[]);

      if (wb.SheetNames.length === 0) {
        // nothing came back — still give the user a clear, empty workbook
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet([{ note: "No data available" }]), "Empty");
      }
      XLSX.writeFile(wb, `bb_market_intelligence_full_${today}.xlsx`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className="icon-btn"
      onClick={exportAll}
      disabled={busy}
      title="Download ALL data (full history, every dataset) as Excel"
      aria-label="Download all data"
      style={{ width: "auto", padding: "0 11px", gap: 7, display: "inline-flex", alignItems: "center", fontSize: 12, fontWeight: 500, opacity: busy ? 0.6 : 1 }}
    >
      <Icon name="download" size={15} />
      <span className="export-all-label">{busy ? "Exporting…" : "Export all"}</span>
    </button>
  );
}
