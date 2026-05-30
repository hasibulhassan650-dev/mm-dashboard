import { api } from "@/lib/api";
import PortfolioTool from "@/components/PortfolioTool";
import Freshness from "@/components/Freshness";

export const revalidate = 300;

export default async function PortfolioPage() {
  const [secondary, securities, fresh] = await Promise.all([
    api.yieldSecondary(),
    api.securities(),
    api.freshness(),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Portfolio Mark-to-Market</h1>
        <p className="text-sm text-gray-400">
          Value a holdings list against the live GSOM secondary curve · interest-rate sensitivity
        </p>
        <div className="mt-1"><Freshness updated={fresh.secondary} label="Curve as of" /></div>
      </div>
      <PortfolioTool secondary={secondary} securities={securities} />
    </div>
  );
}
