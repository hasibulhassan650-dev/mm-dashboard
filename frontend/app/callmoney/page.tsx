import { api } from "@/lib/api";
import { callMoneyCombo } from "@/lib/terminal";
import CallMoneyView from "@/components/terminal/views/CallMoneyView";

export const revalidate = 300;

export default async function CallMoneyPage() {
  const cm = await api.callmoney(90).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null }));
  const combo = callMoneyCombo(cm);
  const sorted = [...cm.daily_summary].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const last = sorted[sorted.length - 1];
  const prev = sorted[sorted.length - 2];
  const avgWar = combo.length ? combo.reduce((s, r) => s + r.war, 0) / combo.length : null;
  const warDelta = last?.overnight_wavg_rate != null && prev?.overnight_wavg_rate != null
    ? +(last.overnight_wavg_rate - prev.overnight_wavg_rate).toFixed(2) : null;

  return (
    <CallMoneyView d={{
      combo,
      war: last?.overnight_wavg_rate ?? null,
      hi: last?.overnight_high ?? null,
      lo: last?.overnight_low ?? null,
      vol: last?.total_volume_crore ?? null,
      avgWar,
      warDelta,
    }} />
  );
}
