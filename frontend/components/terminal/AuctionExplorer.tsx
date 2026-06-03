"use client";
import * as React from "react";
import { api, type YieldRow } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { bidToCover } from "@/lib/analytics";
import { Panel } from "@/components/terminal/ui";
import YieldTrendChart from "@/components/YieldTrendChart";
import DownloadButton from "@/components/DownloadButton";

function isoAddYears(base: string, years: number): string {
  const d = new Date(base);
  d.setFullYear(d.getFullYear() + years);
  return d.toISOString().slice(0, 10);
}

export default function AuctionExplorer({ bounds }: { bounds: { min: string | null; max: string | null; count: number } }) {
  const max = bounds.max ?? new Date().toISOString().slice(0, 10);
  const min = bounds.min ?? "2007-01-01";
  // Default to the FULL available history so 2007→present shows on load.
  const [from, setFrom] = React.useState(min);
  const [to, setTo] = React.useState(max);
  const [tenor, setTenor] = React.useState("All");
  const [rows, setRows] = React.useState<YieldRow[]>([]);
  const [loading, setLoading] = React.useState(false);

  const load = React.useCallback(async (f: string, t: string, tn: string) => {
    setLoading(true);
    try {
      const data = await api.yieldsRange(f, t, tn === "All" ? undefined : tn);
      setRows(data);
    } catch { setRows([]); }
    setLoading(false);
  }, []);

  React.useEffect(() => { load(from, to, tenor); }, []); // initial

  const tenors = React.useMemo(
    () => ["All", ...Array.from(new Set(rows.map((r) => r.tenor_label))).sort()],
    [rows],
  );

  function applyPreset(years: number | "all") {
    const f = years === "all" ? min : (isoAddYears(max, -years) < min ? min : isoAddYears(max, -years));
    setFrom(f); setTo(max); load(f, max, tenor);
  }

  const btn = (label: string, onClick: () => void) => (
    <button key={label} className="seg-b" style={{ border: "1px solid var(--border)" }} onClick={onClick}>{label}</button>
  );

  return (
    <Panel
      title="Auction Explorer"
      sub={`full history ${min} → ${max} · ${bounds.count.toLocaleString()} prints · pick any window`}
      span={12}
      right={<DownloadButton data={rows} filename={`auctions_${from}_${to}`} />}
    >
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 14 }}>
        <label style={{ fontSize: 12, color: "var(--fg-dim)", display: "flex", gap: 6, alignItems: "center" }}>
          From
          <input type="date" value={from} min={min} max={to} onChange={(e) => { setFrom(e.target.value); load(e.target.value, to, tenor); }}
            style={inputStyle} />
        </label>
        <label style={{ fontSize: 12, color: "var(--fg-dim)", display: "flex", gap: 6, alignItems: "center" }}>
          To
          <input type="date" value={to} min={from} max={max} onChange={(e) => { setTo(e.target.value); load(from, e.target.value, tenor); }}
            style={inputStyle} />
        </label>
        <label style={{ fontSize: 12, color: "var(--fg-dim)", display: "flex", gap: 6, alignItems: "center" }}>
          Tenor
          <select value={tenor} onChange={(e) => { setTenor(e.target.value); load(from, to, e.target.value); }} style={inputStyle}>
            {tenors.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <div className="seg" style={{ marginLeft: "auto" }}>
          {btn("1Y", () => applyPreset(1))}
          {btn("5Y", () => applyPreset(5))}
          {btn("10Y", () => applyPreset(10))}
          {btn("All", () => applyPreset("all"))}
        </div>
      </div>

      <div style={{ opacity: loading ? 0.5 : 1, transition: "opacity .15s" }}>
        <YieldTrendChart data={rows} />
      </div>

      <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto", marginTop: 14 }}>
        <table className="dt">
          <thead><tr><th>Auction Date</th><th>Type</th><th>Tenor</th><th className="r">Yield</th><th className="r">Offered (cr)</th><th className="r">Accepted (cr)</th><th className="r">B/C</th></tr></thead>
          <tbody>
            {rows.length === 0 && !loading && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--fg-mute)", height: 80 }}>No auctions in this window.</td></tr>
            )}
            {rows.map((r, i) => {
              const bc = bidToCover(r);
              return (
                <tr key={i}>
                  <td>{fmtDate(r.auction_date)}</td>
                  <td><span className="pill-inst">{r.security_type}</span></td>
                  <td className="mono">{r.tenor_label}</td>
                  <td className="r mono">{r.cutoff_yield_pct.toFixed(4)}%</td>
                  <td className="r mono">{r.offered_bdt_crore?.toFixed(0) ?? "—"}</td>
                  <td className="r mono">{r.accepted_bdt_crore?.toFixed(0) ?? "—"}</td>
                  <td className="r mono"><span className={bc == null ? "" : bc < 1.1 ? "neg" : "pos"}>{bc == null ? "—" : `${bc.toFixed(2)}×`}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 6,
  color: "var(--fg)", fontSize: 12, fontFamily: "var(--mono)", padding: "4px 8px", outline: "none",
};
