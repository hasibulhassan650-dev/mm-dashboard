"use client";
import * as React from "react";

/**
 * A buyback BB has announced but not yet resulted, as the desk knows it.
 *
 * Provisional and never BB data: it is kept apart, shown labelled, and never added
 * to a cash total. Cash is optional on purpose - BB did not publish the price of
 * its first buyback, and deriving cash from face value would be wrong by the
 * premium the bond traded at. Posts same-origin to /api/desk/buyback, which holds
 * the shared secret server-side.
 */
export default function DeskBuybackForm({ configured }: { configured: boolean }) {
  const today = new Date().toISOString().slice(0, 10);
  const [isin, setIsin] = React.useState("");
  const [date, setDate] = React.useState(today);
  const [face, setFace] = React.useState("");
  const [cash, setCash] = React.useState("");
  const [price, setPrice] = React.useState("");
  const [by, setBy] = React.useState("");
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch("/api/desk/buyback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          isin: isin.trim(), settlement_date: date, accepted_face_crore: Number(face),
          cash_paid_crore: cash === "" ? null : Number(cash),
          price_per_100: price === "" ? null : Number(price),
          entered_by: by || null, note: note || null,
        }),
      });
      const j = await res.json().catch(() => ({}));
      if (res.ok) {
        setMsg({ ok: true, text: `${j.action ?? "recorded"}: ${isin.toUpperCase()} settling ${date}, ${face} cr face. Reload to see it listed.` });
        setFace(""); setCash(""); setPrice(""); setNote("");
      } else {
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
          Desk entry is not configured yet (see the setup check above). Nothing typed here would be stored.
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 10 }}>
        <div>
          <label style={label} htmlFor="b-isin">ISIN</label>
          <input id="b-isin" style={field} maxLength={12} placeholder="BD0926191026"
                 value={isin} onChange={(e) => setIsin(e.target.value)} required />
        </div>
        <div>
          <label style={label} htmlFor="b-date">Settlement date</label>
          <input id="b-date" style={field} type="date" value={date}
                 onChange={(e) => setDate(e.target.value)} required />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        <div>
          <label style={label} htmlFor="b-face">Accepted face, crore</label>
          <input id="b-face" style={field} type="number" step="0.01" min="0.01" placeholder="1716.14"
                 value={face} onChange={(e) => setFace(e.target.value)} required />
        </div>
        <div>
          <label style={label} htmlFor="b-cash">Cash paid, crore (optional)</label>
          <input id="b-cash" style={field} type="number" step="0.01" min="0" placeholder="blank if unknown"
                 value={cash} onChange={(e) => setCash(e.target.value)} />
        </div>
        <div>
          <label style={label} htmlFor="b-price">Price per 100 (optional)</label>
          <input id="b-price" style={field} type="number" step="0.0001" min="0" placeholder="blank if unknown"
                 value={price} onChange={(e) => setPrice(e.target.value)} />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 10 }}>
        <div>
          <label style={label} htmlFor="b-by">Entered by</label>
          <input id="b-by" style={field} maxLength={60} placeholder="initials"
                 value={by} onChange={(e) => setBy(e.target.value)} />
        </div>
        <div>
          <label style={label} htmlFor="b-note">Note</label>
          <input id="b-note" style={field} maxLength={500} placeholder="where the figure came from"
                 value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
      </div>
      <button type="submit" className="seg-b" disabled={busy || !configured}
              style={{ justifySelf: "start", opacity: busy || !configured ? 0.55 : 1 }}>
        {busy ? "Saving…" : "Record buyback"}
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
        Shown on that day&apos;s page as provisional and <b>never added to a cash total</b>: the ladder
        takes buyback cash only from BB&apos;s own result. When BB publishes it, its figure replaces this
        one and the gap between the two is kept.
      </p>
    </form>
  );
}
