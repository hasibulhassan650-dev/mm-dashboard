"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "./Icon";
import { NAV, navByPath } from "./nav";
import { useTheme, ACCENTS, type Density } from "./ThemeProvider";
import ExportAll from "./ExportAll";
import UpdateStatus from "./UpdateStatus";
import CommandPalette from "./CommandPalette";

function openCommand() { if (typeof window !== "undefined") window.dispatchEvent(new Event("bb:command")); }

export interface TickItem { sym: string; val: string; d: number }

// ---------------- Sidebar ----------------
function Sidebar({ active, collapsed, onToggle, onNav }: { active: string; collapsed: boolean; onToggle: () => void; onNav: () => void }) {
  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="brand">
        <div className="brand-mark"><span>BB</span></div>
        {!collapsed && (
          <div className="brand-txt">
            <div className="brand-name">MARKET<span> INTELLIGENCE</span></div>
            <div className="brand-sub">Bangladesh Bank · Money Market</div>
          </div>
        )}
      </div>
      <nav className="nav">
        {NAV.map((n) => {
          const on = active === n.href;
          return (
            <Link key={n.href} href={n.href} className={"nav-item" + (on ? " on" : "")} title={n.label} onClick={onNav}>
              <span className="nav-ic"><Icon name={n.icon} size={17} /></span>
              {!collapsed && <span className="nav-lb">{n.label}</span>}
              {on && <span className="nav-bar" />}
            </Link>
          );
        })}
      </nav>
      <button className="collapse-btn" onClick={onToggle} title="Collapse"><Icon name="chevron" size={15} /></button>
    </aside>
  );
}

// ---------------- Topbar ----------------
function Topbar({ title, onMenu }: { title: string; onMenu: () => void }) {
  const { theme, toggleTheme } = useTheme();
  return (
    <div className="topbar">
      <button className="icon-btn only-mobile" onClick={onMenu} aria-label="Menu"><Icon name="menu" size={18} /></button>
      <div className="crumb">
        <span className="crumb-root">BB Markets</span>
        <Icon name="chevron" size={13} />
        <span className="crumb-cur">{title}</span>
      </div>
      <button className="search" onClick={openCommand} title="Jump to… (⌘K or /)" aria-label="Open command palette">
        <Icon name="search" size={15} />
        <span className="search-ph">Jump to a page or metric…</span>
        <kbd>/</kbd>
      </button>
      <div className="topbar-right">
        <UpdateStatus />
        <ExportAll />
        <button className="icon-btn" onClick={toggleTheme} title="Toggle theme" aria-label="Toggle theme">
          <Icon name={theme === "dark" ? "sun" : "moon"} size={17} />
        </button>
      </div>
    </div>
  );
}

// ---------------- Ticker ----------------
function Ticker({ items }: { items: TickItem[] }) {
  const { tickerSpeed } = useTheme();
  if (!items.length) return null;
  const doubled = [...items, ...items];
  return (
    <div className="ticker">
      <div className="ticker-tag"><Icon name="dot" size={9} /> LIVE</div>
      <div className="ticker-track" style={{ animationDuration: tickerSpeed + "s" }}>
        {doubled.map((it, i) => (
          <span className="tick" key={i}>
            <span className="tick-sym">{it.sym}</span>
            <span className="tick-val">{it.val}</span>
            {it.d !== 0 && <span className={"tick-d " + (it.d > 0 ? "pos" : "neg")}>{it.d > 0 ? "▲" : "▼"} {Math.abs(it.d).toFixed(2)}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------- Tweaks panel ----------------
function TweaksPanel() {
  const { accent, density, grid, tickerSpeed, set } = useTheme();
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <button className="tw-fab" onClick={() => setOpen((o) => !o)} title="Customise" aria-label="Customise">
        <Icon name="sliders" size={18} />
      </button>
      {open && (
        <div className="tw-panel">
          <div className="tw-h">Appearance</div>
          <div className="tw-row">
            <span className="tw-lab">Accent</span>
            <div className="tw-swatches">
              {ACCENTS.map((c) => (
                <button key={c} className={"tw-sw" + (accent === c ? " on" : "")} style={{ background: c }} onClick={() => set("accent", c)} aria-label={"Accent " + c} />
              ))}
            </div>
          </div>
          <div className="tw-row">
            <span className="tw-lab">Density</span>
            <div className="tw-seg">
              {(["compact", "regular", "comfy"] as Density[]).map((d) => (
                <button key={d} className={density === d ? "on" : ""} onClick={() => set("density", d)}>{d}</button>
              ))}
            </div>
          </div>
          <div className="tw-h">Charts &amp; Ticker</div>
          <div className="tw-row">
            <span className="tw-lab">Chart gridlines</span>
            <button className={"tw-toggle" + (grid ? " on" : "")} onClick={() => set("grid", !grid)} aria-label="Toggle gridlines" />
          </div>
          <div className="tw-row">
            <span className="tw-lab">Ticker speed</span>
            <input className="tw-range" type="range" min={20} max={90} step={2} value={tickerSpeed} onChange={(e) => set("tickerSpeed", Number(e.target.value))} />
          </div>
        </div>
      )}
    </>
  );
}

// ---------------- App shell ----------------
export function AppShell({ children, ticker, sub }: { children: React.ReactNode; ticker: TickItem[]; sub?: string }) {
  const path = usePathname();
  const cur = navByPath(path);
  const [collapsed, setCollapsed] = React.useState(false);
  const [mobileNav, setMobileNav] = React.useState(false);

  return (
    <div className={"app" + (collapsed ? " nav-collapsed" : "") + (mobileNav ? " mobile-open" : "")}>
      <Sidebar active={cur.href} collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} onNav={() => setMobileNav(false)} />
      {mobileNav && <div className="scrim" onClick={() => setMobileNav(false)} />}
      <div className="main">
        <Topbar title={cur.label} onMenu={() => setMobileNav(true)} />
        <Ticker items={ticker} />
        <div className="content">
          <div className="view-head">
            <div>
              <h1 className="view-title">{cur.label}</h1>
              <p className="view-sub">{sub || "Bangladesh money market · live data from Bangladesh Bank"}</p>
            </div>
            <div className="view-meta">
              <span className="meta-dot" /> Live · cloud-refreshed daily
            </div>
          </div>
          {children}
        </div>
      </div>
      <TweaksPanel />
      <CommandPalette />
    </div>
  );
}
