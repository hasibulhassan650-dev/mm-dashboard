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
    const [policy, curve, callmoney, macro] = await Promise.all([
      api.policy(),
      api.yieldCurve().catch(() => []),
      api.callmoney(30).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null })),
      api.macro(),
    ]);

    const c = policy.current;
    if (c?.repo != null) ticks.push({ sym: "REPO", val: c.repo.toFixed(2) + "%", d: 0 });
    if (c?.sdf != null) ticks.push({ sym: "SDF", val: c.sdf.toFixed(2) + "%", d: 0 });
    if (c?.slf != null) ticks.push({ sym: "SLF", val: c.slf.toFixed(2) + "%", d: 0 });

    const ds = [...callmoney.daily_summary].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
    if (ds.length) {
      const last = ds[ds.length - 1], prev = ds[ds.length - 2];
      if (last.overnight_wavg_rate != null) {
        const d = prev?.overnight_wavg_rate != null ? +(last.overnight_wavg_rate - prev.overnight_wavg_rate).toFixed(2) : 0;
        ticks.push({ sym: "CALL WAR", val: last.overnight_wavg_rate.toFixed(2) + "%", d });
      }
    }

    for (const t of ["91D", "182D", "364D", "2Y", "5Y", "10Y", "20Y"]) {
      const row = curve.find((r) => r.tenor_label === t);
      if (row) ticks.push({ sym: t + " TB", val: row.cutoff_yield_pct.toFixed(2) + "%", d: 0 });
    }

    if (macro.latest?.gross_reserves_usd_bn != null) ticks.push({ sym: "FX RES", val: "$" + macro.latest.gross_reserves_usd_bn.toFixed(2) + "B", d: 0 });
    if (macro.latest?.remittance_usd_mn != null) ticks.push({ sym: "REMIT", val: "$" + (macro.latest.remittance_usd_mn / 1000).toFixed(2) + "B", d: 0 });
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
