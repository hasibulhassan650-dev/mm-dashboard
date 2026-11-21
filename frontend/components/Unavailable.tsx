import * as React from "react";

/**
 * "The data service did not answer" - the honest alternative to rendering a failed fetch as zero, empty or all-clear.
 * `what` names the source(s) that failed. Server-component safe (no hooks).
 */
export default function Unavailable({ what, children }: { what: string | string[]; children?: React.ReactNode }) {
  const list = Array.isArray(what) ? what : [what];
  if (!list.length) return null;
  return (
    <div role="alert" style={{
      background: "color-mix(in oklab, var(--neg) 14%, var(--panel))",
      border: "1px solid var(--neg)", borderRadius: "var(--radius)",
      padding: "10px 14px", marginBottom: "var(--gap)",
    }}>
      <div style={{ color: "var(--neg)", fontWeight: 700, fontSize: 13 }}>
        ⚠ Unavailable: {list.join(", ")}
      </div>
      <div style={{ color: "var(--fg-dim)", fontSize: 11.5, marginTop: 3, lineHeight: 1.6 }}>
        {children ?? <>The data service did not answer for this. Anything blank (—) or missing below is <b>unknown, not zero</b>
        and not an all-clear; reload in a moment.</>}
      </div>
    </div>
  );
}
