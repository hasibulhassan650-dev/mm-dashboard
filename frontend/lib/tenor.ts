/** Tenor labels come from BB's auction history ("91D", "14D", "10Y", "3Y_FRTB"). Nothing here lists the
 *  tenors that exist: a tenor BB introduces or revives has to work without anyone editing a list. */

// Bills are quoted by days but priced on a year-fraction; these are the conventional ones.
const BILL_YEARS: Record<string, number> = { "91D": 0.25, "182D": 0.5, "364D": 1 };

/** Tenor length in years, from the label alone. null for a label that is not a tenor. */
export function tenorYears(label: string): number | null {
  if (label in BILL_YEARS) return BILL_YEARS[label];
  const m = /^(\d+)(D|Y)(?:_|$)/.exec(label);
  if (!m) return null;
  return m[2] === "D" ? +m[1] / 365 : +m[1];
}

/** Shortest first; a label that is not a tenor sorts last. */
export function byTenor(a: string, b: string): number {
  return (tenorYears(a) ?? 1e9) - (tenorYears(b) ?? 1e9) || a.localeCompare(b);
}

/** "14D" -> "14D T-Bill", "10Y" -> "10Y T-Bond", "3Y_FRTB" -> "3Y FRTB". */
export function tenorTitle(label: string, securityType: string): string {
  const base = label.replace("_FRTB", "");
  return securityType === "FRTB" ? `${base} FRTB` : securityType === "T_BILL" ? `${base} T-Bill` : `${base} T-Bond`;
}
