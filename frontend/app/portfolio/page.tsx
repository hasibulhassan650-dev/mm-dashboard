import { api } from "@/lib/api";
import PortfolioTool from "@/components/PortfolioTool";

export const revalidate = 300;

export default async function PortfolioPage() {
  const [secondary, securities] = await Promise.all([
    api.yieldSecondary().catch(() => []),
    api.securities().catch(() => []),
  ]);
  return <PortfolioTool secondary={secondary} securities={securities} />;
}
