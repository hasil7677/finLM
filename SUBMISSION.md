# finLM — a governance layer for financial agents

**Theme: Governance Layer for Financial Agents**

**Everyone is shipping financial AI agents. Nobody is shipping the thing that makes them safe to deploy. finLM is that layer — enforced mandates, a kill switch, a reasoning audit trail, and automated outcome scoring — and we proved it by running a real financial agent inside it against live markets.**

## The problem

Financial agent "safety" today is theatre. Zerodha's official trading MCP server — the reference implementation for LLM-driven trading in India — gates order placement behind a `confirmed=true` parameter that the model sets on itself. That is not a control; it's a suggestion the agent can approve on your behalf. Across the ecosystem the pattern repeats: guardrails live in prompts, where they get ignored under pressure, rather than in code the model cannot reach.

And when something does go wrong, nobody can answer the questions a risk officer actually asks. What did the agent decide? What was its reasoning *at the time*, not reconstructed afterwards? Was it right? Financial agents today are unauditable by construction — they leave behind chat logs, not records.

## The solution: four governance primitives

finLM is an MCP server that sits between any LLM and a financial system of record. The LLM gets rich capability; the governance layer decides what actually happens. Four primitives, all enforced server-side:

**1. Mandate enforcement.** Every consequential action is validated against a policy file the *user* writes, outside the conversation — value caps, quantity caps, daily action limits, instrument allowlists and blocklists, permitted action types. Crucially it **fails closed**: if no mandate exists, nothing executes. There is no parameter, phrasing, or claimed authority that lets the model bypass it, because the check runs in code the model can only call, never modify.

**2. Kill switch.** A filesystem circuit breaker. Drop a `KILL_SWITCH` file and every action is refused instantly, no restart, no config reload, no cooperation from the agent required. Removing it restores service. This is the control a human needs when an agent is misbehaving *right now*.

**3. Reasoning audit trail.** Every decision is journaled at the moment it's made, with the agent's full thesis captured verbatim — not a summary written later. Each record carries the decision, the parameters, the reasoning, and a timestamp. This is the artifact that makes an agent reviewable: you can reconstruct not just what it did, but why it thought so.

**4. Automated outcome scoring.** The layer closes the loop by grading its own past decisions against ground truth and reporting hit rates over time. Logging tells you what happened; scoring tells you whether the agent is any good. Deployed agents drift, and without an automatic scorer nobody notices until it's expensive.

Supporting these is **pre-deployment policy validation**: a point-in-time simulator that replays the agent's decision policy over historical data with strict no-look-ahead rules, train/test splits, and benchmark adjustment — so a policy is evidenced *before* it touches production, and a favourable environment can't be mistaken for competence.

## Proof: we ran a real agent inside it

Governance layers are easy to describe and hard to trust, so we built a genuinely capable financial agent and governed it. The domain is Indian equities (NSE), chosen deliberately: markets return unambiguous ground truth on a fixed schedule, so every claim the agent makes gets graded by reality within days — no human labelling, no vibes.

The agent screens ~2,700 stocks daily from free public exchange data, runs two independent quantitative models that must each state their conviction and reasoning, researches the real-world catalyst behind each move via web search, and proposes trades with explicit entry, stop and target. Everything it proposes flows through the four primitives above.

What the governance layer surfaced:

- **Pre-deployment validation caught a losing policy before any money moved.** Across 2,758 historical decision events, the intuitive strategy — chase what's already moving — lost in *every* configuration tested: 37% win rate, roughly -1.3% benchmark-adjusted return per trade. An ungoverned agent would have shipped this; it looks compelling in a demo.
- **It validated the counterintuitive one — across eleven years.** The opposite policy (fade the move) held **+2.32% benchmark-adjusted return per trade, net of transaction costs, over 8,405 trades spanning 2010-2020 — positive in every one of the eleven years** (one-sided sign test, p = 0.0005). The historical universe is per-day NSE bhavcopy, so companies that delisted or collapsed mid-sample are still in it: this is a point-in-time test, not a survivorship artifact. On the most recent 14 months the same configuration is marginal (+0.58% per trade net, profit factor 1.02) — an edge demonstrated across a decade and thinning in the current regime, which we report rather than average away. The edge is short-side, so for a cash account this is a de-risking filter, not a return claim.
- **The audit trail earned its keep.** Five decisions were journaled on 21 July with their reasoning, and automatically scored on 24 July against real NSE closing prices. **These are paper decisions — no order was placed and no capital was at risk** (see the disclosure below). The highest-conviction call returned **+10.31%** on paper against a market that fell 0.95%. The three "take no action" calls all avoided 6-10% losses — and because "do nothing" was recorded as a decision with reasoning, its value is measurable rather than invisible.
- **Most importantly, it caught itself being wrong.** The agent had recorded a hypothesis — that moves backed by genuine news catalysts behave differently — and used it to justify two of those inactions. The outcome data contradicted it. That reversal exists as a dated, reviewable record instead of a forgotten assumption, which is precisely what governance is *for*.

*Evidence changelog — 2026-08-09.* The second bullet previously read "+1.2% alpha per trade across 935 trades, PF 1.18, positive in 10 of 12 months, improved out-of-sample 1.14 → 1.27." Those figures were re-run and **did not reproduce**; 18 parameter combinations were tested and none matched. The current 14-month window gives +0.58% net (PF 1.02), and monthly consistency is 8 of 12 rather than 10 of 12. The eleven-year result quoted above was verified as already net-of-cost and is now the headline. The superseded figures and the full diagnosis are kept in the README rather than deleted — the same discipline the fourth bullet describes, applied to our own numbers.

## Why this generalises beyond trading

The four primitives are domain-agnostic; only two things change per use case: the action schema and the outcome scorer. The same layer governs a dispute-resolution agent (mandate: refund ceilings, per-day counts, eligible transaction types; scorer: was the chargeback upheld?), a card-benefit activation agent (mandate: which benefits, which cardholder segments; scorer: was the benefit actually used?), or any servicing agent taking consequential action on a customer account.

Trading was the proving ground because the feedback loop is fast, quantitative and honest. The deliverable is the layer.

## Tech

Python, FastMCP (15 tools over the Model Context Protocol), SQLite for the journal and market store, pandas for deterministic analytics, Tavily for catalyst research, Zerodha Kite Connect for the live financial system. Model-agnostic: any MCP client works — verified across desktop and CLI MCP clients. Runs with **zero credentials** on free public data; API keys only unlock the live tier.

## Status

Working end-to-end and verified over the protocol. 14 months of market history ingested, simulator reproducible from the repo, journal actively scoring live decisions, mandate and kill switch enforced and tested (they fail closed).

**Next:** benchmark-adjusted journal analytics with catalyst tagging to properly test the hypothesis above, plus per-decision attribution reporting for reviewers.

---

## Short-form fields (for submission forms)

**Theme:** Governance Layer for Financial Agents

**Tagline:** finLM — the governance layer financial agents are missing: enforced mandates, a kill switch, a reasoning audit trail, and automated scoring. Proven by running a live-market agent inside it.

**Problem:** Financial agent safety today lives in prompts, not code. The reference LLM trading server in India gates real orders behind a flag the model sets on itself. And when an agent errs, nobody can answer what it decided, why it thought so, or whether it was right — these systems leave chat logs, not records.

**Solution:** An MCP layer between any LLM and a financial system of record, enforcing four primitives server-side: (1) mandate validation against a user-written policy that fails closed and cannot be bypassed by the model; (2) a filesystem kill switch that freezes all action instantly; (3) a reasoning audit trail capturing each decision's thesis verbatim at decision time; (4) automated outcome scoring against ground truth, so agent quality is measured, not assumed. Plus a point-in-time simulator that validates a policy before deployment.

**Proof:** We governed an NSE market-analysis agent end to end. Pre-deployment validation killed the intuitive strategy (-1.3% per trade across 2,758 events) and validated the counterintuitive one (+2.32% alpha per trade net of costs across 8,405 trades, positive in all 11 years tested, p = 0.0005). Its top paper call returned +10.31% against a falling market, and the scorer caught a recorded hypothesis being wrong. **No order was ever placed: the mandate file that would authorise one was deliberately never created, so the gate refused every time — which is the design working, not a gap.**

**One-box version (150 words):** Everyone is shipping financial AI agents; nobody is shipping what makes them safe to deploy. Today's "safety" is prompt-level — the reference LLM trading server in India gates real money behind a flag the model sets itself — and when agents err, they leave chat logs instead of auditable records. finLM is an MCP governance layer enforcing four primitives in code the model cannot reach: mandates that fail closed, an instant filesystem kill switch, a reasoning audit trail capturing each decision's thesis as it's made, and automated outcome scoring against ground truth. A point-in-time simulator validates policies before deployment. We proved it by governing a real market agent: validation killed the intuitive strategy (-1.3% per trade over 2,758 events), confirmed the counterintuitive one (+2.32% net alpha across 8,405 trades, positive in all eleven years tested), and its top paper call returned +10.31% — while catching one of its own hypotheses being wrong. No capital was ever at risk: the mandate authorising an order was never written, so the gate refused every one. Swap the action schema and scorer, and the same layer governs disputes, benefits, or servicing.
