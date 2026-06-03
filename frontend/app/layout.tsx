import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/components/terminal/ThemeProvider";
import { AppShell, type TickItem } from "@/components/terminal/AppShell";
import { api } from "@/lib/api";

const plexSans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-plex-sans" });
const plexMono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: "BB Market Intelligence",
  description: "Bangladesh Bank money market intelligence terminal",
};

// Set the persisted theme/density before paint to avoid a flash of the default.
const THEME_INIT = `(function(){try{var t=JSON.parse(localStorage.getItem('bb_tweaks')||'{}');var h=document.documentElement;if(t.theme)h.setAttribute('data-theme',t.theme);if(t.density)h.setAttribute('data-density',t.density);if(t.accent)h.style.setProperty('--accent',t.accent);}catch(e){}})();`;

async function buildTicker(): Promise<TickItem[]> {
  const ticks: TickItem[] = [];
  try {
    // NOTE: the policy corridor (repo/SDF/SLF) is intentionally NOT in the live
    // ticker — it is seed/DRAFT data and its only "change" is a >1-year-old MPC
    // move, which is misleading shown as a live delta. The ticker carries ONLY
    // verified, real-time series with genuine latest-print deltas.
    const [curve, history, callmoney, macro] = await Promise.all([
      api.yieldCurve().catch(() => []),
      api.yields(3).catch(() => []),
      api.callmoney(30).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null })),
      api.macro(),
    ]);

    // change of the two most-recent distinct readings (rounded to 2dp)
    const diff = (a?: number | null, b?: number | null) =>
      a != null && b != null ? +(a - b).toFixed(2) : 0;

    const ds = [...callmoney.daily_summary].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
    if (ds.length) {
      const last = ds[ds.length - 1], prev = ds[ds.length - 2];
      if (last.overnight_wavg_rate != null)
        ticks.push({ sym: "CALL WAR", val: last.overnight_wavg_rate.toFixed(2) + "%", d: diff(last.overnight_wavg_rate, prev?.overnight_wavg_rate) });
    }

    // per-tenor yield: latest cut-off vs previous auction for that tenor
    const tenorDelta = (t: string) => {
      const rs = history.filter((r) => r.tenor_label === t).sort((a, b) => a.auction_date.localeCompare(b.auction_date));
      return rs.length >= 2 ? diff(rs[rs.length - 1].cutoff_yield_pct, rs[rs.length - 2].cutoff_yield_pct) : 0;
    };
    for (const t of ["91D", "182D", "364D", "2Y", "5Y", "10Y", "20Y"]) {
      const row = curve.find((r) => r.tenor_label === t);
      if (row) ticks.push({ sym: t + " TB", val: row.cutoff_yield_pct.toFixed(2) + "%", d: tenorDelta(t) });
    }

    // reserves & remittance: latest month vs previous month
    const ms = [...macro.series].sort((a, b) => a.month.localeCompare(b.month));
    const lastTwo = (k: "gross_reserves_usd_bn" | "remittance_usd_mn") => {
      const v = ms.filter((m) => m[k] != null);
      return v.length >= 2 ? [v[v.length - 1][k] as number, v[v.length - 2][k] as number] as const : null;
    };
    if (macro.latest?.gross_reserves_usd_bn != null) {
      const tt = lastTwo("gross_reserves_usd_bn");
      ticks.push({ sym: "FX RES", val: "$" + macro.latest.gross_reserves_usd_bn.toFixed(2) + "B", d: tt ? diff(tt[0], tt[1]) : 0 });
    }
    if (macro.latest?.remittance_usd_mn != null) {
      const tt = lastTwo("remittance_usd_mn");
      ticks.push({ sym: "REMIT", val: "$" + (macro.latest.remittance_usd_mn / 1000).toFixed(2) + "B", d: tt ? +((tt[0] - tt[1]) / 1000).toFixed(2) : 0 });
    }
  } catch { /* ticker degrades to whatever was collected */ }
  return ticks;
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const ticker = await buildTicker();
  return (
    <html lang="en" data-theme="dark" data-density="regular" suppressHydrationWarning className={`${plexSans.variable} ${plexMono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>
        <ThemeProvider>
          <AppShell ticker={ticker}>{children}</AppShell>
        </ThemeProvider>
      </body>
    </html>
  );
}
