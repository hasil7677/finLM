const pptxgen = require("pptxgenjs");

const NAVY = "0E1B33";        // dominant dark
const NAVY_CARD = "1C2E52";   // card on dark
const INK = "16223B";         // dark text on light
const SLATE = "5A6B87";       // muted body on light
const ICE = "AFC3E0";         // muted body on dark
const MINT = "2ED8A7";        // accent: allowed / validated
const CORAL = "FF6B6B";       // accent: blocked / refused
const LIGHT = "FFFFFF";
const CARD = "F1F5FA";        // card on light

const H = "Cambria";
const B = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";  // 13.333 x 7.5
pres.author = "Anish Naik";
pres.title = "finLM - Governance Layer for Financial Agents";

const W = 13.333, HT = 7.5, M = 0.7;

// ---------- helpers ----------
function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: NAVY };
  return s;
}
function lightSlide() {
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  return s;
}
function title(s, text, dark, opts = {}) {
  s.addText(text, {
    x: M, y: opts.y || 0.5, w: W - 2 * M, h: 0.85,
    fontFace: H, fontSize: opts.size || 34, bold: true,
    color: dark ? LIGHT : INK, align: "left", margin: 0,
  });
}
function kicker(s, text, dark) {
  s.addText(text, {
    x: M, y: 0.28, w: W - 2 * M, h: 0.3,
    fontFace: B, fontSize: 12, bold: true, charSpacing: 2,
    color: MINT, align: "left", margin: 0,
  });
}

// ============================================================
// 1. TITLE
// ============================================================
{
  const s = darkSlide();
  s.addShape(pres.ShapeType.ellipse, {
    x: 9.4, y: -1.6, w: 6.2, h: 6.2, fill: { color: NAVY_CARD },
  });
  s.addShape(pres.ShapeType.ellipse, {
    x: 10.9, y: 3.9, w: 3.4, h: 3.4, fill: { color: "16274A" },
  });

  s.addText("CODESTREET 2026  ·  AMERICAN EXPRESS", {
    x: M, y: 1.15, w: 8.5, h: 0.3, fontFace: B, fontSize: 12,
    bold: true, charSpacing: 2, color: MINT, margin: 0,
  });
  s.addText("finLM", {
    x: M, y: 1.6, w: 8.5, h: 1.7, fontFace: H, fontSize: 84,
    bold: true, color: LIGHT, margin: 0,
  });
  s.addText("A governance layer for financial agents", {
    x: M, y: 3.35, w: 8.6, h: 0.6, fontFace: H, fontSize: 28,
    color: ICE, margin: 0,
  });
  s.addText(
    "Enforced mandates · kill switch · reasoning audit trail · automated outcome scoring.\nProven by governing a real financial agent against live markets.",
    { x: M, y: 4.15, w: 8.6, h: 1.0, fontFace: B, fontSize: 15,
      color: ICE, lineSpacing: 24, margin: 0 }
  );
  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 5.55, w: 5.3, h: 0.62, rectRadius: 0.31,
    fill: { color: NAVY_CARD },
  });
  s.addText("Theme: Governance Layer for Financial Agents", {
    x: M, y: 5.55, w: 5.3, h: 0.62, fontFace: B, fontSize: 13,
    bold: true, color: LIGHT, align: "center", valign: "middle", margin: 0,
  });
  s.addText("Anish Naik  ·  Idea Submission", {
    x: M, y: 6.45, w: 6, h: 0.3, fontFace: B, fontSize: 12,
    color: "7E93B8", margin: 0,
  });
  s.addNotes("finLM is a governance layer for financial agents. Everyone is shipping agents that can move money. Nobody is shipping the layer that makes them safe to deploy and auditable afterwards. We built that layer, and we proved it by putting a real financial agent inside it.");
}

// ============================================================
// 2. PROBLEM
// ============================================================
{
  const s = darkSlide();
  kicker(s, "THE PROBLEM", true);
  title(s, "Agent safety today is a flag the model sets on itself", true);

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 1.85, w: 6.1, h: 2.1, rectRadius: 0.12, fill: { color: NAVY_CARD },
  });
  s.addText("Zerodha's official LLM trading server", {
    x: M + 0.35, y: 2.05, w: 5.4, h: 0.3, fontFace: B, fontSize: 12,
    bold: true, color: ICE, margin: 0,
  });
  s.addText("place_order(  confirmed = true  )", {
    x: M + 0.35, y: 2.45, w: 5.4, h: 0.5, fontFace: "Courier New", fontSize: 19,
    bold: true, color: CORAL, margin: 0,
  });
  s.addText(
    "The reference implementation for LLM-driven trading in India gates real order placement behind a parameter the model passes to itself.",
    { x: M + 0.35, y: 3.05, w: 5.4, h: 0.8, fontFace: B, fontSize: 13,
      color: ICE, lineSpacing: 19, margin: 0 }
  );

  s.addText("That is not a control.\nIt is a suggestion the agent approves\non your behalf.", {
    x: 7.35, y: 2.0, w: 5.3, h: 1.6, fontFace: H, fontSize: 25, bold: true,
    color: LIGHT, lineSpacing: 34, margin: 0,
  });

  const rows = [
    ["Guardrails live in prompts", "and prompts get ignored under pressure or adversarial input."],
    ["No record of intent", "agents leave chat logs, not decision records with reasoning."],
    ["No measure of quality", "nobody scores whether the agent's judgment was actually right."],
  ];
  rows.forEach((r, i) => {
    const y = 4.35 + i * 0.82;
    s.addShape(pres.ShapeType.ellipse, {
      x: M, y: y + 0.06, w: 0.34, h: 0.34, fill: { color: CORAL },
    });
    s.addText("×", {
      x: M, y: y + 0.06, w: 0.34, h: 0.34, fontFace: B, fontSize: 17, bold: true,
      color: NAVY, align: "center", valign: "middle", margin: 0,
    });
    s.addText(
      [{ text: r[0] + " — ", options: { bold: true, color: LIGHT } },
       { text: r[1], options: { color: ICE } }],
      { x: M + 0.55, y: y, w: 11.3, h: 0.5, fontFace: B, fontSize: 14,
        valign: "middle", margin: 0 }
    );
  });
  s.addNotes("Financial agent safety today is theatre. This is the reference LLM trading server in India, and its safety mechanism is a boolean the model sets itself. The pattern repeats everywhere: guardrails in prompts rather than in code the model can't reach.");
}

// ============================================================
// 3. THE AUDIT GAP
// ============================================================
{
  const s = lightSlide();
  kicker(s, "WHY IT MATTERS IN REGULATED FINANCE", false);
  title(s, "Three questions nobody can answer after an agent acts", false);

  const qs = [
    ["What did it decide?", "Actions are scattered across chat transcripts and API logs with no canonical record."],
    ["Why did it think so?", "Reasoning is reconstructed after the fact, if it survives at all — not captured at decision time."],
    ["Was it right?", "No system grades an agent's judgment against what actually happened, so drift goes unnoticed."],
  ];
  qs.forEach((q, i) => {
    const x = M + i * 4.1;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 2.05, w: 3.7, h: 3.0, rectRadius: 0.12, fill: { color: CARD },
    });
    s.addShape(pres.ShapeType.ellipse, {
      x: x + 0.35, y: 2.4, w: 0.62, h: 0.62, fill: { color: INK },
    });
    s.addText("?", {
      x: x + 0.35, y: 2.4, w: 0.62, h: 0.62, fontFace: H, fontSize: 26, bold: true,
      color: MINT, align: "center", valign: "middle", margin: 0,
    });
    s.addText(q[0], {
      x: x + 0.35, y: 3.2, w: 3.0, h: 0.45, fontFace: H, fontSize: 19, bold: true,
      color: INK, margin: 0,
    });
    s.addText(q[1], {
      x: x + 0.35, y: 3.72, w: 3.0, h: 1.15, fontFace: B, fontSize: 13,
      color: SLATE, lineSpacing: 19, margin: 0,
    });
  });

  s.addText(
    "In a regulated institution, an agent that cannot be audited cannot be deployed — no matter how good it is.",
    { x: M, y: 5.5, w: W - 2 * M, h: 0.6, fontFace: H, fontSize: 19, italic: true,
      color: INK, margin: 0 }
  );
  s.addNotes("These are the questions a risk officer, a regulator, or an incident review will ask. Today's financial agents cannot answer any of them. That is the real blocker to deploying agents in a regulated institution — not model capability.");
}

// ============================================================
// 4. THE IDEA
// ============================================================
{
  const s = lightSlide();
  kicker(s, "THE IDEA", false);
  title(s, "The LLM advises. Code decides. Reality grades.", false);

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 2.1, w: 5.8, h: 3.3, rectRadius: 0.12, fill: { color: CARD },
  });
  s.addText("GIVE THE LLM", {
    x: M + 0.4, y: 2.4, w: 5.0, h: 0.3, fontFace: B, fontSize: 12, bold: true,
    charSpacing: 2, color: SLATE, margin: 0,
  });
  s.addText("Judgment work", {
    x: M + 0.4, y: 2.75, w: 5.0, h: 0.45, fontFace: H, fontSize: 22, bold: true,
    color: INK, margin: 0,
  });
  ["Interpreting unstructured evidence and context",
   "Explaining why a situation looks the way it does",
   "Drafting a recommended action with its rationale"].forEach((t, i) => {
    s.addText(t, {
      x: M + 0.4, y: 3.35 + i * 0.6, w: 5.0, h: 0.5, fontFace: B, fontSize: 14,
      color: INK, bullet: true, margin: 0,
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 6.95, y: 2.1, w: 5.7, h: 3.3, rectRadius: 0.12, fill: { color: INK },
  });
  s.addText("NEVER LET IT TOUCH", {
    x: 7.35, y: 2.4, w: 4.9, h: 0.3, fontFace: B, fontSize: 12, bold: true,
    charSpacing: 2, color: MINT, margin: 0,
  });
  s.addText("Authority", {
    x: 7.35, y: 2.75, w: 4.9, h: 0.45, fontFace: H, fontSize: 22, bold: true,
    color: LIGHT, margin: 0,
  });
  ["Whether an action is permitted at all",
   "The limits on value, volume and frequency",
   "The record of what it decided and why"].forEach((t, i) => {
    s.addText(t, {
      x: 7.35, y: 3.35 + i * 0.6, w: 4.9, h: 0.5, fontFace: B, fontSize: 14,
      color: ICE, bullet: true, margin: 0,
    });
  });

  s.addText(
    "Capability and authority are separated by construction — the model can call the checks, never modify them.",
    { x: M, y: 5.65, w: W - 2 * M, h: 0.5, fontFace: B, fontSize: 14,
      color: SLATE, margin: 0 }
  );
  s.addNotes("One principle drives the whole design. The LLM gets the judgment work it is genuinely good at. It never gets authority. Capability and authority are separated in code, not in a prompt.");
}

// ============================================================
// 5. FOUR PRIMITIVES
// ============================================================
{
  const s = lightSlide();
  kicker(s, "THE SOLUTION", false);
  title(s, "Four governance primitives, enforced server-side", false);

  const prims = [
    ["1", "Mandate enforcement",
     "Every consequential action is validated against a policy file the user writes outside the conversation — value caps, action limits, allowlists. Fails closed: no mandate, nothing executes."],
    ["2", "Kill switch",
     "A filesystem circuit breaker. Drop the file and every action is refused instantly — no restart, no config reload, no cooperation from the agent required."],
    ["3", "Reasoning audit trail",
     "Every decision journaled the moment it is made, with the agent's full thesis captured verbatim — reviewable months later, not reconstructed."],
    ["4", "Automated outcome scoring",
     "The layer grades its own past decisions against ground truth and reports hit rates, so agent quality is measured rather than assumed."],
  ];
  prims.forEach((p, i) => {
    const x = M + (i % 2) * 6.15;
    const y = 1.95 + Math.floor(i / 2) * 2.35;
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: 5.75, h: 2.05, rectRadius: 0.12, fill: { color: CARD },
    });
    s.addShape(pres.ShapeType.ellipse, {
      x: x + 0.35, y: y + 0.32, w: 0.6, h: 0.6, fill: { color: MINT },
    });
    s.addText(p[0], {
      x: x + 0.35, y: y + 0.32, w: 0.6, h: 0.6, fontFace: H, fontSize: 24, bold: true,
      color: INK, align: "center", valign: "middle", margin: 0,
    });
    s.addText(p[1], {
      x: x + 1.15, y: y + 0.35, w: 4.3, h: 0.4, fontFace: H, fontSize: 18, bold: true,
      color: INK, margin: 0,
    });
    s.addText(p[2], {
      x: x + 1.15, y: y + 0.82, w: 4.3, h: 1.1, fontFace: B, fontSize: 12.5,
      color: SLATE, lineSpacing: 17, margin: 0,
    });
  });

  s.addText("Plus: a point-in-time simulator that validates an agent's decision policy on historical data before it ever reaches production.", {
    x: M, y: 6.5, w: W - 2 * M, h: 0.45, fontFace: B, fontSize: 13.5, italic: true,
    color: INK, margin: 0,
  });
  s.addNotes("Four primitives. Mandate enforcement that fails closed. A kill switch a human can pull without the agent's cooperation. An audit trail capturing reasoning at decision time. And automated scoring against ground truth. Plus pre-deployment validation.");
}

// ============================================================
// 6. ARCHITECTURE
// ============================================================
{
  const s = lightSlide();
  kicker(s, "HOW IT WORKS", false);
  title(s, "One layer between any LLM and the system of record", false);

  const boxes = [
    [M, "Any LLM client", "Claude · GPT · Gemini\nvia Model Context Protocol", CARD, INK, SLATE],
    [4.85, "finLM governance layer", "13 MCP tools\nmandate gate · journal · scorer", INK, LIGHT, ICE],
    [9.15, "System of record", "Brokerage, card platform,\nservicing system", CARD, INK, SLATE],
  ];
  boxes.forEach((b) => {
    s.addShape(pres.ShapeType.roundRect, {
      x: b[0], y: 2.05, w: 3.5, h: 1.5, rectRadius: 0.12, fill: { color: b[3] },
    });
    s.addText(b[1], {
      x: b[0] + 0.25, y: 2.3, w: 3.0, h: 0.4, fontFace: H, fontSize: 16, bold: true,
      color: b[4], align: "center", margin: 0,
    });
    s.addText(b[2], {
      x: b[0] + 0.25, y: 2.75, w: 3.0, h: 0.65, fontFace: B, fontSize: 12,
      color: b[5], align: "center", lineSpacing: 16, margin: 0,
    });
  });
  [[4.3, "proposes"], [8.6, "executes"]].forEach((a) => {
    s.addShape(pres.ShapeType.rightArrow, {
      x: a[0], y: 2.68, w: 0.45, h: 0.25, fill: { color: SLATE },
    });
    s.addText(a[1], {
      x: a[0] - 0.15, y: 2.95, w: 0.75, h: 0.25, fontFace: B, fontSize: 10,
      color: SLATE, align: "center", margin: 0,
    });
  });

  s.addText("Every proposed action passes through:", {
    x: M, y: 3.95, w: 6, h: 0.35, fontFace: B, fontSize: 13, bold: true,
    color: INK, margin: 0,
  });

  const gates = [
    ["Kill switch active?", "refuse instantly", CORAL],
    ["Mandate on file?", "no policy, no action", CORAL],
    ["Within all caps?", "value, volume, frequency", MINT],
    ["Journal the thesis", "before anything executes", MINT],
  ];
  gates.forEach((g, i) => {
    const x = M + i * 3.05;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 4.4, w: 2.8, h: 1.0, rectRadius: 0.1, fill: { color: CARD },
    });
    s.addShape(pres.ShapeType.ellipse, {
      x: x + 0.25, y: 4.72, w: 0.34, h: 0.34, fill: { color: g[2] },
    });
    s.addText(g[0], {
      x: x + 0.7, y: 4.6, w: 2.0, h: 0.3, fontFace: B, fontSize: 12.5, bold: true,
      color: INK, margin: 0,
    });
    s.addText(g[1], {
      x: x + 0.7, y: 4.9, w: 2.0, h: 0.4, fontFace: B, fontSize: 11,
      color: SLATE, margin: 0,
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 5.75, w: W - 2 * M, h: 0.85, rectRadius: 0.1, fill: { color: INK },
  });
  s.addText("Afterwards the scorer grades every journaled decision against what actually happened — closing the loop no agent framework closes today.", {
    x: M + 0.4, y: 5.75, w: W - 2 * M - 0.8, h: 0.85, fontFace: B, fontSize: 13.5,
    color: LIGHT, valign: "middle", margin: 0,
  });
  s.addNotes("The layer sits between any MCP-capable LLM and the system of record. Nothing reaches the system of record without passing the kill switch, the mandate, the caps, and being journaled first. Afterwards the scorer grades what happened.");
}

// ============================================================
// 7. PROOF
// ============================================================
{
  const s = lightSlide();
  kicker(s, "PROOF — WE ALREADY BUILT AND RAN IT", false);
  title(s, "Validation killed the intuitive policy before money moved", false);

  const stats = [
    ["2,758", "historical decision events replayed point-in-time", INK],
    ["-1.3%", "per trade: the intuitive 'chase momentum' policy, negative in every configuration", CORAL],
    ["+1.2%", "per trade: the validated policy, benchmark-adjusted, held up out-of-sample", MINT],
  ];
  stats.forEach((st, i) => {
    const y = 1.95 + i * 1.35;
    s.addText(st[0], {
      x: M, y, w: 2.1, h: 0.8, fontFace: H, fontSize: 40, bold: true,
      color: st[2], margin: 0,
    });
    s.addText(st[1], {
      x: M + 2.2, y: y + 0.1, w: 3.3, h: 0.9, fontFace: B, fontSize: 12.5,
      color: SLATE, lineSpacing: 17, margin: 0,
    });
  });

  s.addText("Monthly benchmark-adjusted return of the validated policy (%)", {
    x: 6.5, y: 1.95, w: 6.2, h: 0.3, fontFace: B, fontSize: 12, bold: true,
    color: INK, margin: 0,
  });
  s.addChart(pres.ChartType.bar, [{
    name: "Alpha per trade",
    labels: ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"],
    values: [-1.37, -0.36, 0.72, 1.10, 0.00, 4.32, 0.83, 1.93, 1.46, 2.03, 1.12, 1.75],
  }], {
    x: 6.4, y: 2.3, w: 6.3, h: 3.0,
    barDir: "col", chartColors: [MINT],
    showLegend: false, showTitle: false,
    catAxisLabelColor: SLATE, catAxisLabelFontSize: 10,
    valAxisLabelColor: SLATE, valAxisLabelFontSize: 10,
    valGridLine: { color: "DDE4EE", size: 1 },
    catGridLine: { style: "none" },
    barGapWidthPct: 45,
  });
  s.addText("Positive in 10 of 12 months · 935 trades · profit factor 1.18 · improved on held-out data (1.14 → 1.27)", {
    x: 6.4, y: 5.35, w: 6.3, h: 0.5, fontFace: B, fontSize: 11.5,
    color: SLATE, lineSpacing: 16, margin: 0,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 6.05, w: 5.5, h: 0.85, rectRadius: 0.1, fill: { color: CARD },
  });
  s.addText([
    { text: "Live: ", options: { bold: true, color: INK } },
    { text: "top governed decision returned ", options: { color: SLATE } },
    { text: "+10.31%", options: { bold: true, color: INK } },
    { text: " against a market that fell 0.95%.", options: { color: SLATE } },
  ], {
    x: M + 0.3, y: 6.05, w: 4.9, h: 0.85, fontFace: B, fontSize: 13,
    valign: "middle", margin: 0,
  });
  s.addNotes("We did not just describe a governance layer. We built one and put a real financial agent inside it. Pre-deployment validation showed the intuitive strategy loses in every configuration, and validated the counterintuitive one. Live, the top decision returned 10.31 percent against a falling market.");
}

// ============================================================
// 8. CAUGHT ITSELF WRONG
// ============================================================
{
  const s = darkSlide();
  kicker(s, "THE MOMENT THAT MATTERS", true);
  title(s, "It caught its own hypothesis being wrong", true);

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 1.9, w: 5.7, h: 2.35, rectRadius: 0.12, fill: { color: NAVY_CARD },
  });
  s.addText("WHAT THE AGENT BELIEVED", {
    x: M + 0.35, y: 2.15, w: 5.0, h: 0.3, fontFace: B, fontSize: 11, bold: true,
    charSpacing: 2, color: ICE, margin: 0,
  });
  s.addText("“Moves backed by a genuine news catalyst behave differently, so we take no action on those.”", {
    x: M + 0.35, y: 2.52, w: 5.0, h: 1.1, fontFace: H, fontSize: 16, italic: true,
    color: LIGHT, lineSpacing: 24, margin: 0,
  });
  s.addText("Recorded in the journal, with reasoning, before the outcome was known.", {
    x: M + 0.35, y: 3.66, w: 5.0, h: 0.45, fontFace: B, fontSize: 12,
    color: ICE, margin: 0,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 6.95, y: 1.9, w: 5.7, h: 2.35, rectRadius: 0.12, fill: { color: NAVY_CARD },
  });
  s.addText("WHAT THE SCORER FOUND", {
    x: 7.3, y: 2.15, w: 5.0, h: 0.3, fontFace: B, fontSize: 11, bold: true,
    charSpacing: 2, color: MINT, margin: 0,
  });
  s.addText("The hypothesis was wrong.", {
    x: 7.3, y: 2.52, w: 5.0, h: 0.45, fontFace: H, fontSize: 22, bold: true,
    color: LIGHT, margin: 0,
  });
  s.addText("Catalyst-backed moves reversed just as hard. The agent's own reasoning cost it two profitable decisions — and the data said so within three days.", {
    x: 7.3, y: 3.05, w: 5.0, h: 1.05, fontFace: B, fontSize: 13,
    color: ICE, lineSpacing: 19, margin: 0,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.42, w: 11.93, h: 0.62, rectRadius: 0.12, fill: { color: "16274A" },
  });
  s.addText([
    { text: "n = 5, one week. ", options: { bold: true, color: MINT } },
    { text: "Not statistically valid — and logged, dated and testable anyway. That is the point: the loop caught it, not a person reviewing it later.", options: { color: ICE } },
  ], {
    x: M + 0.35, y: 4.42, w: 11.25, h: 0.62, fontFace: B, fontSize: 13,
    valign: "middle", margin: 0,
  });

  s.addText("A dated, reviewable reversal instead of a forgotten assumption.\nThat is what governance is for.", {
    x: M, y: 5.25, w: 11.9, h: 1.0, fontFace: H, fontSize: 24, bold: true,
    color: LIGHT, lineSpacing: 34, margin: 0,
  });
  s.addText("Most AI agent projects cannot tell you whether they were right. This one keeps receipts — including the unflattering ones.", {
    x: M, y: 6.4, w: 11.9, h: 0.5, fontFace: B, fontSize: 13.5,
    color: ICE, margin: 0,
  });
  s.addNotes("This is the slide that matters. The agent recorded a hypothesis, acted on it, and the scorer proved it wrong three days later. Be first to say the sample size out loud: five decisions over one week, nowhere near statistically valid. The claim is not that the finding is conclusive — it is that the loop surfaced it automatically, dated and reviewable, without a human going looking. No other agent framework produces that artifact.");
}

// ============================================================
// 9. GENERALIZES
// ============================================================
{
  const s = lightSlide();
  kicker(s, "BEYOND THE PROVING GROUND", false);
  title(s, "Swap the action schema and the scorer — the layer holds", false);

  s.addText("Markets were chosen deliberately: they return unambiguous ground truth on a fixed schedule, so a governance layer can be proven rather than asserted. The primitives themselves are domain-agnostic.", {
    x: M, y: 1.85, w: W - 2 * M, h: 0.6, fontFace: B, fontSize: 13.5,
    color: SLATE, lineSpacing: 19, margin: 0,
  });

  const cases = [
    ["Dispute & chargeback agent", "Mandate: refund ceilings, eligible transaction types, per-day counts", "Scorer: was the dispute ultimately upheld?"],
    ["Card benefit activation", "Mandate: which benefits, which cardholder segments, contact frequency", "Scorer: was the activated benefit actually used?"],
    ["End-to-end servicing agent", "Mandate: permitted account actions, value thresholds, escalation rules", "Scorer: was the issue resolved without repeat contact?"],
  ];
  cases.forEach((c, i) => {
    const x = M + i * 4.1;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 2.75, w: 3.7, h: 2.75, rectRadius: 0.12, fill: { color: CARD },
    });
    s.addText(c[0], {
      x: x + 0.35, y: 3.05, w: 3.0, h: 0.7, fontFace: H, fontSize: 17, bold: true,
      color: INK, margin: 0,
    });
    s.addText(c[1], {
      x: x + 0.35, y: 3.85, w: 3.0, h: 0.85, fontFace: B, fontSize: 12,
      color: SLATE, lineSpacing: 17, margin: 0,
    });
    s.addText(c[2], {
      x: x + 0.35, y: 4.75, w: 3.0, h: 0.55, fontFace: B, fontSize: 12, bold: true,
      color: INK, lineSpacing: 17, margin: 0,
    });
  });

  s.addText("The audit trail, the kill switch and the fail-closed mandate never change. Only the action schema and the definition of “was it right” do.", {
    x: M, y: 5.8, w: W - 2 * M, h: 0.75, fontFace: H, fontSize: 17, italic: true,
    color: INK, margin: 0,
  });
  s.addNotes("The primitives are domain-agnostic. A dispute agent, a benefit activation agent, a servicing agent — each needs the same four things. Only the action schema and the outcome scorer change per domain.");
}

// ============================================================
// 10. STATUS / TECH
// ============================================================
{
  const s = lightSlide();
  kicker(s, "STATUS", false);
  title(s, "Built and running, not proposed", false);

  const done = [
    "13 MCP tools live and verified over the protocol, in Claude Desktop and Claude Code",
    "Mandate gate and kill switch enforced server-side — tested, and they fail closed",
    "14 months of market history ingested; simulator reproducible from the repo",
    "Journal actively scoring live decisions with full reasoning retained",
  ];
  done.forEach((t, i) => {
    const y = 1.95 + i * 0.72;
    s.addShape(pres.ShapeType.ellipse, {
      x: M, y: y + 0.04, w: 0.34, h: 0.34, fill: { color: MINT },
    });
    s.addText("✓", {
      x: M, y: y + 0.04, w: 0.34, h: 0.34, fontFace: B, fontSize: 15, bold: true,
      color: INK, align: "center", valign: "middle", margin: 0,
    });
    s.addText(t, {
      x: M + 0.55, y: y, w: 7.0, h: 0.5, fontFace: B, fontSize: 13.5,
      color: INK, valign: "middle", margin: 0,
    });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 8.5, y: 1.95, w: 4.15, h: 2.9, rectRadius: 0.12, fill: { color: CARD },
  });
  s.addText("STACK", {
    x: 8.85, y: 2.25, w: 3.4, h: 0.3, fontFace: B, fontSize: 11, bold: true,
    charSpacing: 2, color: SLATE, margin: 0,
  });
  s.addText("Python · FastMCP · SQLite · pandas\nTavily for evidence retrieval\nZerodha Kite Connect as the live system of record", {
    x: 8.85, y: 2.6, w: 3.4, h: 1.2, fontFace: B, fontSize: 12.5,
    color: INK, lineSpacing: 19, margin: 0,
  });
  s.addText("Model-agnostic: any MCP client. Runs with zero credentials on free public data.", {
    x: 8.85, y: 3.95, w: 3.4, h: 0.75, fontFace: B, fontSize: 12,
    color: SLATE, lineSpacing: 17, margin: 0,
  });

  s.addText("NEXT — PROTOTYPE ROUND", {
    x: M, y: 5.1, w: 6, h: 0.3, fontFace: B, fontSize: 11, bold: true,
    charSpacing: 2, color: SLATE, margin: 0,
  });
  const next = [
    ["Second domain adapter", "prove the layer on a card-servicing action schema"],
    ["Reviewer console", "per-decision attribution and hit-rate reporting for risk teams"],
    ["Structured evidence tagging", "so hypotheses are tested automatically, not by hand"],
  ];
  next.forEach((n, i) => {
    const x = M + i * 4.1;
    s.addText(n[0], {
      x, y: 5.5, w: 3.7, h: 0.3, fontFace: H, fontSize: 15, bold: true,
      color: INK, margin: 0,
    });
    s.addText(n[1], {
      x, y: 5.85, w: 3.7, h: 0.6, fontFace: B, fontSize: 12,
      color: SLATE, lineSpacing: 17, margin: 0,
    });
  });
  s.addNotes("This is not a concept. Thirteen tools are live and verified, the guardrails are tested and fail closed, and the journal is already scoring live decisions. For the prototype round we add a second domain adapter, a reviewer console, and structured evidence tagging.");
}

// ============================================================
// 11. CLOSE
// ============================================================
{
  const s = darkSlide();
  s.addShape(pres.ShapeType.ellipse, {
    x: -1.9, y: 3.4, w: 5.6, h: 5.6, fill: { color: NAVY_CARD },
  });

  s.addText("The bottleneck for financial agents\nis not capability. It is trust.", {
    x: M, y: 1.9, w: 11.5, h: 1.7, fontFace: H, fontSize: 36, bold: true,
    color: LIGHT, lineSpacing: 48, margin: 0,
  });
  s.addText("finLM makes an agent's authority enforceable, its reasoning auditable, and its judgment measurable — so a financial institution can actually deploy one.", {
    x: M, y: 3.8, w: 10.5, h: 0.9, fontFace: B, fontSize: 16,
    color: ICE, lineSpacing: 26, margin: 0,
  });

  const tags = ["Mandate that fails closed", "Instant kill switch", "Reasoning audit trail", "Outcome scoring"];
  tags.forEach((t, i) => {
    const x = M + i * 3.05;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 5.0, w: 2.85, h: 0.6, rectRadius: 0.3, fill: { color: NAVY_CARD },
    });
    s.addText(t, {
      x, y: 5.0, w: 2.85, h: 0.6, fontFace: B, fontSize: 12, bold: true,
      color: MINT, align: "center", valign: "middle", margin: 0,
    });
  });

  s.addText("finLM  ·  Anish Naik  ·  CodeStreet 2026", {
    x: M, y: 6.3, w: 8, h: 0.4, fontFace: H, fontSize: 15, bold: true,
    color: LIGHT, margin: 0,
  });
  s.addNotes("The bottleneck for financial agents is not capability, it is trust. finLM makes authority enforceable, reasoning auditable, and judgment measurable. Thank you.");
}

pres.writeFile({ fileName: "finLM_CodeStreet2026.pptx" }).then(() => console.log("deck written"));
