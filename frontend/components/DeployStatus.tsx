"use client";
import { useEffect, useState, useCallback } from "react";
import type { DeployStatusPayload } from "@/app/api/deploy-status/route";

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diffMs = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diffMs / 1000);
  if (s < 60)  return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)  return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const STATE_CONFIG = {
  BUILDING: { dot: "bg-yellow-400 animate-pulse", label: "Deploying…", text: "text-yellow-400" },
  QUEUED:   { dot: "bg-yellow-400 animate-pulse", label: "Queued",      text: "text-yellow-400" },
  READY:    { dot: "bg-emerald-400",               label: "Live",        text: "text-emerald-400" },
  ERROR:    { dot: "bg-red-500",                   label: "Failed",      text: "text-red-400"    },
  CANCELED: { dot: "bg-gray-500",                  label: "Canceled",    text: "text-gray-400"   },
  UNKNOWN:  { dot: "bg-gray-500",                  label: "Live",        text: "text-gray-400"   },
};

export default function DeployStatus() {
  const [data, setData] = useState<DeployStatusPayload | null>(null);
  const [, setTick] = useState(0); // force re-render for timeAgo refresh

  const fetch_ = useCallback(() => {
    fetch("/api/deploy-status")
      .then(r => r.json())
      .then(setData)
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch_();
    // Poll fast when building, slow otherwise
    const interval = setInterval(() => {
      fetch_();
    }, data?.state === "BUILDING" || data?.state === "QUEUED" ? 12_000 : 300_000);
    return () => clearInterval(interval);
  }, [fetch_, data?.state]);

  // Refresh displayed timestamps every 30s without re-fetching
  useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  if (!data) return null;

  const cfg = STATE_CONFIG[data.state] ?? STATE_CONFIG.UNKNOWN;
  const ts  = data.deployedAt ?? data.buildTime;

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${cfg.dot}`} />
      <span className={cfg.text}>{cfg.label}</span>
      {ts && (
        <span className="text-gray-500 hidden sm:inline">{timeAgo(ts)}</span>
      )}
      {data.commitSha && (
        <span className="text-gray-600 font-mono hidden md:inline">{data.commitSha}</span>
      )}
    </div>
  );
}
