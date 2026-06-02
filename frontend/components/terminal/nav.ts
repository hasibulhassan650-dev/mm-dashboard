// Shared nav config — maps the 12 sections to routes + terminal icons.
export interface NavItem { href: string; label: string; icon: string; }

export const NAV: NavItem[] = [
  { href: "/",            label: "Overview",    icon: "grid" },
  { href: "/cashflows",   label: "Cash Flows",  icon: "flow" },
  { href: "/omo",         label: "OMO",         icon: "layers" },
  { href: "/yields",      label: "Yields",      icon: "curve" },
  { href: "/callmoney",   label: "Call Money",  icon: "pulse" },
  { href: "/fx",          label: "FX Auctions", icon: "swap" },
  { href: "/refrate",     label: "Ref Rates",   icon: "ruler" },
  { href: "/macro",       label: "External",    icon: "globe" },
  { href: "/monetary",    label: "Monetary",    icon: "bank" },
  { href: "/securities",  label: "Securities",  icon: "doc" },
  { href: "/portfolio",   label: "Portfolio",   icon: "briefcase" },
  { href: "/glossary",    label: "Glossary",    icon: "book" },
];

export function navByPath(path: string): NavItem {
  // exact match first, then longest prefix (so /drilldown etc. fall back sanely)
  return NAV.find((n) => n.href === path) || NAV[0];
}
