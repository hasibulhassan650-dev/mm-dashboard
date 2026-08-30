"use client";
import * as React from "react";

export default function LoginPage() {
  const [pw, setPw] = React.useState("");
  const [err, setErr] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(false);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      if (r.ok) {
        window.location.href = "/";        // full reload → middleware sees the cookie
        return;
      }
    } catch {
      /* fall through to error */
    }
    setErr(true);
    setBusy(false);
    setPw("");
  }

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 9999,
      display: "flex", alignItems: "center", justifyContent: "center",
      background: "var(--bg, #0b0e13)", padding: 20,
    }}>
      <form onSubmit={submit} style={{
        width: "100%", maxWidth: 340, display: "flex", flexDirection: "column", gap: 14,
        background: "var(--bg-elev, #12161d)", border: "1px solid var(--border, #232a35)",
        borderRadius: 12, padding: "28px 24px",
      }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--fg, #e6eaf0)" }}>Money Market Terminal</div>
          <div style={{ fontSize: 12, color: "var(--fg-mute, #8a94a6)", marginTop: 4 }}>Enter password to continue</div>
        </div>
        <input
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="Password"
          autoFocus
          autoComplete="current-password"
          style={{
            background: "var(--bg, #0b0e13)", border: `1px solid ${err ? "var(--warn, #e0a458)" : "var(--border, #232a35)"}`,
            borderRadius: 8, color: "var(--fg, #e6eaf0)", fontSize: 14, padding: "10px 12px", outline: "none",
            fontFamily: "var(--mono, monospace)", letterSpacing: 2,
          }}
        />
        {err && <div style={{ fontSize: 12, color: "var(--warn, #e0a458)", textAlign: "center" }}>Incorrect password</div>}
        <button type="submit" disabled={busy || !pw} style={{
          background: "var(--accent, #4c8dff)", color: "#fff", border: "none", borderRadius: 8,
          padding: "10px 12px", fontSize: 13, fontWeight: 600, cursor: busy || !pw ? "default" : "pointer",
          opacity: busy || !pw ? 0.6 : 1,
        }}>
          {busy ? "Checking…" : "Enter"}
        </button>
      </form>
    </div>
  );
}
