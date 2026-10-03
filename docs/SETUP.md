# Setting up the prerequisites

This guide gets you from nothing to a working WhatsApp test. It takes about 45 minutes.
Creating the accounts needs your own logins; everything else is already in the repo.

When you're done, run this and fix anything marked ❌:

```bash
python -m edunxt check-setup                         # checks every credential
python -m edunxt check-setup --send-test +2010XXXXXXX  # also sends WhatsApp's hello_world to that phone
```

> Meta renames dashboard menus often. If a screen doesn't match these steps exactly, look for the closest equivalent.

---

## 0. Install (about 5 min)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # you'll fill this in as you go
python -m edunxt render-sample   # should print two PDF paths
```

WeasyPrint needs Pango installed on the machine: `brew install pango` on macOS, `apt install libpango-1.0-0 libpangoft2-1.0-0` on Debian/Ubuntu.

## A. Anthropic API key (about 5 min)

1. Sign in at **console.anthropic.com** and add billing credit under **Billing**.
2. Set a conservative **monthly spend limit** to start (e.g. $50). Each draft logs its estimated cost (`est. $… this run` in the founder's WhatsApp summary and in the server log). Check the real per-draft cost on your first few simulator runs before going live.
3. Go to **API Keys → Create key**, then put it in `.env` as `ANTHROPIC_API_KEY=`.

You can already test the whole agent, with no Meta setup, using the simulator:

```bash
python -m edunxt chat
```

## B. WhatsApp Cloud API test number (about 20 min)

1. Go to **developers.facebook.com**, log in with Facebook and register as a developer.
2. **My Apps → Create App** and choose the use case **"Connect with customers through WhatsApp"**. When asked, create or select a **Business portfolio** (name it "EduNXT").
3. Open **WhatsApp → API Setup**. Meta has provisioned a free **test number**. Copy these into `.env`:
   - **Phone number ID** → `WA_PHONE_NUMBER_ID`
   - **WhatsApp Business Account ID** → `WA_BUSINESS_ACCOUNT_ID`
4. In the **To** field, add up to **5** phone numbers: yours (to play a client) and the co-founders'. Each person types in the code WhatsApp sends them. Click **Send message** to check that the `hello_world` message arrives.
5. **Create a permanent token** (the one on the API Setup page expires after 24 hours):
   1. Go to **business.facebook.com → Settings → Users → System users → Add**, with the **Admin** role.
   2. **Assign assets**: the app (full control) and the WhatsApp account (full control).
   3. **Generate token** for the app, with the permissions `whatsapp_business_messaging` and `whatsapp_business_management`.
   4. Put it in `.env` as `WA_ACCESS_TOKEN=`.
6. Copy the **App secret** from **App settings → Basic** into `META_APP_SECRET=`. It's used to check that webhook calls really come from Meta.
7. **Founder notification template.** WhatsApp only allows free-form messages within 24 hours of the recipient's last message. To reach a founder outside that window, the agent sends a template and holds the drafts until the founder replies.
   1. In **WhatsApp Manager → Message templates → Create template**, choose category **Utility**, name `new_case_ready`, language **English**.
   2. Body: `New EduNXT case {{1}} is ready for review. Reply to this message to receive it.`
   3. Submit it. Approval usually takes minutes to hours.

   Until the template is approved, ask the founders to send any message (e.g. `help`) to the test number once a day.

## C. Your inputs (about 5 min)

- `FOUNDER_NUMBERS`: comma-separated, international format, e.g. `+201001234567,+201112345678`. They must be among the 5 verified test recipients from step B4. New enquiries are assigned to them in turn.
- `WEBHOOK_VERIFY_TOKEN`: any random string. `check-setup` suggests one.
- **Partner universities** (optional): edit `data/partners.csv`, or export it from a Google Sheet with the same columns:

  | column | example |
  |---|---|
  | university | University of Leeds |
  | country | United Kingdom |
  | course | Psychology BSc |
  | degree_level | Bachelor's |
  | fees | £32,750 |
  | requirements | IB: 35 points; A-Level: AAA |
  | scholarships | International Excellence Award £5,000 (optional) |
  | partner | yes / no (yes = pays EduNXT commission; these are always included when they fit) |
  | url | https://www.leeds.ac.uk/... |

  The sample rows are the 10 UK Psychology universities from your example list, marked `partner=no`. Replace them with your real partners.

## D. Connect the webhook (about 10 min)

1. Start the server: `python -m edunxt serve` (port 8000).
2. Expose it publicly. For local testing, a free Cloudflare quick tunnel needs no account:
   ```bash
   cloudflared tunnel --url http://localhost:8000     # prints https://<random>.trycloudflare.com
   ```
   ngrok works too.
3. In your Meta app, go to **WhatsApp → Configuration → Webhook → Edit**:
   - Callback URL: `https://<your-tunnel>/webhooks/meta`
   - Verify token: the same value as `WEBHOOK_VERIFY_TOKEN`
   - Click **Verify and save**, then **Manage** webhook fields and subscribe to **messages**.
4. Test:
   1. From your phone, message the test number something like "salam, 3ayez a3raf 3an el services". You should get the welcome questionnaire.
   2. The founder phone gets a "New enquiry" notification.
   3. Reply with a profile ("IB predicted 36, psychology, UK"). The founder gets the summary and two PDFs a few minutes later.
   4. The founder swipe-replies with changes, or sends `approve`.

The quick-tunnel URL changes every time it restarts, so update it in Meta when it does. Once the app is deployed (the `Dockerfile` runs on Railway, Render, Fly.io or Cloud Run), use the permanent URL instead.

## Where the secrets live

- **Locally:** `.env` (gitignored, so it is never committed).
- **Claude Code cloud sessions:** add the same variables to the environment's settings, so `check-setup` and live tests can run there.
- **Deployed:** the host's secret/environment-variable settings.
- Never paste tokens into chat messages or commit them.

## Going to production later

- Move from the test number to EduNXT's real number. Using the WhatsApp Business app **coexistence** mode keeps the founders' phone app working on the same number. Only the `.env` values change.
- Instagram can be added as another channel adapter (`edunxt/channels/`) without changing the agents.
