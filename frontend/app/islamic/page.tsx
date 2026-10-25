import { api } from "@/lib/api";
import type { IslamicWindow, OmoInjection, OmoTxnRow } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import RelatedLinks from "@/components/RelatedLinks";
import IslamicFacilities from "@/components/IslamicFacilities";

export const revalidate = 300;

const n2 = (v: number | null | undefined) =>
  v == null ? "" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default async function IslamicPage() {
  let data: IslamicWindow | null = null;
  try { data = await api.islamic(26); } catch { data = null; }
  // The Islamic banks' own facilities live in the OMO data; this page shows them,
  // it does not keep a second copy.
  const [inj, txns]: [OmoInjection | null, OmoTxnRow[]] = await Promise.all([
    api.omoInjection(30).catch(() => null),
    api.omoTransactions(90).catch(() => [] as OmoTxnRow[]),
  ]);

  if (!data || !data.count) {
    return (
      <Panel title="Islamic Window — BGIIB" span={12}>
        <p style={{ margin: 0, fontSize: 13, color: "var(--warn)" }}>
          Nothing stored yet, or <code>/api/islamic</code> is not deployed. BGIIB figures are
          parsed from BB's weekly press releases; no numbers are invented in the meantime.
        </p>
      </Panel>
    );
  }

  return (
    <>
      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))" }}>
        {data.by_tenor.map((t) => (
          <div className="kpi" key={t.tenor_label}>
            <div className="kpi-top"><span className="kpi-label">{t.tenor_label} BGIIB — latest EPR</span></div>
            <div className="kpi-val">
              <span className="kpi-num">{t.latest_epr_pct == null ? "—" : `${t.latest_epr_pct.toFixed(2)}%`}</span>
            </div>
            <div className="kpi-sub">{t.latest_date} · {t.auctions} auctions, {n2(t.accepted_crore)} cr accepted</div>
          </div>
        ))}
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Auctions</span></div>
          <div className="kpi-val"><span className="kpi-num">{data.count}</span></div>
          <div className="kpi-sub">last {data.weeks} weeks · latest {data.latest_auction}</div>
        </div>
      </div>

      <div className="grid12">
        <Panel
          title="BGIIB auctions"
          sub="Bangladesh Government Islami Investment Bond · weekly · BDT crore"
          span={12}
          pad={false}
        >
          <div className="table-wrap" style={{ maxHeight: 560, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Auction</th>
                <th>Tenor</th>
                <th className="r">Bids</th>
                <th className="r">Offered</th>
                <th className="r">Accepted</th>
                <th>PSR</th>
                <th className="r">Wtd avg EPR</th>
                <th>EPR range</th>
                <th className="r">vs T-Bill</th>
                <th />
              </tr></thead>
              <tbody>
                {data.auctions.map((a) => (
                  <tr key={`${a.auction_date}-${a.tenor_label}`}>
                    <td className="mono">{String(a.auction_date).slice(0, 10)}</td>
                    <td className="mono">{a.tenor_label}</td>
                    <td className="r mono">
                      {a.bids_accepted}
                      {a.bids_offered !== a.bids_accepted && (
                        <span style={{ color: "var(--fg-mute)" }}>/{a.bids_offered}</span>
                      )}
                    </td>
                    <td className="r mono">{n2(a.offered_crore)}</td>
                    <td className="r mono">{n2(a.accepted_crore)}</td>
                    <td className="mono" style={{ color: "var(--fg-dim)" }}>{a.psr ?? "—"}</td>
                    <td className="r mono">
                      {a.war_epr_pct == null ? "—" : `${a.war_epr_pct.toFixed(2)}%`}
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: "var(--fg-dim)" }}>
                      {a.epr_range ?? "—"}
                    </td>
                    <td className="r mono" style={{
                      color: a.epr_minus_tbill_bps == null ? undefined
                           : a.epr_minus_tbill_bps < 0 ? "var(--pos)" : "var(--warn)",
                    }}
                        title={a.comparable_tbill_date
                          ? `${a.comparable_tbill_tenor} cut-off ${a.comparable_tbill_cutoff_pct}% at the auction of ${a.comparable_tbill_date}`
                          : "no comparable T-Bill cut-off on or before this date"}>
                      {a.epr_minus_tbill_bps == null ? "—"
                        : `${a.epr_minus_tbill_bps > 0 ? "+" : ""}${a.epr_minus_tbill_bps.toFixed(0)}bp`}
                    </td>
                    <td>
                      {a.source && (
                        <a href={a.source} target="_blank" rel="noreferrer"
                           style={{ fontSize: 11, textDecoration: "none", color: "var(--info)" }}>
                          PDF
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                        borderTop: "1px solid var(--border)", lineHeight: 1.75 }}>
            <p style={{ margin: 0 }}>
              <b style={{ color: "var(--fg)" }}>EPR</b> is the Expected Profit Rate — the
              Islamic-finance counterpart of a yield, and the figure to hold against a T-Bill
              cut-off of the same tenor. <b style={{ color: "var(--fg)" }}>PSR</b> is the
              profit-sharing ratio with the government: a split, not a rate, so it is never
              averaged or charted.
            </p>
            <p style={{ margin: "6px 0 0" }}>
              <b style={{ color: "var(--fg)" }}>vs T-Bill</b> compares the EPR with the last
              comparable cut-off <i>on or before</i> that auction — BGIIB is auctioned on
              Tuesdays and T-Bills on Sundays, so a same-day pair does not exist. Hover to see
              which auction the cut-off came from. Nothing is interpolated, and no print after
              the BGIIB auction is used.
            </p>
            <p style={{ margin: "6px 0 0" }}>
              Every release is checked against BB's own Total line before it is stored. A
              release whose rows do not add up is refused whole and reported, because one
              missing tenor misstates the size of the window.
            </p>
          </div>
        </Panel>
      </div>

      <div className="grid12">
        <IslamicFacilities inj={inj} txns={txns} />
      </div>

      <RelatedLinks items={[
        { href: "/yields", label: "Yield Curve", why: "the conventional cut-offs these are measured against" },
        { href: "/press", label: "Press Releases", why: "the releases these figures come from" },
        { href: "/auction-data", label: "Auction Data", why: "BB's T-Bill and T-Bond results in full" },
      ]} />
    </>
  );
}
