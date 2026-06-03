"use client";
import * as React from "react";
import { api, type MetaStatus } from "@/lib/api";
import { timeAgo, fmtDate } from "@/lib/format";

// Order datasets by importance for the panel.
const ORDER = ["yields", "omo", "callmoney", "fx", "refrate", "flows", "secondary", "securities"];

function hoursSince(iso: string | null): number {
  if (!iso) return Infinity;
  return (Date.now() - new Date(iso).getTime()) / 3.6e6;
}

export default function UpdateStatus() {
  const [open, setOpen] = React.useState(false);
  const [s, setS] = React.useState<MetaStatus | null>(null);

  const load = React.useCallback(() => { api.status().then(setS).catch(() => {}); }, []);
  React.useEffect(() => {
    load();
    const id = setInterval(load, 120_000); // re-check every 2 min
    return () => clearInterval(id);
  }, [load]);
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // staleness of the most recent refresh run (3×/day ⇒ healthy if < ~10h)
  const runAge = hoursSince(s?.last_run ?? null);
  const healthy = runAge < 10;
  const keys = s ? ORDER.filter((k) => s.datasets[k]).concat(Object.keys(s.datasets).filter((k) => !ORDER.includes(k))) : [];

  return (
    <div style={{ position: "relative" }}>
      <button className="status" onClick={() => { setOpen((o) => !o); load(); }}
        title="Data update status" aria-label="Data update status"
        style={{ background: "none", border: "none", cursor: "pointer", padding: "4px 6px", borderRadius: 7 }}>
        <span className="status-dot" style={{ background: healthy ? "var(--pos)" : "var(--warn)", boxShadow: `0 0 0 3px color-mix(in oklab, ${healthy ? "var(--pos)" : "var(--warn)"} 22%, transparent)` }} />
        <span className="status-txt">Auto</span>
        <span className="status-time" suppressHydrationWarning>{s?.last_run ? timeAgo(s.last_run) : "—"}</span>
      </button>

      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 79 }} />
          <div style={{ position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 80, width: 320, background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 12, boxShadow: "0 16px 50px rgba(0,0,0,.45)", padding: 14, animation: "drillIn .14s ease" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
              <h3 className="panel-title">Data Updates</h3>
              <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>{s?.cadence || "Auto-refreshed"}</span>
            </div>
            <p style={{ fontSize: 11.5, color: "var(--fg-dim)", margin: "0 0 12px" }}>
              Not live — data is fetched from Bangladesh Bank on a schedule.{" "}
              <b style={{ color: healthy ? "var(--pos)" : "var(--warn)" }}>Last refresh {s?.last_run ? timeAgo(s.last_run) : "unknown"}</b>.
            </p>

            {s && s.last_run_errors.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--neg)", marginBottom: 10 }}>
                ⚠ Last run had {s.last_run_errors.length} error(s): {s.last_run_errors.join("; ").slice(0, 120)}
              </div>
            )}

            <div className="metric-list">
              {keys.map((k) => {
                const d = s!.datasets[k];
                const old = hoursSince(d.ingested) > 30; // flag if no write in >~30h
                return (
                  <div className="metric" key={k} style={{ padding: "8px 0", alignItems: "flex-start" }}>
                    <span style={{ display: "flex", flexDirection: "column" }}>
                      <span style={{ color: "var(--fg)" }}>{d.label}</span>
                      <span style={{ fontSize: 10.5, color: "var(--fg-mute)" }}>{d.rows.toLocaleString()} rows</span>
                    </span>
                    <span style={{ textAlign: "right" }}>
                      {d.latest_data && <b className="mono" style={{ fontSize: 12 }}>thru {fmtDate(d.latest_data)}</b>}
                      <span style={{ display: "block", fontSize: 10.5, color: old ? "var(--warn)" : "var(--fg-mute)" }}>
                        updated {d.ingested ? timeAgo(d.ingested) : "—"}
                      </span>
                    </span>
                  </div>
                );
              })}
              {!s && <div style={{ color: "var(--fg-mute)", fontSize: 12, padding: "8px 0" }}>Loading…</div>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
