"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import DeployStatus from "@/components/DeployStatus";

/**
 * Two grouped rows, holding EVERY page.
 *
 * This replaced a single 13-item `overflow-x-auto` row that silently scrolled
 * its tail out of view, and — worse — six finished pages were never added to it
 * at all (/forecast, /repo, /fxmarket, /guardian, /explore, /research). From
 * the desk's side those pages did not exist. tests/test_navigation.py now fails
 * the build if a page.tsx has no nav entry, which is the only thing that stops
 * that recurring.
 *
 * Grouping is by the question being asked, not by data source: row 1 is "what
 * is happening to cash", row 2 is "what are the rates and what do I hold".
 *
 * /fx is labelled FX Intervention, not "FX Auctions": it is BB's USD/BDT market
 * operations. While it was the only entry containing the word "Auctions",
 * anyone scanning the bar for treasury auctions landed there and concluded
 * there was no auctions page.
 */
const ROWS: { label: string; links: { href: string; label: string }[] }[] = [
  {
    label: "Flows & liquidity",
    links: [
      { href: "/",           label: "Overview"   },
      { href: "/auctions",   label: "Auctions"   },
      { href: "/cashflows",  label: "Cash Flows" },
      { href: "/schedule",   label: "Schedule"   },
      { href: "/forecast",   label: "Forecast"   },
      { href: "/omo",        label: "OMO"        },
      { href: "/repo",       label: "Repo"       },
      { href: "/callmoney",  label: "Call Money" },
    ],
  },
  {
    label: "Rates & reference",
    links: [
      { href: "/yields",     label: "Yields"          },
      { href: "/fx",         label: "FX Intervention" },
      { href: "/fxmarket",   label: "FX Market"       },
      { href: "/refrate",    label: "Ref Rates"       },
      { href: "/macro",      label: "External"        },
      { href: "/monetary",   label: "Monetary"        },
      { href: "/securities", label: "Securities"      },
      { href: "/portfolio",  label: "Portfolio"       },
      { href: "/explore",    label: "Explore"         },
      { href: "/research",   label: "Research"        },
      { href: "/guardian",   label: "Guardian"        },
      { href: "/glossary",   label: "Glossary"        },
    ],
  },
];

export default function Nav() {
  const path = usePathname();
  return (
    <header style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elev)" }}>
      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "0 24px" }}>
        {/* Brand row */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          height: 48, borderBottom: "1px solid var(--border-soft)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ width: 6, height: 24, borderRadius: 2, background: "var(--accent)" }} />
            <span style={{
              fontSize: 13, fontWeight: 600, letterSpacing: ".14em",
              textTransform: "uppercase", color: "var(--accent)", fontFamily: "var(--sans)",
            }}>
              BB Market Intelligence
            </span>
          </div>
          <DeployStatus />
        </div>

        {/* Nav rows — wrap rather than scroll, so nothing can hide off-edge */}
        {ROWS.map((row, i) => (
          <nav key={row.label} aria-label={row.label} style={{
            display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap",
            padding: "5px 0",
            borderBottom: i === 0 ? "1px solid var(--border-soft)" : undefined,
          }}>
            <span style={{
              fontSize: 9.5, textTransform: "uppercase", letterSpacing: ".11em",
              color: "var(--fg-mute)", marginRight: 8, minWidth: 92,
              fontFamily: "var(--sans)",
            }}>
              {row.label}
            </span>
            {row.links.map(({ href, label }) => {
              const active = path === href;
              return (
                <Link
                  key={href}
                  href={href}
                  style={{
                    padding: "4px 9px", fontSize: 12, fontWeight: 500,
                    whiteSpace: "nowrap", textDecoration: "none",
                    fontFamily: "var(--sans)",
                    color: active ? "var(--accent)" : "var(--fg-dim)",
                    borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                    background: active ? "var(--accent-soft)" : "transparent",
                    borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
                  }}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
        ))}
      </div>
    </header>
  );
}
