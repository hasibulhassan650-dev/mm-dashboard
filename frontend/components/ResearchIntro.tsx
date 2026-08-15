"use client";
import { Term, Say, useExplain } from "@/components/Explain";

/**
 * The "Start Here" narrative, written twice.
 *
 * Not a simplified copy with words removed — the simple register re-explains
 * each idea from scratch using everyday comparisons, because the desk version
 * assumes you already know what a benchmark, a basis point and a confidence
 * interval are. Every jargon word in the desk version is a hoverable Term.
 */
export default function ResearchIntro() {
  const { mode } = useExplain();
  const li: React.CSSProperties = { marginBottom: 11 };
  const b = (s: string) => <b style={{ color: "var(--fg)" }}>{s}</b>;

  return (
    <div style={{ fontSize: 13, lineHeight: 1.85, color: "var(--fg-mute)", maxWidth: 980 }}>
      <p style={{ marginBottom: 12 }}>
        <Say
          desk={<>{b("The question this page answers:")} what rate will the next auction clear
            at, and how much should you trust that number?</>}
          simple={<>{b("What this page is for:")} the government borrows money by holding an
            auction, and an interest rate comes out of it. This page tries to guess that rate
            before it happens — and, just as importantly, tells you how much to trust the guess.</>}
        />
      </p>

      <ol style={{ paddingLeft: 20, margin: 0 }}>
        <li style={li}>
          <Say
            desk={<>{b("The thing to beat is “same as last time”.")} Guessing that the next{" "}
              <Term t="Cutoff Yield" /> equals the last one is surprisingly hard to improve on,
              because rates only move when genuinely new information arrives. That guess is the{" "}
              <Term t="Naive Benchmark" />. Every other model must prove it beats it.</>}
            simple={<>{b("The guess to beat is “the same as last time”.")} Imagine guessing
              tomorrow&apos;s temperature. Saying &quot;same as today&quot; is a boring guess, but
              it&apos;s right a lot. Interest rates are like that. So any clever idea has to prove
              it beats the boring guess — otherwise it isn&apos;t worth using.</>}
          />
        </li>

        <li style={li}>
          <Say
            desk={<>{b("On BONDS, something genuinely works.")} Bonds auction ~monthly, bills
              weekly — so by the time a bond is bid, the bill market has already moved, and the
              previous bond print is stale. <Term t="Curve Carry" /> follows that move and roughly
              halves the error on 2Y through 20Y. This is statistically{" "}
              <span style={{ color: "var(--pos)" }}>established</span>, not a hunch.</>}
            simple={<>{b("For long loans, we found something that really works.")} Long loans are
              auctioned about once a month. Short loans are auctioned every single week. So the
              weekly ones are like a fresh weather report — they tell you what&apos;s been happening
              recently. Using them to guess the monthly ones cuts our mistakes roughly in half.
              We checked carefully, and this one is <span style={{ color: "var(--pos)" }}>real</span>.</>}
          />
        </li>

        <li style={li}>
          <Say
            desk={<>{b("On BILLS, nothing beats it.")} Some models look better by 1–2{" "}
              <Term t="Basis Point">bps</Term>, but the <Term t="Confidence Interval" /> for that
              improvement includes zero — it could be luck. So for 91D / 182D / 364D: bid off the
              last cutoff and treat any model tilt as a mild lean.</>}
            simple={<>{b("For short loans, nothing beats the boring guess.")} A few ideas looked
              slightly better — but only by a hair. When we tested whether that hair could just be
              luck, it could. So for the short ones, honestly: just use last week&apos;s rate.</>}
          />
        </li>

        <li style={li}>
          <Say
            desk={<>{b("Bonds are judged on more history than bills.")} Bills auction weekly, so 3
              years gives 150+ examples. Bonds auction monthly — 3 years is only ~20, too few to
              separate a real edge from luck, so they use a 5-year{" "}
              <Term t="Lookback Window" />. The band still uses recent data either way.</>}
            simple={<>{b("We look further back for long loans.")} Short loans happen weekly, so a
              few years gives hundreds of examples. Long loans happen monthly, so the same few years
              gives only about twenty — not enough to tell skill from luck. So for those we look
              back further to gather more examples.</>}
          />
        </li>

        <li style={li}>
          <Say
            desk={<>{b("The market got choppier — use the RECENT error.")} On 91D the typical miss
              was ~5 bps early and ~14 bps recently. A lifetime average flatters the present, so
              tables show <b>early → recent</b> and the headline uses the recent figure.</>}
            simple={<>{b("Things got bumpier lately, so use the recent score.")} Earlier we were
              usually off by a small amount; recently we&apos;re off by about three times more.
              Nothing broke — the market just got more jumpy. Averaging the calm past with the
              jumpy present would make us look better than we are.</>}
          />
        </li>

        <li>
          <Say
            desk={<>{b("Read the range, not just the number.")} Every forecast carries a band built
              from how wrong the model has <i>recently</i> been. <Term t="Coverage" /> checks it:
              real outcomes land inside about 90–95% of the time, as advertised. A wide band is the
              model saying it does not know.</>}
            simple={<>{b("Look at the range, not just the single number.")} Every guess comes with
              a &quot;probably between here and here&quot;. We checked that reality really does land
              inside that range about 9 times out of 10. When the range is very wide, that&apos;s the
              model admitting it isn&apos;t sure — which is useful to know.</>}
          />
        </li>
      </ol>

      <p style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border-soft)" }}>
        <Say
          desk={<>{b("One-line summary:")} trust the bond forecasts, bid the bills off the last
            print, and size off the recent error and the band — not the headline average.</>}
          simple={<>{b("In one sentence:")} trust the guesses for the long loans, just use last
            week&apos;s rate for the short ones, and always look at the range before deciding how
            much money to put behind it.</>}
        />
      </p>

      {mode === "desk" && (
        <p style={{ marginTop: 10, fontSize: 12, color: "var(--fg-mute)" }}>
          Every underlined term is explained on hover, and links to the full{" "}
          <a href="/glossary" style={{ color: "var(--accent)" }}>glossary</a>. Switch to{" "}
          <b>Explain simply</b> above for a no-jargon version of this whole page.
        </p>
      )}
    </div>
  );
}
