import { api } from "@/lib/api";
import type { DeskMarks } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import DeskMarkForm from "@/components/DeskMarkForm";
import DeskSetup from "@/components/DeskSetup";
import DeskBuybackForm from "@/components/DeskBuybackForm";
import RelatedLinks from "@/components/RelatedLinks";

export const revalidate = 0;   // a mark entered a second ago must show

export default async function DeskPage() {
  let data: DeskMarks | null = null;
  try { data = await api.deskMarks(60); } catch { data = null; }

  // The page is a server component, so it can read the SITE's own env var - a
  // boolean only, never the value. The API's side comes from /api/desk/marks.
  const siteSet = !!process.env.DESK_WRITE_SECRET;
  const apiSet = data?.configured ?? false;
  const marks = data?.marks ?? [];
  const confirmed = marks.filter((m) => m.confirmed);
  const worst = data?.worst_diff_bps ?? null;

  return (
    <>
      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Marks</span></div>
          <div className="kpi-val"><span className="kpi-num">{marks.length}</span></div>
          <div className="kpi-sub">last {data?.window_days ?? 60} days</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Awaiting BB</span></div>
          <div className="kpi-val">
            <span className="kpi-num" style={{ color: (data?.unconfirmed ?? 0) ? "var(--warn)" : "var(--pos)" }}>
              {data?.unconfirmed ?? 0}
            </span>
          </div>
          <div className="kpi-sub">still provisional</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Confirmed</span></div>
          <div className="kpi-val"><span className="kpi-num">{confirmed.length}</span></div>
          <div className="kpi-sub">BB has published</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Worst gap</span></div>
          <div className="kpi-val">
            <span className="kpi-num" style={{ color: worst != null && worst > 10 ? "var(--warn)" : undefined }}>
              {worst == null ? "—" : `${worst.toFixed(1)}`}
            </span>
          </div>
          <div className="kpi-sub">bp, desk vs BB</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Enter today's cut-off" span={5}
               sub="what the desk saw at the auction, before BB publishes">
          <DeskSetup siteSet={siteSet} apiSet={apiSet} />
          <DeskMarkForm tenors={data?.tenors ?? []} configured={siteSet && apiSet} />
        </Panel>

        <Panel
          title="Desk marks against BB"
          sub={`${marks.length} mark(s) · BB's figure replaces the mark the moment it is published`}
          span={7}
          pad={false}
        >
          <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Auction</th>
                <th>Tenor</th>
                <th className="r">Desk</th>
                <th className="r">BB</th>
                <th className="r">Diff</th>
                <th>Status</th>
                <th>By</th>
              </tr></thead>
              <tbody>
                {marks.length === 0 && (
                  <tr><td colSpan={7} style={{ padding: "10px 12px", fontSize: 12.5,
                                               color: "var(--fg-mute)" }}>
                    No marks yet. Enter one on auction day and it appears here, provisional,
                    until BB publishes the result.
                  </td></tr>
                )}
                {marks.map((m) => (
                  <tr key={`${m.auction_date}-${m.tenor_label}`}>
                    <td className="mono">{String(m.auction_date).slice(0, 10)}</td>
                    <td className="mono">{m.tenor_label}</td>
                    <td className="r mono">{m.cutoff_yield_pct.toFixed(4)}%</td>
                    <td className="r mono">
                      {m.bb_cutoff_yield_pct == null
                        ? <span style={{ color: "var(--fg-mute)" }}>—</span>
                        : `${m.bb_cutoff_yield_pct.toFixed(4)}%`}
                    </td>
                    <td className="r mono" style={{
                      color: m.diff_bps == null ? undefined
                           : Math.abs(m.diff_bps) > 10 ? "var(--warn)" : "var(--pos)",
                    }}>
                      {m.diff_bps == null ? "" : `${m.diff_bps > 0 ? "+" : ""}${m.diff_bps.toFixed(1)}bp`}
                    </td>
                    <td style={{ fontSize: 11 }}>
                      {m.confirmed
                        ? <span style={{ color: "var(--pos)" }}>BB confirmed</span>
                        : <span style={{ color: "var(--warn)" }}>provisional</span>}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--fg-mute)" }}>{m.entered_by ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                        borderTop: "1px solid var(--border)", lineHeight: 1.75 }}>
            <p style={{ margin: 0 }}>
              A desk mark is <b style={{ color: "var(--fg)" }}>never stored as BB data</b>. It
              lives in its own table and is labelled provisional everywhere it appears; BB's
              published cut-off is preferred the moment it exists. The mark is kept rather than
              overwritten, because the gap between the two is worth reading — more than 10bp
              out is either a typo or a surprise in the auction.
            </p>
            <p style={{ margin: "6px 0 0" }}>
              BB prints the <b style={{ color: "var(--fg)" }}>issue</b> date and the auction was
              the previous working day, which is why a published cut-off lands here a day or two
              after the desk has already traded off it.
            </p>
          </div>
        </Panel>
      </div>

      <div className="grid12">
        <Panel title="Announced buyback — before BB publishes"
               sub="the desk's figure, provisional · never added to a cash total" span={5}>
          <DeskBuybackForm configured={siteSet && apiSet} />
        </Panel>
        <Panel title="Desk buyback figures against BB" span={7} pad={false}
               sub={`${(data?.buybacks ?? []).length} entered`}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Settles</th><th>ISIN</th><th className="r">Desk face (cr)</th>
                <th className="r">BB face (cr)</th><th className="r">Gap</th><th>Status</th>
              </tr></thead>
              <tbody>
                {(data?.buybacks ?? []).length === 0 && (
                  <tr><td colSpan={6} style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--fg-mute)" }}>
                    None yet. Enter an announced buyback here and it shows on its settlement day as provisional.
                  </td></tr>
                )}
                {(data?.buybacks ?? []).map((b) => (
                  <tr key={`${b.isin}-${b.settlement_date}`}>
                    <td className="mono">{String(b.settlement_date).slice(0, 10)}</td>
                    <td className="mono">{b.isin}</td>
                    <td className="r mono">{b.accepted_face_crore.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td className="r mono">{b.bb_accepted_face_crore == null ? "—"
                      : b.bb_accepted_face_crore.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td className="r mono">{b.diff_face_crore == null ? "" : b.diff_face_crore.toFixed(2)}</td>
                    <td style={{ fontSize: 11 }}>
                      {b.confirmed ? <span style={{ color: "var(--pos)" }}>BB confirmed</span>
                        : <span style={{ color: "var(--warn)" }}>provisional</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/auction-data", label: "Auction Data", why: "BB's own results, column for column" },
        { href: "/yields", label: "Yield Curve", why: "where these cut-offs sit on the curve" },
        { href: "/auctions", label: "Treasury Auctions", why: "bids, cover and devolvement" },
      ]} />
    </>
  );
}
