"use client";
import * as React from "react";
import { Panel, Delta } from "@/components/terminal/ui";
import { ComboChart, type ComboRow } from "@/components/terminal/charts";

export interface CallMoneyData {
  combo: ComboRow[];
  war: number | null;
  hi: number | null;
  lo: number | null;
  vol: number | null;
  avgWar: number | null;
  warDelta: number | null;
}

export default function CallMoneyView({ d }: { d: CallMoneyData }) {
  return (
    <div className="grid12">
      <div className="kpi-strip four">
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">WAR (today)</span>{d.warDelta != null && <Delta value={d.warDelta} />}</div>
          <div className="kpi-val"><span className="kpi-num">{d.war != null ? d.war.toFixed(2) : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">weighted avg rate</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">High / Low</span></div>
          <div className="kpi-val"><span className="kpi-num">{d.hi != null && d.lo != null ? `${d.hi.toFixed(2)}/${d.lo.toFixed(2)}` : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">intraday range</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Volume (today)</span></div>
          <div className="kpi-val"><span className="kpi-cur">৳</span><span className="kpi-num">{d.vol != null ? Math.round(d.vol).toLocaleString() : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">turnover</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Avg WAR (window)</span></div>
          <div className="kpi-val"><span className="kpi-num">{d.avgWar != null ? d.avgWar.toFixed(2) : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">mean over period</div>
        </div>
      </div>
      <Panel title="Call Money — Rate & Volume" sub="Weighted average rate (line) · turnover (bars)" span={12}>
        <ComboChart data={d.combo} height={340} />
      </Panel>
    </div>
  );
}
