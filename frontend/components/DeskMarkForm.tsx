"use client";
import * as React from "react";

/**
 * The desk's cut-off, typed in on auction day.
 *
 * Posts same-origin to /api/desk/mark, which holds the shared secret
 * server-side and forwards to the API. Nothing secret reaches this component.
 *
 * It refuses to pretend: when the write path is unconfigured the form says so
 * and stays disabled, rather than accepting a figure and dropping it.
 */
export default function DeskMarkForm({ tenors, configured }:
  { tenors: string[]; configured: boolean }) {
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = React.useState(today);
  const [tenor, setTenor] = React.useState(tenors[0] ?? "91D");
  const [cutoff, setCutoff] = React.useState("");
  const [amount, setAmount] = React.useState("");
  const [by, setBy] = React.useState("");
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch("/api/desk/mark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          auction_date: date,
          tenor_label: tenor,
          cutoff_yield_pct: Number(cutoff),
          accepted_crore: amount === "" ? null : Number(amount),
          entered_by: by || null,
          note: note || null,
        }),
      });
      const j = await res.json().catch(() => ({}));
      if (res.ok) {
        setMsg({ ok: true, text: `${j.action ?? "recorded"}: ${tenor} at ${cutoff}% on ${date}. Reload to see it listed.` });
        setCutoff(""); setAmount(""); setNote("");
      } else {
        // The API's own message names the field that is wrong; show it rather
        // than a generic failure.
        const detail = typeof j.detail === "string" ? j.detail
          : Array.isArray(j.detail) ? j.detail.map((d: { msg?: string }) => d.msg).join("; ")
          : j.error ?? `HTTP ${res.status}`;
        setMsg({ ok: false, text: detail });
      }
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const field: React.CSSProperties = {
    width: "100%", background: "var(--panel-2, var(--panel))", color: "var(--fg)",
    border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
    padding: "6px 8px", fontSize: 12.5, fontFamily: "inherit",
  };
  const label: React.CSSProperties = {
    fontSize: 10.5, letterSpacing: 0.3, color: "var(--fg-mute)",
    textTransform: "uppercase", display: "block", marginBottom: 3,
  };

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
      {!configured && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "7px 10px", fontSize: 11.5, color: "var(--warn)",
          background: "color-mix(in oklab, var(--warn) 9%, transparent)",
        }}>
          Desk entry is not configured yet. Set <code>DESK_WRITE_SECRET</code> on both the
          site and the API in Vercel. Until then writes are refused rather than left open,
          so nothing typed here would be stored.
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div>
          <label style={label} htmlFor="d-date">Auction date</label>
          <input id="d-date" style={field} type="date" value={date} max={today}
                 onChange={(e) => setDate(e.target.value)} required />
        </div>
        <div>
          <label style={label} htmlFor="d-tenor">Tenor</label>
          <select id="d-tenor" style={field} value={tenor}
                  onChange={(e) => setTenor(e.target.value)}>
            {(tenors.length ? tenors : ["91D", "182D", "364D"]).map((t) =>
              <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div>
          <label style={label} htmlFor="d-cut">Cut-off yield %</label>
          <input id="d-cut" style={field} type="number" step="0.0001" min="0.0001" max="30"
                 placeholder="8.5899" value={cutoff}
                 onChange={(e) => setCutoff(e.target.value)} required />
        </div>
        <div>
          <label style={label} htmlFor="d-amt">Accepted, crore (optional)</label>
          <input id="d-amt" style={field} type="number" step="0.01" min="0"
                 placeholder="leave blank if not known"
                 value={amount} onChange={(e) => setAmount(e.target.value)} />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 10 }}>
        <div>
          <label style={label} htmlFor="d-by">Entered by</label>
          <input id="d-by" style={field} maxLength={60} placeholder="initials"
                 value={by} onChange={(e) => setBy(e.target.value)} />
        </div>
        <div>
          <label style={label} htmlFor="d-note">Note</label>
          <input id="d-note" style={field} maxLength={500}
                 placeholder="e.g. heavy devolvement expected"
                 value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
      </div>

      <button type="submit" className="seg-b" disabled={busy || !configured}
              style={{ justifySelf: "start", opacity: busy || !configured ? 0.55 : 1 }}>
        {busy ? "Saving…" : "Record mark"}
      </button>

      {msg && (
        <div role={msg.ok ? undefined : "alert"} style={{
          fontSize: 11.5, padding: "7px 10px", borderRadius: "var(--radius-sm)",
          color: msg.ok ? "var(--pos)" : "var(--neg)",
          border: `1px solid ${msg.ok ? "var(--pos)" : "var(--neg)"}`,
          background: `color-mix(in oklab, ${msg.ok ? "var(--pos)" : "var(--neg)"} 9%, transparent)`,
        }}>{msg.text}</div>
      )}

      <p style={{ margin: 0, fontSize: 11, color: "var(--fg-mute)", lineHeight: 1.7 }}>
        Entering the same auction and tenor again corrects the earlier mark. The figure is
        kept apart from BB's data and is replaced by BB's published cut-off when it arrives —
        it is never written into the published-results table.
      </p>
    </form>
  );
}
