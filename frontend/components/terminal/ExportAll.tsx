"use client";
import * as React from "react";
import * as XLSX from "xlsx";
import { api } from "@/lib/api";
import { Icon } from "@/components/terminal/Icon";
import { todayDhaka } from "@/lib/format";

// One-click export of the ENTIRE dataset (full history) as a multi-sheet .xlsx.
export default function ExportAll() {
  const [busy, setBusy] = React.useState(false);

  async function exportAll() {
    if (busy) return;
    setBusy(true);
    try {
      const today = todayDhaka();
      // A dataset that fails to load is NOT silently left out of the workbook: it is listed on a "NOT INCLUDED" sheet.
      const failed: string[] = [];
      const ok = <T,>(name: string, p: Promise<T>, fallback: T): Promise<T> =>
        p.catch(() => { failed.push(name); return fallback; });
      const range = await ok("Yield date range", api.yieldDateRange(), { min: "2007-01-01" as string | null, max: today as string | null, count: 0 });
      const yFrom = range.min ?? "2007-01-01";
      const yTo = range.max ?? today;

      // Fetch every dataset's FULL history in parallel; each guarded so one
      // failure doesn't sink the whole export.
      const [
        yields, omoTxns, omoOut, cm, fx, refrate, securities, flows, macro, monetary,
        ibfx, fxrates, repo,
      ] = await Promise.all([
        ok("Yield Auctions", api.yieldsRange(yFrom, yTo), []),
        ok("OMO Transactions", api.omoTransactions(3650), []),
        ok("OMO Outstanding", api.omoOutstanding(3650), []),
        ok("Call Money", api.callmoney(0), { daily_summary: [], latest_breakdown: [], latest_date: null }),
        ok("FX Auctions", api.fx(0), []),
        ok("Reference Rates", api.refrate(0), []),
        ok("Securities", api.securities(), []),
        ok("Cash Flows", api.flows(120), []),
        ok("Reserves & Remittance", api.macro(), { series: [], latest: null }),
        ok("Monetary & Prices", api.monetary(), { monthly: [], latest: null, reserve_requirements: { current: null, history: [] } }),
        ok("Interbank FX", api.interbankFx(0), []),
        ok("Exchange Rates", api.fxRates(0), []),
        ok("Interbank Repo", api.interbankRepo(0), []),
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
      add("Interbank FX", ibfx as unknown as Record<string, unknown>[]);
      add("Exchange Rates", fxrates as unknown as Record<string, unknown>[]);
      add("Interbank Repo", repo as unknown as Record<string, unknown>[]);

      if (failed.length) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(
          failed.map((d) => ({ dataset: d, status: "NOT INCLUDED - the data service did not answer; this is missing, not empty" }))), "NOT INCLUDED");
        window.alert(`Export is INCOMPLETE: ${failed.join(", ")} could not be loaded and are missing from the workbook (see the NOT INCLUDED sheet).`);
      }
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
