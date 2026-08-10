import { api } from "@/lib/api";
import PortfolioTool from "@/components/PortfolioTool";

export const revalidate = 300;

export default async function PortfolioPage() {
  const [secondary, securities, policy] = await Promise.all([
    api.yieldSecondary().catch(() => []),
    api.securities().catch(() => []),
    api.policy().catch(() => ({ current: null, history: [] })),
  ]);
  const repoDefault = policy.current?.repo ?? 9.5;
  return <PortfolioTool secondary={secondary} securities={securities} repoDefault={repoDefault} />;
}
