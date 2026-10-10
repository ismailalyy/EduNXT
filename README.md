# EduNXT
An agent to respond to initial enquiries, draft templates and support operations.

The agent's jobs:

1. **Reply instantly to new WhatsApp enquiries.** It sends a welcome and a questionnaire (curriculum, grades, course, countries, budget, ...). This is the *only* message it ever sends to a client.
2. **Draft the University Options Form** in EduNXT's template, as a PDF.
3. **Draft a Scholarships document**: the scholarships EduNXT can apply for at each university. The row is left empty when there are none.
4. **Send both drafts to a co-founder on WhatsApp.** The co-founder can reply in plain words ("swap Leeds for UCL") to get a revised version, or send `approve`.

It understands English, Arabic and Franco-Arabic, and writes everything in English.

## How many agents?

There are **two agents**, split by who they talk to. Scholarship research is a sub-task of the second agent, not a third agent.

| | Intake Agent | Research Agent |
|---|---|---|
| Reads | client messages (untrusted) | the student profile and founder requests |
| Can do | return structured data only; its one client reply is a fixed template | look up the partner sheet, search the web, fetch pages |
| Model | `claude-haiku-4-5` (replies in seconds) | `claude-opus-5-5` (takes a few minutes) |

- **Why not one agent:** the agent that reads messages from strangers should not be able to browse the web or send documents.
- **Why scholarships aren't separate:** they depend on the university list. When a founder changes the list, the scholarships for new universities are re-researched in the same job, so the two PDFs never disagree. Scholarship lookups run in parallel, one per university.

The flow around the agents (case states, routing and the 24-hour WhatsApp window) is plain Python in `edunxt/workflow.py`.

```
WhatsApp webhook ──> Workflow ──> Intake Agent ──> welcome (first contact only) + founder notification
                         │
                         └──> Research Agent ──> 2 PDFs + summary ──> founder ──(reply: changes / approve)──┐
                                    ^──────────────────────────────────────────────────────────────────────┘
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
| `retry #EN-0012` / `redo #EN-0012` | re-run a failed draft / re-draft from scratch |
| `help` | command list |

Messages from clients after their first one are never answered. They are translated and forwarded to the assigned founder, and the student profile is updated. Drafting starts automatically once the course, countries, curriculum and grades are known.

## Layout

```
edunxt/
  agents/      intake.py, research.py, schemas.py, prompts/*.md
  channels/    whatsapp.py (Cloud API), simulator.py, base.py
  documents/   render.py + HTML templates (WeasyPrint → PDF)
  tools/       partners.py (founder-maintained CSV of universities)
  workflow.py  case state machine, routing, 24h-window handling, worker
  app.py       FastAPI webhook endpoints
data/partners.csv   partner/known universities (edit this)
docs/SETUP.md       prerequisites walkthrough
```
