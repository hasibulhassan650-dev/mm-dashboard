import * as React from "react";

const PATHS: Record<string, React.ReactNode> = {
  grid: (<><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>),
  flow: (<><path d="M4 7h9" /><path d="M11 4l3 3-3 3" /><path d="M20 17h-9" /><path d="M13 14l-3 3 3 3" /></>),
  layers: (<><path d="M12 3l9 5-9 5-9-5 9-5Z" /><path d="M3 13l9 5 9-5" /></>),
  curve: (<><path d="M3 20v-2c0-7 5-11 12-12h6" /><path d="M3 20h18" /></>),
  pulse: (<><path d="M3 12h4l2-6 4 14 2-8h6" /></>),
  swap: (<><path d="M7 4 3 8l4 4" /><path d="M3 8h13" /><path d="m17 20 4-4-4-4" /><path d="M21 16H8" /></>),
  ruler: (<><rect x="3" y="8" width="18" height="8" rx="1" /><path d="M7 8v3M11 8v4M15 8v3M19 8v4" /></>),
  globe: (<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></>),
  bank: (<><path d="M3 9l9-5 9 5" /><path d="M4 9v8M9 9v8M15 9v8M20 9v8" /><path d="M3 20h18" /></>),
  doc: (<><path d="M6 3h8l5 5v13H6Z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h6" /></>),
  briefcase: (<><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><path d="M3 12h18" /></>),
  book: (<><path d="M4 5v15a1 1 0 0 0 1 1h14V4H6a2 2 0 0 0-2 2Z" /><path d="M8 4v16" /></>),
  sun: (<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" /></>),
  moon: <path d="M21 12.8A8 8 0 1 1 11 3a6 6 0 0 0 10 9.8Z" />,
  search: (<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>),
  arrow: (<><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>),
  dot: <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />,
  chevron: <path d="m9 6 6 6-6 6" />,
  menu: (<><path d="M3 6h18M3 12h18M3 18h18" /></>),
  sliders: (<><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" /></>),
  download: (<><path d="M12 3v12" /><path d="m7 11 5 5 5-5" /><path d="M5 21h14" /></>),
};

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className="icn"
      style={{ fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" }}
    >
      {PATHS[name] || PATHS.dot}
    </svg>
  );
}
