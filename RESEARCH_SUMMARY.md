# Bitcoin Electrical Cost — Research Summary

**One-line answer:** The claim *"Bitcoin NEVER trades below its electrical cost"* is **false**. It has happened — rarely, briefly, and those dips were historically good long-term buying zones. The “floor” is also very sensitive to assumptions (especially electricity price).

Full interactive charts and tables: [`report.html`](report.html).

---

## What was the claim?

Some Bitcoin analysts (notably Charles Edwards / Capriole) track an **electrical cost of production**: roughly what miners spend on electricity to create one new BTC.

The strong version of the story is:

> Bitcoin **never** trades below that cost line — it’s a hard floor.

We treated that as a **falsification test**: if we find even one solid day where the market traded below the cost line, “never” is wrong. Then we checked the weaker, more useful version:

> BTC *rarely* trades below electrical cost, only briefly, and those episodes were good long-term entries.

---

## How the “cost” is estimated (in plain English)

Miners use electricity to run computers that secure the Bitcoin network. Roughly:

```
daily electricity bill for the whole network
────────────────────────────────────────────  =  cost per BTC
     how many BTC miners earned that day
```

We built three versions:

| Model | Idea | Independent of Cambridge energy data? |
|--------|------|----------------------------------------|
| **A** | Cambridge energy estimate (CBECI) × $0.05/kWh ÷ BTC mined | No — uses CBECI |
| **B** | Network hashrate × assumed miner efficiency × $0.05/kWh | Yes — bottom-up check |
| **C** | Capriole’s own chart (only if you export it) | External |

**Why $0.05/kWh?** That’s the standard Capriole / Cambridge assumption for a global average miner power price. It’s an assumption, not a measured fact.

**Electrical cost vs production cost:** Capriole also publishes a wider “production cost” ≈ electrical cost ÷ 0.6 (because electricity is assumed to be ~60% of miner costs). That wider line is breached **much** more often. This summary focuses on **electrical** cost unless noted.

---

## Does “NEVER” hold?

### No.

Using Model A (Cambridge-style electrical cost) and a **consensus daily low** across Bitstamp / Coinbase / Kraken:

- Price traded **below** electrical cost on **14 days** (~0.25% of history)
- First clear breach: **24 Nov 2018**
- So the literal word “never” is falsified

Model B (our independent bottom-up estimate) barely breaches at all on the median low — so the answer depends on the model. But Model A is the one closest to the public Capriole/Cambridge claim, and **it fails “never.”**

---

## When did it fail? (the episodes)

Only **four** short episodes show up for Model A electrical cost:

| When | How long | How far below cost | ~1 year later |
|------|----------|--------------------|---------------|
| Nov 2018 (bear market) | 3 days | ~4% below | ~+83% |
| Mar 2020 (COVID crash) | 8 days | ~32% below | ~+1,080% |
| May 2020 | 2 days | ~1% below | ~+312% |
| May 2020 (again) | 1 day | ~2% below | ~+346% |

**Takeaway:** Breaches are rare and short. In this small sample, buying near those dips looked excellent a year later — but there are only ~4 independent episodes, so this is a hint, not a proven trading rule.

### What about famous crashes people mention?

| Event | Electrical cost (Model A, today’s revised data) | Production cost (electrical ÷ 0.6) |
|-------|--------------------------------------------------|-------------------------------------|
| Dec 2018 | Briefly below | Below |
| Mar 2020 COVID | Clearly below | Below |
| Nov 2022 FTX | **Not below** electrical (~$14K cost vs ~$15–17K price) | **Below** for most of the month |

That FTX result matters: Cambridge revised its energy history downward in 2023. With **today’s** revised numbers, Nov 2022 doesn’t break the electrical line. With Capriole’s wider production cost, it does. So some “breaches” people remember depend on **which line** and **which vintage of data** you use.

---

## How sensitive is the answer?

The cost line moves a lot if you change the assumed power price:

| Assumed $/kWh | Days below Model A cost | Does “never” survive? |
|---------------|-------------------------|------------------------|
| $0.03 | 0 | Yes |
| $0.04 | 2 | No |
| **$0.05** (standard) | **14** | **No** |
| $0.06 | 122 | No |
| $0.07 | 582 | No |
| $0.08 | 1,422 (~25% of days) | No |

**Plain English:** At cheap power assumptions, the floor almost never breaks. At expensive power assumptions, Bitcoin spends a large share of history “below cost.” The floor is only as strong as the $/kWh guess.

(Rule of thumb from industry commentary: about **$4,000+ of cost per 1¢/kWh** change — small input changes move the line by thousands of dollars.)

---

## Where are we now? (as of mid-Nov 2026)

| Metric | Approx. value |
|--------|----------------|
| Bitcoin price (consensus) | ~$65,000 |
| Model A electrical cost | ~$49,000 |
| Model A (7-day smooth) | ~$50,000 |
| Model B bottom-up | ~$37,000 |
| Capriole-style **production** cost | ~$81,000 |

So today:

- Price is **above** electrical cost (~25–30% cushion vs Model A)
- Price is **below** the wider production-cost line
- The circulating claim of a **$55K** current electrical cost is **not** what Model A prints at $0.05/kWh (~$49K). You’d need about **$0.056/kWh** to force Model A up to $55K

---

## The weaker claim — is it useful for investing?

**“BTC rarely goes below electrical cost, and when it does it’s brief and often a good long-term entry.”**

That version fits the data much better than “never”:

1. **Rare** — under 1% of days for Model A at $0.05/kWh  
2. **Brief** — episodes lasted days, not months (for electrical cost)  
3. **Historically strong forward returns** in the few episodes we have — especially March 2020  

But:

- Sample size is tiny (~4 episodes)
- Past luck ≠ future guarantee
- The cost line **moves with the market** (see caveats below)

Treat it as a **stress / capitulation flag**, not a mechanical “buy when below cost” system.

---

## Best buy zone: how far above cost?

We also asked a more practical question:

> If I’m not waiting for a rare dip *below* cost, what **percentage above electrical cost** has historically been the best place to buy?

### Method (simple version)

1. Each day: `pct_above = (price / electrical_cost − 1) × 100`
2. Group into bands: at/below, 0–10%, 10–20%, 20–30%, 30–50%, 50–100%, >100%
3. Take **one entry per contiguous stay** in a band (so consecutive days don’t inflate the stats)
4. Measure 180- and 365-day forward returns vs a baseline of “buy every year”

### Recommended range

| Intent | Range vs Model A electrical cost |
|--------|-----------------------------------|
| **Best historical edge** | **0–20% above** (include dips below cost) |
| **Usable accumulation / stretch** | **0–30% above** |
| **Edge mostly fades** | **above ~40–50%** |

Non-overlapping 365-day results (Model A, 7-day smooth):

| Zone | Episodes | Median 1y return | vs baseline |
|------|----------|------------------|-------------|
| At / below (≤0%) | ~2 | very high | huge, tiny *n* |
| **0–10%** | ~9 | **~285%** | strong |
| **10–20%** | ~20 | **~244%** | strong |
| 20–30% | ~45 | ~131% | still good |
| 30–50% | ~93 | ~96% | mild |
| 50%+ | many | closer to baseline | weak / noisy |

**Today:** price is roughly **~30% above** electrical cost — outer edge of the stretch zone, not deep value.

This is descriptive history, not a guarantee. Deep bands (≤0% / 0–10%) have few independent episodes. Charts and full tables live in `report.html` under **Buy-zone study**.

---

## Big caveats (read these)

1. **The floor moves toward price**  
   After a crash, some miners shut off → hashrate/difficulty fall → estimated cost often falls too. So the “support” partly follows price down. It’s not an independent concrete slab.

2. **We don’t really know global $/kWh or average miner efficiency**  
   Those are guesses. Change them and the story changes (see sensitivity table).

3. **Look-ahead bias**  
   Cambridge revised history in 2023. Capriole free charts can lag by weeks. The line on a chart today may not be what traders saw in real time.

4. **Exchange wicks**  
   One exchange can print a crazy low that others don’t. We tested both the **lowest low anywhere** and the **median low** across venues.

5. **Small sample**  
   A handful of episodes is not enough for strong statistical claims.

---

## Bottom line

| Question | Answer |
|----------|--------|
| Does Bitcoin **never** trade below electrical cost? | **No.** It has, a few times. |
| Does it happen often? | **No.** Rare and usually short (for electrical cost at $0.05/kWh). |
| Were those dips good long-term entries historically? | **In this sample, yes** — especially COVID — but *n* is tiny. |
| Best average buy zone above cost? | **About 0–20% above** electrical cost (stretch to ~30%). |
| Is ~$55K the current electrical cost? | **Not under the standard $0.05/kWh Model A** (~$49K). |
| Is this a hard law of Bitcoin? | **No.** It’s an economic estimate that moves with assumptions and with miner behavior. |

---

## How this study was run (short)

- **Price:** daily candles from Bitstamp, Coinbase, Kraken → consensus bar  
- **Energy:** Cambridge CBECI (min / guess / max)  
- **Network:** Coin Metrics free API (hashrate, issuance, fees, blocks)  
- **Code:** reproducible Python project in this folder — `python -m src.pipeline`  
- **Tests:** `pytest` (halving schedule, unit conversions, Model A sanity band)

To refresh everything:

```bash
cd btc-electrical-cost
python -m src.pipeline
```

Optional: export Capriole’s TradingView series to `data/manual/capriole.csv` to add Model C.
