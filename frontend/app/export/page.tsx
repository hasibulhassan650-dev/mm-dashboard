import { api, API_BASE } from "@/lib/api";
import type { ExportCatalog } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import RelatedLinks from "@/components/RelatedLinks";

export const revalidate = 300;

function iso(d: Date) { return d.toISOString().slice(0, 10); }

/** Build the download URL from what the export says it accepts, so this page
 *  does not keep a second copy of each export's argument list. */
function url(path: string, args: string[], from: string, to: string, span: number) {
  const q = new URLSearchParams();
  if (args.includes("date_from")) q.set("date_from", from);
  if (args.includes("date_to")) q.set("date_to", to);
  if (args.includes("years")) q.set("years", String(span));
  else if (args.includes("months") && !args.includes("date_from")) q.set("months", String(span * 12));
  return `${API_BASE}${path}${q.toString() ? `?${q}` : ""}`;
}

export default async function ExportPage({
  searchParams,
}: { searchParams: Promise<{ from?: string; to?: string; years?: string }> }) {
  const sp = await searchParams;
  const today = new Date();
  const to = sp.to ?? iso(today);
  const from = sp.from ?? iso(new Date(today.getFullYear() - 2, today.getMonth(), today.getDate()));
  const years = Math.min(20, Math.max(1, Number(sp.years ?? 20)));

  let cat: ExportCatalog | null = null;
  try { cat = await api.exports(); } catch { cat = null; }

  if (!cat || !cat.count) {
    return (
      <Panel title="Export" span={12}>
        <p style={{ margin: 0, fontSize: 13, color: "var(--warn)" }}>
          <code>/api/exports</code> is not deployed yet. Each page still has its own
          Download button in the meantime.
        </p>
      </Panel>
    );
  }

  return (
    <>
      <Panel title="Export" span={12}
             sub={`${cat.count} workbooks · every exportable dataset, with the dates it covers · as of ${cat.as_of}`}>
        <form method="get" style={{ display: "flex", gap: 12, alignItems: "flex-end",
                                    flexWrap: "wrap", marginBottom: 4 }}>
          <label style={{ fontSize: 11, color: "var(--fg-mute)" }}>
            <div style={{ textTransform: "uppercase", letterSpacing: 0.3, marginBottom: 3 }}>From</div>
            <input type="date" name="from" defaultValue={from} max={iso(today)}
                   style={{ background: "var(--panel)", color: "var(--fg)", fontSize: 12.5,
                            border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                            padding: "5px 8px" }} />
          </label>
          <label style={{ fontSize: 11, color: "var(--fg-mute)" }}>
            <div style={{ textTransform: "uppercase", letterSpacing: 0.3, marginBottom: 3 }}>To</div>
            <input type="date" name="to" defaultValue={to}
                   style={{ background: "var(--panel)", color: "var(--fg)", fontSize: 12.5,
                            border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                            padding: "5px 8px" }} />
          </label>
          <label style={{ fontSize: 11, color: "var(--fg-mute)" }}>
            <div style={{ textTransform: "uppercase", letterSpacing: 0.3, marginBottom: 3 }}>
              Forward years
            </div>
            <input type="number" name="years" defaultValue={years} min={1} max={20}
                   style={{ width: 80, background: "var(--panel)", color: "var(--fg)",
                            fontSize: 12.5, border: "1px solid var(--border)",
                            borderRadius: "var(--radius-sm)", padding: "5px 8px" }} />
          </label>
          <button type="submit" className="seg-b">Apply to all</button>
          <span style={{ fontSize: 11.5, color: "var(--fg-mute)" }}>
            The date range applies to the historical exports; forward years applies to the
            schedule, which projects coupons and redemptions ahead.
          </span>
        </form>
      </Panel>

      <div className="grid12">
        {cat.exports.map((e) => {
          const c = e.coverage ?? {};
          const href = url(e.path, e.args, from, to, years);
          return (
            <Panel key={e.key} title={e.label} span={6}
                   sub={e.sheets ? `sheets: ${e.sheets}` : undefined}>
              <p style={{ margin: "0 0 10px", fontSize: 12.5, color: "var(--fg-dim)",
                          lineHeight: 1.7 }}>{e.what}</p>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "baseline",
                            marginBottom: 10, fontSize: 11.5 }}>
                <span style={{ color: "var(--fg-mute)" }}>
                  Data available:{" "}
                  <b className="mono" style={{ color: "var(--fg)" }}>
                    {c.earliest ?? "—"} → {c.latest ?? "—"}
                  </b>
                </span>
                <span style={{ color: "var(--fg-mute)" }}>
                  {c.rows == null ? "" : `${c.rows.toLocaleString()} source rows`}
                </span>
                <span style={{ color: "var(--fg-mute)" }}>{e.unit}</span>
              </div>
              <a href={href} className="seg-b" style={{ textDecoration: "none" }}>
                Download Excel
              </a>
              <div style={{ marginTop: 8, fontSize: 10.5, color: "var(--fg-mute)" }}>
                Accepts: <span className="mono">{e.args.join(", ")}</span>
                {c.error && <span style={{ color: "var(--warn)" }}> · {c.error}</span>}
              </div>
            </Panel>
          );
        })}
      </div>

      <Panel title="What these files are" span={12}>
        <div style={{ fontSize: 11.5, color: "var(--fg-mute)", lineHeight: 1.8 }}>
          <p style={{ margin: 0 }}>
            Every workbook is built on the server with real formatting — bold frozen headers,
            number formats, an autofilter and a cover sheet naming the source and the window —
            rather than assembled in the browser. A figure BB has not published is left{" "}
            <b style={{ color: "var(--fg)" }}>blank</b>, never zero: an empty cell is a
            question, a zero is a lie.
          </p>
          <p style={{ margin: "6px 0 0" }}>
            The <b style={{ color: "var(--fg)" }}>Data available</b> dates are read from the
            database as this page loads, so they are what the export can really cover today —
            asking for a window outside them returns the part that exists, not an error.
          </p>
          <p style={{ margin: "6px 0 0" }}>
            Amounts are BDT crore unless a column says otherwise, and an auction's cash line is
            the <b style={{ color: "var(--fg)" }}>sale value</b> — what the market actually
            paid — not the face value allotted. The two differ on every auction.
          </p>
        </div>
      </Panel>

      <RelatedLinks items={[
        { href: "/auction-data", label: "Auction Data", why: "the sheet this export mirrors" },
        { href: "/schedule", label: "Schedule", why: "the monthly view behind that workbook" },
        { href: "/cashflows", label: "Cash Flows", why: "the ladder, on screen" },
        { href: "/omo", label: "OMO", why: "the operations behind the OMO ladder" },
      ]} />
    </>
  );
}
