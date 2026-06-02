import { api } from "@/lib/api";
import { buildCurve } from "@/lib/terminal";
import YieldsView from "@/components/terminal/views/YieldsView";

export const revalidate = 300;

export default async function YieldsPage() {
  const [curve, history] = await Promise.all([
    api.yieldCurve().catch(() => []),
    api.yields(6).catch(() => []),
  ]);
  const cb = buildCurve(curve, history);
  return <YieldsView d={{ tenors: cb.tenors, today: cb.today, weekAgo: cb.weekAgo, monthAgo: cb.monthAgo }} />;
}
