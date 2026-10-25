"use client";
import * as React from "react";

/**
 * Which half of desk-entry setup is missing?
 *
 * Desk entry needs the SAME secret on two Vercel projects - the site and the
 * API - and the form used to simply refuse, leaving the desk to guess. This
 * shows each side separately, runs the real round trip (a ping that checks the
 * shared secret and writes nothing), and says the exact next step. It never
 * sees or displays the secret itself.
 */
type Stage =
  | "ok" | "site_secret_missing" | "api_secret_missing" | "secret_mismatch"
  | "api_unreachable" | "api_error" | "not_signed_in";

const NEXT: Record<Stage, string> = {
  ok: "Everything is wired up. Entries you record are stored, labelled provisional, and replaced by BB's figure when it publishes.",
  site_secret_missing:
    "Add DESK_WRITE_SECRET to the SITE project in Vercel (Settings → Environment Variables), then redeploy it.",
  api_secret_missing:
    "Add the same DESK_WRITE_SECRET to the API project in Vercel, then redeploy it. The site already has one.",
  secret_mismatch:
    "Both projects have a secret but they differ. Set them to exactly the same value on both, then redeploy both.",
  api_unreachable: "The API could not be reached just now. Try again in a minute.",
  api_error: "The API answered with an unexpected error. Check its deployment.",
  not_signed_in: "Your session has expired. Sign in again.",
};

function Chip({ ok, label }: { ok: boolean; label: string }) {
  const c = ok ? "var(--pos)" : "var(--warn)";
  return (
    <span style={{
      display: "inline-flex", gap: 6, alignItems: "center", fontSize: 11.5,
      padding: "3px 9px", borderRadius: 999, color: c,
      border: `1px solid color-mix(in oklab, ${c} 45%, transparent)`,
      background: `color-mix(in oklab, ${c} 10%, transparent)`,
    }}>
      <b>{ok ? "✓" : "✗"}</b> {label}
    </span>
  );
}

export default function DeskSetup({ siteSet, apiSet }: { siteSet: boolean; apiSet: boolean }) {
  const [stage, setStage] = React.useState<Stage | null>(null);
  const [detail, setDetail] = React.useState<string>("");
  const [busy, setBusy] = React.useState(false);

  const test = React.useCallback(async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/desk/ping", { cache: "no-store" });
      const j = await r.json().catch(() => ({}));
      setStage((j.stage as Stage) ?? "api_error");
      setDetail(j.detail ?? "");
    } catch (e) {
      setStage("api_unreachable");
      setDetail((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  React.useEffect(() => { void test(); }, [test]);

  const ok = stage === "ok";
  const border = ok ? "var(--pos)" : "var(--warn)";
  return (
    <div style={{
      border: `1px solid ${border}`, borderRadius: "var(--radius-sm)",
      padding: "10px 12px", marginBottom: 12, fontSize: 12.5,
      background: `color-mix(in oklab, ${border} 8%, transparent)`,
    }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Chip ok={siteSet} label="Site has the secret" />
        <Chip ok={apiSet} label="API has the secret" />
        <Chip ok={ok} label="Round trip" />
        <button type="button" className="seg-b" onClick={test} disabled={busy}
                style={{ marginLeft: "auto", opacity: busy ? 0.6 : 1 }}>
          {busy ? "Testing…" : "Test connection"}
        </button>
      </div>
      <p style={{ margin: "8px 0 0", color: "var(--fg-dim)", lineHeight: 1.6 }}>
        {stage ? NEXT[stage] : "Checking…"}
        {detail && !ok && <span style={{ color: "var(--fg-mute)" }}> ({detail})</span>}
      </p>
      {!ok && (
        <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--fg-mute)", lineHeight: 1.6 }}>
          Generate a value with <code>python -c &quot;import secrets; print(secrets.token_urlsafe(32))&quot;</code>.
          The secret is never shown on this page or sent to your browser.
        </p>
      )}
    </div>
  );
}
