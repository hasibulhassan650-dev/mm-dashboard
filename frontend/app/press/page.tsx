import { api } from "@/lib/api";
import type { PressFeed, PressRelease } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import RelatedLinks from "@/components/RelatedLinks";

export const revalidate = 300;

const CHIP: Record<string, { bg: string; fg: string }> = {
  BUYBACK:        { bg: "var(--pos)",  fg: "var(--pos)" },
  AUCTION_NOTICE: { bg: "var(--warn)", fg: "var(--warn)" },
  TBILL_RESULT:   { bg: "var(--info)", fg: "var(--info)" },
  TBOND_RESULT:   { bg: "var(--info)", fg: "var(--info)" },
  BGIIB:          { bg: "var(--accent)", fg: "var(--accent)" },
  SUKUK:          { bg: "var(--accent)", fg: "var(--accent)" },
  OMO:            { bg: "var(--fg-mute)", fg: "var(--fg-dim)" },
  POLICY:         { bg: "var(--neg)",  fg: "var(--neg)" },
  FX:             { bg: "var(--fg-mute)", fg: "var(--fg-dim)" },
  OTHER:          { bg: "var(--fg-mute)", fg: "var(--fg-mute)" },
};

const LABEL: Record<string, string> = {
  BUYBACK: "Buyback", AUCTION_NOTICE: "Notice", TBILL_RESULT: "T-Bill result",
  TBOND_RESULT: "T-Bond result", BGIIB: "BGIIB", SUKUK: "Sukuk", OMO: "OMO",
  POLICY: "Policy", FX: "FX", OTHER: "Other",
};

function Chip({ c }: { c: string }) {
  const col = CHIP[c] ?? CHIP.OTHER;
  return (
    <span style={{
      fontSize: 9.5, fontWeight: 600, letterSpacing: 0.3, padding: "1px 6px",
      borderRadius: 999, whiteSpace: "nowrap", color: col.fg,
      border: `1px solid color-mix(in oklab, ${col.bg} 45%, transparent)`,
      background: `color-mix(in oklab, ${col.bg} 10%, transparent)`,
    }}>{LABEL[c] ?? c}</span>
  );
}

export default async function PressPage() {
  let feed: PressFeed | null = null;
  try { feed = await api.press(90); } catch { feed = null; }

  if (!feed || !feed.count) {
    return (
      <Panel title="Bangladesh Bank Press Releases" span={12}>
        <p style={{ margin: 0, fontSize: 13, color: "var(--warn)" }}>
          Nothing stored yet, or <code>/api/press</code> is not deployed. The listing is
          captured by the daily fetch; no figures are invented in the meantime.
        </p>
      </Panel>
    );
  }

  const byDate = new Map<string, PressRelease[]>();
  for (const r of feed.releases) {
    const k = String(r.published_date).slice(0, 10);
    (byDate.get(k) ?? byDate.set(k, []).get(k)!).push(r);
  }

  return (
    <>
      {/* What has to be acted on. Computed by comparing the listing against
          the tables we hold — never a paraphrase of BB. */}
      {feed.follow_ups.length > 0 && (
        <div style={{ display: "grid", gap: 8, marginBottom: "var(--gap)" }}>
          {feed.follow_ups.map((f, i) => (
            <div key={i} role={f.severity === "defect" ? "alert" : undefined} style={{
              border: `1px solid ${f.severity === "defect" ? "var(--neg)" : "var(--accent)"}`,
              background: `color-mix(in oklab, ${f.severity === "defect" ? "var(--neg)" : "var(--accent)"} 9%, transparent)`,
              color: f.severity === "defect" ? "var(--neg)" : "var(--fg-dim)",
              borderRadius: "var(--radius-sm)", padding: "8px 12px", fontSize: 12.5,
            }}>
              <b>{f.severity === "defect" ? "Act on this: " : "Coverage gap: "}</b>{f.text}
            </div>
          ))}
        </div>
      )}

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))" }}>
        {feed.by_category.map((b) => (
          <div className="kpi" key={b.category}>
            <div className="kpi-top"><span className="kpi-label">{b.label}</span></div>
            <div className="kpi-val"><span className="kpi-num">{b.last_7_days}</span></div>
            <div className="kpi-sub">in 7 days · {b.total} in {feed!.window_days}d</div>
          </div>
        ))}
      </div>

      <div className="grid12">
        <Panel
          title="Bangladesh Bank Press Releases"
          sub={`${feed.count} releases · last ${feed.window_days} days · as of ${feed.as_of}`}
          span={12}
          pad={false}
        >
          <div className="table-wrap" style={{ maxHeight: 620, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Published</th>
                <th>Kind</th>
                <th>About</th>
                <th>Title</th>
                <th>ISIN</th>
                <th />
              </tr></thead>
              <tbody>
                {feed.releases.map((r) => (
                  <tr key={r.serial}>
                    <td className="mono">{String(r.published_date).slice(0, 10)}</td>
                    <td><Chip c={r.category ?? "OTHER"} /></td>
                    <td className="mono" style={{ color: "var(--fg-mute)" }}>
                      {r.subject_date ? String(r.subject_date).slice(0, 10) : "—"}
                    </td>
                    <td style={{ maxWidth: 520 }}>
                      {r.title}
                      {r.headline && (
                        <div style={{
                          marginTop: 3, fontSize: 11.5, lineHeight: 1.5,
                          color: r.headline_basis === "table" || r.headline_basis === "title"
                            ? "var(--fg-dim)" : "var(--fg-mute)",
                        }}>
                          {r.headline}
                          <span style={{ marginLeft: 6, fontSize: 10, color: "var(--fg-mute)" }}>
                            {r.headline_basis === "table" ? "from our stored figures"
                              : r.headline_basis === "title" ? "from BB's title" : ""}
                          </span>
                        </div>
                      )}
                    </td>
                    <td className="mono">{r.isin ?? ""}</td>
                    <td>
                      <a href={r.url} target="_blank" rel="noreferrer"
                         style={{ fontSize: 11, textDecoration: "none", color: "var(--info)" }}>
                        PDF
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                        borderTop: "1px solid var(--border)", lineHeight: 1.75 }}>
            <p style={{ margin: 0 }}>
              The <b style={{ color: "var(--fg)" }}>Published</b> date is when BB posted the
              release; <b style={{ color: "var(--fg)" }}>About</b> is the date it concerns —
              "Open Market Operations as on 13 October" went up on the 14th, and conflating
              the two puts every figure derived from it a day out.
            </p>
            <p style={{ margin: "6px 0 0" }}>
              Kinds come from BB's own wording, not from a summary: an unrecognised release
              keeps its title and is marked <b style={{ color: "var(--fg)" }}>Other</b>. The
              banners above are computed by comparing this listing with the data we hold —
              that is the only thing worth automating here, and it is what was missing when
              BB announced the country's first bond buyback twice and nothing reacted.
            </p>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/omo", label: "OMO", why: "the operations these releases publish" },
        { href: "/auction-data", label: "Auction Data", why: "the auction results, parsed" },
        { href: "/cashflows", label: "Cash Flows", why: "where a buyback's cash lands" },
        { href: "/guardian", label: "Guardian", why: "what the checks make of all this" },
      ]} />
    </>
  );
}
