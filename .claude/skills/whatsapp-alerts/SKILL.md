---
name: whatsapp-alerts
description: Send JobTracker's WhatsApp notifications. Use when wiring up or debugging WhatsApp delivery, when messages stop arriving or fail silently, when formatting the alert, or when handling the 24-hour window and message templates.
---

# WhatsApp delivery

One recipient (the user), fired from a scheduled run. The whole difficulty is a policy constraint,
not an API one — get that wrong and the code looks correct while nothing arrives.

## The 24-hour window is the thing that breaks this

WhatsApp only allows **free-form** business messages within 24 hours of the recipient's last
inbound message. Outside that window, only a **pre-approved template** can be delivered.

A job tracker is exactly the bad case: it fires on a schedule, and the user has usually not
messaged it in the last 24 hours. So a free-form send will be **accepted by the API and never
delivered** — often with a success-looking response and a later failed status callback.

Consequences for the design:

- The scheduled alert **must** be a template message. Treat free-form as the exception (only
  valid if the user just replied).
- Templates have fixed text with `{{1}}`-style variables. Design the alert *around* that: a
  template with a couple of variables (count, and a short digest line), and put the detail behind
  a link or a follow-up.
- Templates need approval before use. Get one approved early — it is the long-lead item, not the
  code.
- If messages stop arriving with no error, check the window and the template status **before**
  debugging the request.

## Provider

Check the project config for which is wired up before changing anything. Both are viable:

**Meta WhatsApp Cloud API** — direct, no reseller markup, but needs a Meta Business account, a
verified number and template approval.

```
POST https://graph.facebook.com/<version>/<PHONE_NUMBER_ID>/messages
Authorization: Bearer <ACCESS_TOKEN>
{"messaging_product":"whatsapp","to":"9725XXXXXXXX","type":"template",
 "template":{"name":"job_alert","language":{"code":"en"},
             "components":[{"type":"body","parameters":[{"type":"text","text":"3"}]}]}}
```

Pin a specific Graph API version and confirm the current one against Meta's docs — versions are
deprecated on a schedule and an expired version fails at runtime.

**Twilio** — much faster to a first working message; its sandbox needs no approval.

```
POST https://api.twilio.com/2010-04-01/Accounts/<SID>/Messages.json   (basic auth SID:TOKEN)
From=whatsapp:+14155238886   To=whatsapp:+9725XXXXXXXX   Body=...
```

Sandbox caveat that wastes hours: the **sandbox session expires every 72 hours**, and the
recipient must re-send the join code to re-activate. A tracker that "worked yesterday and stopped"
is usually this. For anything long-running, move off the sandbox to a real sender.

For a personal single-recipient tracker, Twilio's sandbox is the right way to prove the pipeline
end-to-end; plan the move to an approved template before relying on it.

Recipient number in **E.164 without `+`** for Meta (`9725...`), **with** `whatsapp:+` prefix for
Twilio. A silently wrong-format number is a common cause of no-delivery.

## Formatting

WhatsApp uses its own markup, not Markdown:

```
*bold*   _italic_   ~strikethrough~   ```monospace```
```

`[text](url)` does **not** render — it shows as literal characters. Put bare URLs on their own
line; the client autolinks them.

Batch one message per run, not one per job — N separate messages for a morning's postings is both
rate-limit bait and unreadable. Lead with the count, then one line per job:

```
*3 new roles in Israel*

• Data Engineer — Taboola, Tel Aviv-Yafo
  https://boards.greenhouse.io/taboola/jobs/123

• Senior Data Analyst — Wiz, Tel Aviv
  https://...
```

Cap the body (roughly 1,000 chars is a safe working limit; the hard limit is higher) and truncate
with `…and N more` rather than letting a long run get rejected. Keep the job URL — it is the only
part the user acts on.

## Operational

- **Secrets in env vars only** (`WHATSAPP_TOKEN`, `TWILIO_AUTH_TOKEN`, `ALERT_TO_NUMBER`), loaded
  from a `.env` that is gitignored. Never commit a token or the phone number; never log them —
  scrub before printing the request.
- **Dry-run mode** that renders the exact message to stdout without sending. Use it for all
  formatting work; every real send costs money and burns the window.
- **Send after matching, record after sending.** See `israel-job-match` — marking jobs as seen
  before a confirmed send loses them permanently.
- **Check the delivery status, not the HTTP code.** A `200`/`201` means accepted, not delivered.
  Failures surface asynchronously (webhook or status field). If a `sent` message never becomes
  `delivered`, it is the 24-hour window or an unapproved template.
- **Fail loudly.** If delivery breaks, the user sees silence — which is indistinguishable from
  "no new jobs today". Log delivery failures somewhere visible, and consider a weekly heartbeat so
  silence is provably not a broken pipeline.
