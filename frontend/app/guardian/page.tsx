import { api } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import RelatedLinks from "@/components/RelatedLinks";
import DownloadButton from "@/components/DownloadButton";

export const revalidate = 300;

function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const h = (Date.now() - new Date(iso).getTime()) / 3.6e6;
  if (h < 1) return "just now";
  if (h < 48) return `${Math.round(h)}h`;
  return `${Math.round(h / 24)}d`;
}

function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default async function GuardianPage() {
  const g = await api.guardian();

  const defects = g.open.filter((o) => o.severity === "defect");
  const waiting = g.open.filter((o) => o.severity === "waiting");

  return (
    <>
      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Open Defects</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: defects.length ? "var(--neg)" : "var(--pos)" }}>{defects.length}</span></div>
          <div className="kpi-sub">problems with our data</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Waiting on BB</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: waiting.length ? "var(--info)" : "var(--pos)" }}>{waiting.length}</span></div>
          <div className="kpi-sub">not published yet · not ours to fix</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Resolved</span></div>
          <div className="kpi-val"><span className="kpi-num">{g.resolved.length}</span></div>
          <div className="kpi-sub">most recent {g.resolved.length ? ago(g.resolved[0].resolved_at) : "—"} ago</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Auto-Repairs</span></div>
          <div className="kpi-val"><span className="kpi-num">{g.repairs.length}</span></div>
          <div className="kpi-sub">rows fixed without a human</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Open — our data" sub="every one of these fails the build until it is fixed" span={12} pad={false}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr><th>Check</th><th>Problem</th><th className="r">First seen</th><th className="r">Age</th><th className="r">Seen</th></tr></thead>
              <tbody>
                {defects.map((o, i) => (
                  <tr key={i}>
                    <td><span className="pill-inst">{o.table}</span></td>
                    <td style={{ color: "var(--neg)" }}>{o.message}</td>
                    <td className="r mono">{when(o.first_seen)}</td>
                    <td className="r mono">{ago(o.first_seen)}</td>
                    <td className="r mono">{o.occurrences}×</td>
                  </tr>
                ))}
                {defects.length === 0 && (
                  <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--pos)", height: 64 }}>
                    No open defects — every integrity check passes.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Open — waiting on Bangladesh Bank"
          sub="not defects and never a build failure, but the age is the point" span={12} pad={false}>
          <div style={{ padding: "10px 14px 0", fontSize: 11, color: "var(--fg-mute)", lineHeight: 1.5 }}>
            BB has not published these yet, so the data is <b>missing rather than zero</b>. A day or two is
            ordinary publication lag; a gap that keeps growing means they have stopped, and only the age
            tells you which one you are looking at.
          </div>
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table className="dt">
              <thead><tr><th>Check</th><th>Waiting for</th><th className="r">Since</th><th className="r">Age</th><th className="r">Seen</th></tr></thead>
              <tbody>
                {waiting.map((o, i) => (
                  <tr key={i}>
                    <td><span className="pill-inst">{o.table}</span></td>
                    <td style={{ color: "var(--fg-dim)" }}>{o.message}</td>
                    <td className="r mono">{when(o.first_seen)}</td>
                    <td className="r mono" style={{ color: (o.age_hours ?? 0) > 24 * 7 ? "var(--warn)" : "var(--info)" }}>{ago(o.first_seen)}</td>
                    <td className="r mono">{o.occurrences}×</td>
                  </tr>
                ))}
                {waiting.length === 0 && (
                  <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--fg-mute)", height: 64 }}>
                    Nothing outstanding from Bangladesh Bank.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Resolved" sub="what it was and how long it lived" span={7} pad={false}
          right={<DownloadButton data={g.resolved} filename="guardian_resolved" />}>
          <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Check</th><th>Problem</th><th className="r">Resolved</th><th>Lifetime</th></tr></thead>
              <tbody>
                {g.resolved.map((r, i) => (
                  <tr key={i}>
                    <td><span className="pill-inst">{r.table}</span></td>
                    <td style={{ color: "var(--fg-dim)" }}>{r.message?.slice(0, 110)}</td>
                    <td className="r mono">{ago(r.resolved_at)} ago</td>
                    <td className="mono" style={{ fontSize: 11, color: "var(--pos)" }}>{r.resolution}</td>
                  </tr>
                ))}
                {g.resolved.length === 0 && (
                  <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--fg-mute)", height: 64 }}>
                    Nothing has been resolved yet — the ledger starts from its first run.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Automatic repairs" sub="corrections applied without a human" span={5} pad={false}
          right={<DownloadButton data={g.repairs} filename="guardian_repairs" />}>
          <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Class</th><th>What changed</th><th className="r">When</th></tr></thead>
              <tbody>
                {g.repairs.map((r, i) => (
                  <tr key={i}>
                    <td className="mono" style={{ fontSize: 11 }}>{r.change_class}</td>
                    <td style={{ color: "var(--fg-dim)", fontSize: 11.5 }}>{r.detail}</td>
                    <td className="r mono">{ago(r.changed_utc)} ago</td>
                  </tr>
                ))}
                {g.repairs.length === 0 && (
                  <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--fg-mute)", height: 64 }}>
                    No automatic repairs recorded yet.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/", label: "Overview", why: "what the data says today" },
        { href: "/cashflows", label: "Cash Flows", why: "what a missing auction calendar affects" },
      ]} />
    </>
  );
}
