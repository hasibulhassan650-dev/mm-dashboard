import { fmtDate } from "@/lib/format";
import type { NextAuctionResult } from "@/lib/api";

/**
 * What the desk needs to know every morning: when the next auction is, and
 * whether anything is in the way of it.
 *
 * Three states, deliberately distinct — the third is the one that matters most:
 *   1. an auction is scheduled  → name the day, the tenors and the size;
 *   2. it falls on a closure    → flag the clash, but never invent the new date;
 *   3. BB has published nothing → SAY SO. Rendering an empty space would read
 *      as "no auction due", which is a completely different claim and the one
 *      that would actually cost money.
 *
 * The weekday habit (bills Sunday, bonds Tuesday) is never assumed: everything
 * here comes from BB's published calendar.
 */
const DAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function dayName(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return DAY[d.getUTCDay()] ?? "";
}

function when(daysAway: number, iso: string): string {
  if (daysAway === 0) return "today";
  if (daysAway === 1) return "tomorrow";
  return `${dayName(iso)} ${fmtDate(iso)}`;
}

export default function NextAuction({ data }: { data: NextAuctionResult }) {
  const box = (tone: string, children: React.ReactNode) => (
    <div style={{
      background: `color-mix(in oklab, ${tone} 11%, var(--panel))`,
      border: `1px solid color-mix(in oklab, ${tone} 45%, transparent)`,
      borderRadius: "var(--radius)", padding: "11px 14px", marginBottom: "var(--gap)",
      display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap",
    }}>
      <span style={{ color: tone, fontWeight: 700, fontSize: 14, lineHeight: 1.3 }}>◆</span>
      <div style={{ minWidth: 240, flex: 1, fontSize: 12.5, lineHeight: 1.55 }}>{children}</div>
    </div>
  );

  // 3. Nothing published — the honest, and currently the real, case.
  if (!data.published || !data.next) {
    return box("var(--warn)", (
      <>
        <b style={{ color: "var(--warn)" }}>No auction calendar published beyond{" "}
          {data.calendar_through ? fmtDate(data.calendar_through) : "the stored history"}.</b>
        <div style={{ color: "var(--fg-dim)", marginTop: 2 }}>
          Bangladesh Bank has not yet put out the next quarter&apos;s auction calendar, so the
          forward auction outflow on this site is <b>missing, not zero</b> — treat the cash-flow
          ladder beyond that date as incomplete until BB publishes.
        </div>
      </>
    ));
  }

  const n = data.next;
  const bills = n.security_types.includes("T_BILL");
  const kind = bills && n.security_types.length === 1 ? "bill" : n.security_types.length > 1 ? "bill & bond" : "bond";
  const tone = n.is_holiday ? "var(--warn)" : n.days_away <= 1 ? "var(--accent)" : "var(--info)";

  return box(tone, (
    <>
      <b style={{ color: tone }}>
        Next {kind} auction {when(n.days_away, n.auction_date)} — {n.tenors.join(", ")}
      </b>
      <div style={{ color: "var(--fg-dim)", marginTop: 2 }}>
        {n.offered_total_crore > 0 && <>৳{Math.round(n.offered_total_crore).toLocaleString()} crore offered · </>}
        settles {n.settlement_date ? fmtDate(n.settlement_date) : "—"}
        {data.following.length > 0 && (
          <> · then {data.following.map((f) => `${fmtDate(f.auction_date)} (${f.tenors.join("/")})`).join(", ")}</>
        )}
      </div>
      {n.is_holiday && (
        <div style={{ color: "var(--warn)", marginTop: 4 }}>
          ⚠ {fmtDate(n.auction_date)} is a closure{n.holiday_name ? ` — ${n.holiday_name}` : ""}. BB will move this
          auction; the calendar above still shows the original date and the new one is not guessed here.
          Confirm against BB&apos;s notice.
        </div>
      )}
    </>
  ));
}
