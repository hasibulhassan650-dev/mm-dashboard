"use client";
import * as React from "react";
import { api, type MetaStatus } from "@/lib/api";

/**
 * Two honest warnings about the data, both driven by /api/meta/status.
 *
 * StaleDataBanner — shown on EVERY page when the refresh has stopped. In
 * Sep-2026 the pipeline was dead for five days while the site stayed up: the
 * only hint was a small "5d ago" in the status pill, and every figure on every
 * page still looked current. A dead pipeline is not a footnote.
 *
 * DataWarning — marks the specific numbers a failing check covers, so a flagged
 * dataset is visible on the page you actually trade from, not just on Overview.
 */

function useStatus(): MetaStatus | null {
  const [s, setS] = React.useState<MetaStatus | null>(null);
  React.useEffect(() => {
    let alive = true;
    const load = () => api.status().then((r) => alive && setS(r)).catch(() => {});
    load();
    const id = setInterval(load, 120_000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return s;
}

function daysAgo(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const h = (Date.now() - new Date(iso).getTime()) / 3.6e6;
  if (h < 48) return `${Math.round(h)} hours`;
  return `${Math.round(h / 24)} days`;
}

export function StaleDataBanner() {
  const s = useStatus();
  // `pipeline_alive === false` only — never `!pipeline_alive`, or an older API
  // build that omits the field would show a false alarm on every page.
  if (!s || s.pipeline_alive !== false) return null;
  const when = s.last_run ? new Date(s.last_run).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "unknown";
  return (
    <div role="alert" style={{
      background: "color-mix(in oklab, var(--neg) 16%, var(--panel))",
      border: "1px solid var(--neg)", borderRadius: "var(--radius)",
      padding: "11px 14px", marginBottom: "var(--gap)",
      display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap",
    }}>
      <span style={{ color: "var(--neg)", fontWeight: 700, fontSize: 15, lineHeight: 1.2 }}>⚠</span>
      <div style={{ minWidth: 220, flex: 1 }}>
        <div style={{ color: "var(--neg)", fontWeight: 600, fontSize: 13 }}>
          The data refresh has not run for {daysAgo(s.last_run)} — do not trade these numbers as current.
        </div>
        <div style={{ color: "var(--fg-dim)", fontSize: 11.5, marginTop: 3 }}>
          Every figure on this page is as of <b className="mono">{when}</b>. The site being online does not
          mean Bangladesh Bank data is current. {s.health_stale && "The integrity verdict below is from that same run, so it describes stale data too."}
        </div>
      </div>
    </div>
  );
}

/**
 * Bangladesh Bank's own website is down.
 *
 * From 22-Oct-2026 BB served a maintenance page in place of its pages and every
 * BB-fed series stopped. For days that read, here and in the alerts, as a fault
 * in OUR fetch - sending the desk to debug the wrong system. It is neither BB
 * being late nor our code failing: it is a frozen feed with a clear owner and a
 * clear action (wait, and know the numbers are not current). This banner says
 * exactly that, and it is the first thing shown because it changes how every
 * other number on the page should be read.
 */
export function BBOutageBanner() {
  const s = useStatus();
  const o = s?.bb_outage;
  if (!o) return null;
  const half = o.state === "partial";
  return (
    <div role="alert" style={{
      background: "color-mix(in oklab, var(--warn) 14%, var(--panel))",
      border: "1px solid var(--warn)", borderRadius: "var(--radius)",
      padding: "10px 14px", marginBottom: "var(--gap)",
    }}>
      <div style={{ color: "var(--warn)", fontWeight: 700, fontSize: 13 }}>
        ⚠ Bangladesh Bank&apos;s website is {half ? "partly " : ""}under maintenance —
        the figures below are frozen at their last published values
      </div>
      <div style={{ color: "var(--fg-dim)", fontSize: 11.5, marginTop: 3, lineHeight: 1.6 }}>
        Nothing is wrong with this dashboard and nothing will refresh until BB is back;
        it re-checks every refresh and will catch up on its own.
        {o.probed_utc && <> Last checked <b className="mono">{o.probed_utc.slice(0, 16).replace("T", " ")} UTC</b>.</>}
        {" "}Treat OMO, auction results, call money, reference rates and FX as not current.
      </div>
    </div>
  );
}

/**
 * A feed that has been dead long enough that "Bangladesh Bank is late" no
 * longer explains it.
 *
 * OMO data stopped on 29-Sep-2026 and the desk found out eleven days later, by
 * hand. The check fired the whole time — it even called the gap "implausible" —
 * but it was filed as "waiting on BB", which is deliberately quiet, and the one
 * finding that mattered read exactly like the ones that did not. Escalations
 * are therefore the loudest thing on the site and the only one that says, in
 * words, which numbers not to trust.
 */
export function EscalationBanner() {
  const s = useStatus();
  const items = s?.escalations ?? [];
  if (!items.length) return null;
  const clean = (m: string) =>
    m.replace(/^freshness:\s*/, "").replace(/ESCALATED\s*—\s*/, "");
  return (
    <div role="alert" style={{
      background: "color-mix(in oklab, var(--neg) 18%, var(--panel))",
      border: "2px solid var(--neg)", borderRadius: "var(--radius)",
      padding: "11px 14px", marginBottom: "var(--gap)",
    }}>
      <div style={{ color: "var(--neg)", fontWeight: 700, fontSize: 13,
                    display: "flex", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 15, lineHeight: 1 }}>⚠</span>
        <span>
          {items.length === 1 ? "A dataset is past its tolerance"
                              : `${items.length} datasets are past their tolerance`}
          {" "}— these are not up to date, whatever the page shows
        </span>
      </div>
      <ul style={{ margin: "5px 0 0", paddingLeft: 26, color: "var(--fg-dim)", fontSize: 11.5 }}>
        {items.slice(0, 4).map((m, i) => <li key={i}>{clean(m)}</li>)}
      </ul>
    </div>
  );
}

/**
 * Bangladesh Bank changed the shape of a page.
 *
 * Its own colour because it is its own meaning: not our data being wrong, not
 * BB being late. Columns are mapped by header text rather than position, so a
 * reworded header or an inserted column does not corrupt a figure — but the
 * layout moved, and nobody should discover that months later in a diff.
 */
export function SourceChangeNotice() {
  const s = useStatus();
  const items = s?.notices ?? [];
  if (!items.length) return null;
  const src = (m: string) => m.replace(/^source:\s*/, "").split(":")[0];
  return (
    <div style={{
      background: "color-mix(in oklab, var(--accent) 8%, var(--panel))",
      border: "1px solid color-mix(in oklab, var(--accent) 34%, transparent)",
      borderRadius: "var(--radius-sm)", padding: "7px 12px", marginBottom: "var(--gap)",
      fontSize: 11.5, color: "var(--fg-dim)", display: "flex", gap: 8, flexWrap: "wrap",
      alignItems: "baseline",
    }}>
      <span style={{ color: "var(--accent)", fontWeight: 600 }}>Bangladesh Bank changed a page:</span>
      <span>
        {items.map((n, i) => (
          <span key={i} title={n.message}>
            {i > 0 && " · "}
            {src(n.message)}
            {n.age_days != null && n.age_days > 0 &&
              <span style={{ color: "var(--fg-mute)" }}> ({n.age_days}d)</span>}
          </span>
        ))}
      </span>
      <span style={{ color: "var(--fg-mute)" }}>
        — columns are read by header, so the figures still line up.
      </span>
    </div>
  );
}

/** Dataset key as used by validate.py's by_table (yields, omo, callmoney, …). */
export function DataWarning({ dataset }: { dataset: string | string[] }) {
  const s = useStatus();
  const keys = Array.isArray(dataset) ? dataset : [dataset];
  const health = s?.data_health;
  if (!health || health.ok) return null;
  const issues = (health.issues ?? []).filter((i) => keys.some((k) => i.startsWith(k + ":")));
  if (!issues.length) return null;
  return (
    <div style={{
      background: "color-mix(in oklab, var(--warn) 12%, var(--panel))",
      border: "1px solid color-mix(in oklab, var(--warn) 55%, transparent)",
      borderRadius: "var(--radius-sm)", padding: "8px 11px", marginBottom: "var(--gap)",
      fontSize: 11.5, color: "var(--fg-dim)",
    }}>
      <b style={{ color: "var(--warn)" }}>⚠ {issues.length} integrity issue(s) affect this page</b>
      {s?.health_as_of && (
        <span style={{ color: "var(--fg-mute)" }}>
          {" "}· checked {new Date(s.health_as_of).toLocaleDateString(undefined, { dateStyle: "medium" })}
        </span>
      )}
      <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
        {issues.slice(0, 4).map((i, n) => <li key={n} style={{ fontSize: 10.5 }}>{i}</li>)}
      </ul>
    </div>
  );
}

/**
 * Things Bangladesh Bank has not published yet.
 *
 * Deliberately NOT red and deliberately not silent. These are not faults in
 * our data and nobody can act on them, so failing the build on them (which is
 * what used to happen, ~28 red runs a day) only taught everyone to ignore red.
 * But they have consequences worth seeing — a missing auction calendar means
 * the cash-flow ladder carries no outflow for those auctions — so they are
 * shown with the age that separates "BB is late" from "BB has stopped".
 */
export function WaitingNotice() {
  const s = useStatus();
  const items = s?.waiting ?? [];
  if (!items.length) return null;
  const label = (m: string) => {
    const m2 = m.replace(/^freshness:\s*/, "").split(":")[0];
    return ({ auctions_forward: "auction calendar", repo: "interbank repo",
              fxmarket: "interbank FX", fxrates: "exchange rates",
              callmoney: "call money", refrate: "reference rates",
              omo: "OMO operations", yields: "auction results",
              secondary: "secondary market" } as Record<string, string>)[m2] ?? m2;
  };
  return (
    <div style={{
      background: "color-mix(in oklab, var(--info) 8%, var(--panel))",
      border: "1px solid color-mix(in oklab, var(--info) 32%, transparent)",
      borderRadius: "var(--radius-sm)", padding: "7px 12px", marginBottom: "var(--gap)",
      fontSize: 11.5, color: "var(--fg-dim)", display: "flex", gap: 8, flexWrap: "wrap",
      alignItems: "baseline",
    }}>
      <span style={{ color: "var(--info)", fontWeight: 600 }}>Waiting on Bangladesh Bank:</span>
      <span>
        {items.map((w, i) => (
          <span key={i} title={w.message}>
            {i > 0 && " · "}
            {label(w.message)}
            {w.age_days != null && w.age_days > 0 && <span style={{ color: "var(--fg-mute)" }}> ({w.age_days}d)</span>}
          </span>
        ))}
      </span>
      <span style={{ color: "var(--fg-mute)" }}>— not published yet, so this data is missing rather than zero.</span>
    </div>
  );
}
