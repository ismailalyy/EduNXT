# Running and recording the PoC

## How it runs

The agent runs **on your laptop**. WhatsApp reaches it through a free Cloudflare tunnel.

```
Client phone ──WhatsApp──> Meta ──webhook──> tunnel ──> your laptop: python -m edunxt serve
                                                          ├─ Intake Agent (Claude Haiku): a few seconds
                                                          ├─ Drafting Agent (Claude Sonnet, EduNXT's lists only)
                                                          └─ Research Agent (Claude Opus + web): only on "research …"
Founder phone <──WhatsApp── Meta <──API── your laptop  (summary text + 2 PDFs)
```

Everything is stored in `edunxt.db` (SQLite), and the PDFs go to `out/EN-0001/...`. The terminal log shows every step and the estimated cost of each draft.

You can test before Meta approves the `new_case_ready` template. The template is only used when a founder hasn't messaged the test number in the last 24 hours. Before recording, the founder phone sends `help`, which opens a 24-hour window.

## Pre-flight (about 10 minutes before recording)

1. `.env` is complete. Run `python -m edunxt check-setup` and confirm everything is ✅.
2. Clear old test data so the case is numbered **EN-0001**: delete `edunxt.db` and the `out/` folder.
3. Terminal 1: `python -m edunxt serve`
4. Terminal 2: `cloudflared tunnel --url http://localhost:8000`. Copy the `https://….trycloudflare.com` address.
5. In the Meta app, go to **WhatsApp → Configuration → Webhook → Edit**:
   - Callback URL: `https://….trycloudflare.com/webhooks/meta`
   - Verify token: your `WEBHOOK_VERIFY_TOKEN`
   - Click **Verify and save** and make sure **messages** is subscribed.
6. From the **founder** phone, send `help` to the test number. The command list should come back.
7. The **client** phone must be one of the 5 verified test numbers, and must not be listed in `FOUNDER_NUMBERS`.

## Recording script

Set up a split screen: client phone on the left, founder phone on the right (mirroring tools: scrcpy for Android, QuickTime for iPhone). Optionally show the terminal log in a corner. Put the measured timings from `out/benchmark/summary.md` in captions.

| # | Who does what | What to show / say |
|---|---|---|
| 1 | **Client** sends: `salam, 3ayez a3raf 3an el services beta3etkom` | A Franco-Arabic message, as real students write. |
| 2 | **Client** receives the English welcome and questionnaire within seconds | "Instant reply at 2 a.m., while the founders are at their day jobs." |
| 3 | **Founder** receives *🆕 New enquiry EN-0001* with an English translation | The founder is kept informed, but doesn't have to act. |
| 4 | **Client** sends: `ana Khaled, IB predicted 36, HL Maths 6 Physics 6 Chemistry 6, 3ayez adros electrical engineering fe Canada, ana masry` | The profile is complete (nationality decides international or local fees), so drafting starts. |
| 5 | **Founder** receives *✅ Profile complete … ⏳ Drafting* | |
| 6 | *(short wait)* | The agent picks from EduNXT's own university list. No web research per student, so it's fast and cheap. |
| 7 | **Founder** receives the summary, *University options* PDF and *Scholarships* PDF | Open both PDFs: EduNXT's own template, ready to forward. |
| 8 | **Founder** swipe-replies to the PDF: `replace University of Calgary with Concordia University` | Changes in plain words, from a phone. |
| 9 | **Founder** receives v2 of both PDFs, with the change summary | Only the changed row is re-researched. |
| 10 | **Founder** sends `approve` | The case closes. The founder forwards the PDFs to the student; the agent never contacts the student itself. |

Optional extra shots:
- the founder sends `research add 2 electrical engineering bachelor's in the Netherlands under €15k`; the reply lists what was added, how many searches and pages it used, and the cost;
- the founder sends `cases` to list open cases;
- the founder sends `new Omar, IB 34, CS in Germany, €10k budget` to start a case from an Instagram enquiry.

## If something goes wrong

| Symptom | Fix |
|---|---|
| No welcome reply | Check the terminal 1 log. If nothing arrived, check the webhook URL and that **messages** is subscribed. |
| `401 bad signature` in the log | `META_APP_SECRET` doesn't match the app's secret. |
| Founder gets nothing, and the log says the template failed | The founder's 24h window is closed. Send `help` from the founder phone. |
| The draft says the list has no universities for the request | Send `research add …` from the founder phone, then `redo #EN-…`. |

**Fallback for the recording:** `python -m edunxt chat` runs the same flow in the terminal, with no WhatsApp at all. Use `/client`, `/founder` and `/reply <id> <text>`.

## Measuring cost and quality

```bash
python -m edunxt benchmark --cases canada_ee             # per-student cost: intake + draft from the lists + 1 revision
python -m edunxt benchmark --cases canada_ee --research  # per-update cost: Research Agent rebuilds that case's rows from the web
```

Output goes to `out/benchmark/` (or `out/benchmark_research/`):
- `results.csv`: seconds, tokens, web searches and $ for every step;
- `summary.md`: averages, plus how many of the consultants' universities the agent chose or found;
- PDFs and JSON for each case; in research mode, `research_log.jsonl` lists every search and page read.

With Docker on Windows (Command Prompt), mount `data` and `var` so list updates and results are kept:
`docker run --rm --env-file .env -v "%cd%\data:/app/data" -v "%cd%\var:/app/var" edunxt python -m edunxt benchmark --cases canada_ee`

These are the numbers the deck uses.

## Cost model

```bash
python scripts/build_cost_model.py   # rebuilds docs/cost_model.xlsx, AI-cost inputs taken from out/benchmark/results.csv
```

`docs/cost_model.xlsx` shows monthly cost for 25–500 new enquiries on WhatsApp and on Instagram. Every assumption (message counts, Meta's Egypt rates, VAT, share of enquiries drafted, average fees) is an editable blue cell on the **Inputs** sheet. Without benchmark results the AI-cost cells stay blank (yellow) and the totals show messaging fees only. Open it in Excel or LibreOffice so the formulas recalculate.

