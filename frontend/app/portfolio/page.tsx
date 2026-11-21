import { api } from "@/lib/api";
import PortfolioTool from "@/components/PortfolioTool";
import Unavailable from "@/components/Unavailable";
import { tracker } from "@/lib/safe";

export const revalidate = 300;

export default async function PortfolioPage() {
  const t = tracker();
  const [secondary, securities, policy] = await Promise.all([
    t.soft("secondary-market curve", api.yieldSecondary(), []),
    t.soft("securities", api.securities(), []),
    t.soft("policy rate", api.policy(), { current: null, history: [] }),
  ]);
  // No default funding rate: a made-up repo rate would silently drive every carry figure. When the policy rate is
  // not available the funding field starts EMPTY and the tool asks for it.
  const repoDefault = policy.verified ? (policy.current?.repo ?? null) : null;   // an unverified/seed corridor is not a rate to price carry on
  return (
    <>
      <Unavailable what={t.failed} />
      <PortfolioTool secondary={secondary} securities={securities} repoDefault={repoDefault} />
    </>
  );
}
