"use client";
import { Term } from "@/components/Explain";

/**
 * The "Start Here" narrative.
 *
 * Written in the desk's own language — the technical terms stay, because this
 * is a working page for someone who trades these auctions. Every one of them is
 * clickable and opens a full explanation of the concept, so the depth lives one
 * click away instead of being spread through the prose.
 */
export default function ResearchIntro() {
  const li: React.CSSProperties = { marginBottom: 11 };
  const b = (s: string) => <b style={{ color: "var(--fg)" }}>{s}</b>;

  return (
    <div style={{ fontSize: 13, lineHeight: 1.85, color: "var(--fg-mute)", maxWidth: 980 }}>
      <p style={{ marginBottom: 12 }}>
        {b("The question this page answers:")} what rate will the next auction clear at, and how
        much should you trust that number?
      </p>

      <ol style={{ paddingLeft: 20, margin: 0 }}>
        <li style={li}>
          {b("The thing to beat is “same as last time”.")} Guessing that the next{" "}
          <Term t="Cutoff yield">cutoff yield</Term> equals the last one is surprisingly hard to
          improve on, because rates only move when genuinely new information arrives. That guess is
          the <Term t="Naive Benchmark" />, and every other model must prove it beats it.
        </li>

        <li style={li}>
          {b("On BONDS, something genuinely works.")} Bonds auction ~monthly, bills weekly — so by
          the time a bond is bid, the bill market has already moved and the previous bond print is
          stale. <Term t="Curve Carry" /> follows that move, with a fitted{" "}
          <Term t="Pass-through">pass-through (λ)</Term>, and roughly halves the{" "}
          <Term t="MAE">average error</Term> on 2Y through 20Y. Statistically{" "}
          <span style={{ color: "var(--pos)" }}>established</span>, not a hunch.
        </li>

        <li style={li}>
          {b("On BILLS, nothing beats it.")} Some models look better by 1–2{" "}
          <Term t="Basis Point">bps</Term>, but the <Term t="Confidence Interval" /> for that
          improvement includes zero — it could be luck. So for 91D / 182D / 364D: bid off the last
          cutoff and treat any model tilt as a mild lean.
        </li>

        <li style={li}>
          {b("Bonds are judged on more history than bills.")} Bills auction weekly, so 3 years gives
          150+ examples. Bonds auction monthly — 3 years is only ~20, too few to separate a real edge
          from luck — so they use a 5-year <Term t="Lookback Window" />. The band still uses recent
          data either way.
        </li>

        <li style={li}>
          {b("The market got choppier — use the RECENT error.")} On 91D the typical miss was ~5 bps
          early and ~14 bps recently. A lifetime average flatters the present, so the tables show{" "}
          <b>early → recent</b> and the headline uses the recent figure.
        </li>

        <li>
          {b("Read the range, not just the number.")} Every forecast carries a band built from{" "}
          <Term t="RMSE">how wrong the model has recently been</Term>.{" "}
          <Term t="Coverage" /> confirms it: real outcomes land inside about 90–95% of the time, as
          advertised. A wide band is the model saying it does not know.
        </li>
      </ol>

      <p style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border-soft)" }}>
        {b("One-line summary:")} trust the bond forecasts, bid the bills off the last print, and
        size off the recent error and the band — not the headline average.
      </p>

      <p style={{ marginTop: 10, fontSize: 12, color: "var(--fg-mute)" }}>
        <b style={{ color: "var(--accent)" }}>Every underlined term is clickable.</b> Hover for a
        one-line definition; click to open the full concept — the intuition, how it works on a
        worked example with these actual numbers, how to read it here, and the mistake people
        usually make. No prior statistics assumed.
      </p>
    </div>
  );
}
