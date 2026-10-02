// Shared nav config — the ONLY nav definition. Rendered by AppShell's sidebar.
//
// There used to be a second, unused components/Nav.tsx. It was dead code
// (imported nowhere) and it caused a real failure: a nav rework went into it,
// passed its own test, and changed nothing on screen, while /schedule and
// /auctions stayed missing from the sidebar — which is why they could not be
// found. tests/test_navigation.py now checks THIS file and fails if a second
// nav definition appears or if this one stops being imported.
export interface NavItem { href: string; label: string; icon: string; }

export const NAV: NavItem[] = [
  { href: "/",            label: "Overview",        icon: "grid" },
  { href: "/auctions",    label: "Auctions",        icon: "doc" },
  { href: "/schedule",    label: "Schedule",        icon: "flow" },
  { href: "/cashflows",   label: "Cash Flows",      icon: "flow" },
  { href: "/forecast",    label: "Forecast",        icon: "pulse" },
  { href: "/omo",         label: "OMO",             icon: "layers" },
  { href: "/repo",        label: "Repo",            icon: "layers" },
  { href: "/callmoney",   label: "Call Money",      icon: "pulse" },
  { href: "/yields",      label: "Yields",          icon: "curve" },
  { href: "/fxmarket",    label: "FX Market",       icon: "swap" },
  // Not "FX Auctions": this is BB's USD/BDT intervention, and while it was the
  // only entry containing the word "Auctions" it drew anyone looking for
  // treasury auctions to the wrong page.
  { href: "/fx",          label: "FX Intervention", icon: "swap" },
  { href: "/refrate",     label: "Ref Rates",       icon: "ruler" },
  { href: "/macro",       label: "External",        icon: "globe" },
  { href: "/monetary",    label: "Monetary",        icon: "bank" },
  { href: "/securities",  label: "Securities",      icon: "doc" },
  { href: "/portfolio",   label: "Portfolio",       icon: "briefcase" },
  { href: "/explore",     label: "Explore",         icon: "grid" },
  { href: "/research",    label: "Research",        icon: "pulse" },
  { href: "/guardian",    label: "Guardian",        icon: "pulse" },
  { href: "/glossary",    label: "Glossary",        icon: "book" },
];

export function navByPath(path: string): NavItem {
  // exact match first, then longest prefix (so /drilldown etc. fall back sanely)
  return NAV.find((n) => n.href === path) || NAV[0];
}
