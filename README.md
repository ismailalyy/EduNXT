# EduNXT
An agent to respond to initial enquiries, draft templates and support operations.

The agent's jobs:

1. **Reply instantly to new WhatsApp enquiries.** It sends a welcome and a questionnaire (curriculum, grades, course, countries, budget, ...). This is the *only* message it ever sends to a client.
2. **Draft the University Options Form** in EduNXT's template, as a PDF.
3. **Draft a Scholarships document**: the scholarships EduNXT can apply for at each university. The row is left empty when there are none.
4. **Send both drafts to a co-founder on WhatsApp.** The co-founder can reply in plain words ("swap Leeds for UCL") to get a revised version, or send `approve`.

It understands English, Arabic and Franco-Arabic, and writes everything in English.

## How many agents?

There are **three agents**. They are split by who they talk to and by when they run, so the web, the expensive part, is only used when a founder asks for it.

| | Intake Agent | Drafting Agent | Research Agent |
|---|---|---|---|
| Runs | on every client message | for every student draft and revision | only when a founder sends `research …` |
| Reads | client messages (untrusted) | the student profile plus EduNXT's university and scholarship lists | the founder's request plus the web |
| Can do | return structured data; its one client reply is a fixed template | choose rows from the lists by id; it **cannot** browse or invent | search and read official university pages, then add or correct rows in the lists |
| Model | `claude-haiku-4-5` | `claude-sonnet-5-5` | `claude-opus-5-5` |

- **EduNXT's lists are the single source of truth:** `data/partners.csv` (universities) and `data/scholarships.csv`. Founders can edit them in Excel, or ask the Research Agent to update them. Student documents only ever use what is in these lists, copied verbatim.
- **Why research is separate:** web research is about 90% of the cost of a draft. In the first benchmark, a draft with live research cost $4.68. Doing research once per list update, instead of once per student, removes it from the per-student cost.
- **Research is auditable:** every search and page the Research Agent reads is appended to `out/research_log.jsonl`. Its web tools are capped (`RESEARCH_MAX_SEARCHES`, `RESEARCH_MAX_FETCHES`, `RESEARCH_FETCH_MAX_TOKENS`) and can be limited to chosen sites (`RESEARCH_ALLOWED_DOMAINS`).

The flow around the agents (case states, routing and the 24-hour WhatsApp window) is plain Python in `edunxt/workflow.py`.

```
WhatsApp webhook ──> Workflow ──> Intake Agent ──> welcome (first contact only) + founder notification
                         │
                         ├──> Drafting Agent (EduNXT's lists only) ──> 2 PDFs + summary ──> founder ──(changes / approve)──┐
                         │          ^──────────────────────────────────────────────────────────────────────────────────┘
                         └──> Research Agent (web, on "research …") ──> updates data/partners.csv + data/scholarships.csv
Case states: AWAITING_PROFILE → DRAFTING → IN_REVIEW ⇄ REVISING → APPROVED
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env               # add ANTHROPIC_API_KEY at least
python -m edunxt render-sample     # renders the Layla Diab example PDFs into out/sample/
python -m edunxt chat              # play client and founder in the terminal (no WhatsApp needed)
python -m edunxt check-setup       # checks credentials
python -m edunxt serve             # webhook server + background worker
pytest                             # tests (no API calls); RUN_LIVE=1 also runs the live intake checks
```

To connect a real WhatsApp number, follow **[docs/SETUP.md](docs/SETUP.md)**.

## Founder commands (on WhatsApp)

| Send | Effect |
|---|---|
| a swipe-reply to a draft, e.g. *remove Leeds, add 2 Dutch options under €15k* | revised draft (v2, v3, ...) |
| `#EN-0012 ...` | address a case by number instead of swipe-replying |
| `approve` | close the case; you forward the PDFs to the client yourself |
| `cases` | list your open cases |
| `new Omar, IB 34, CS in Germany, €10k budget` | start a case yourself (e.g. for an Instagram enquiry) |
| `research add 3 Dutch EE bachelor's under €15k` | update EduNXT's university/scholarship lists from official sites |
| `research fill in IB and Thanaweya Amma requirements for the Canada EE rows` | complete missing details in the lists |
| `retry #EN-0012` / `redo #EN-0012` | re-run a failed draft / re-draft from scratch |
| `help` | command list |

When a draft can't be filled well from the lists, the founder summary says what's missing so a founder can send a `research …` request and then `redo #EN-…`.

Messages from clients after their first one are never answered. They are translated and forwarded to the assigned founder, and the student profile is updated. Drafting starts automatically once the course, countries, curriculum and grades are known.

## Layout

```
edunxt/
  agents/      intake.py, research.py, schemas.py, prompts/*.md
  channels/    whatsapp.py (Cloud API), simulator.py, base.py
  documents/   render.py + HTML templates (WeasyPrint → PDF)
  tools/       partners.py (EduNXT's university and scholarship lists: lookup and safe updates)
  workflow.py  case state machine, routing, 24h-window handling, worker
  app.py       FastAPI webhook endpoints
data/partners.csv   partner/known universities (edit this)
docs/SETUP.md       prerequisites walkthrough
```
