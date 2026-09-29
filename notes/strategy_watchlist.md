# Strategy watchlist

New strategy ideas from outside sources (YouTube, blogs, papers, other
traders) go here before any code is written, so "keeping up to date" doesn't
mean chasing every viral claim. For each one, write down the claim, the
source, and which parts are mechanical vs. left to interpretation (the
interpretive parts are where most viral strategies hide their edge, or
their lack of one).

Pipeline for an idea that looks worth testing:
1. Pin every discretionary rule to an explicit, sweepable parameter and
   implement it as a `Strategy` (lifecycle stage: `research`).
2. Run the **Research Report** workflow (GitHub → Actions → Research Report →
   Run workflow). The result lands in Telegram as a `RESEARCH_REPORT`;
   paste it back to Claude.
3. Judge it on the **test** line against the baseline, not the train line. A
   good train result with a bad test result is the overfitting pattern this
   project has already hit once (donchian exit widths, spec Section 8).
4. Log the verdict below and in the spec (Section 7), including negative
   results: a tested "doesn't work" is still worth keeping.

| Idea | Source | Status | Verdict |
|---|---|---|---|
| Candle Range Theory (CRT): sweep of prior candle's high/low, close back inside, target the opposite end | Trading Geek (YouTube), "I Tested The Trading Geek Strategy…" | implemented (`crt`), awaiting first research report | — |
