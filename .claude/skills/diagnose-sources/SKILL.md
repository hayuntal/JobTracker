---
name: diagnose-sources
description: Diagnose and repair JobTracker sources that stopped returning jobs. Use when alerts have gone quiet, a source shows FAILED, the GitHub Action is red, or the user asks why they aren't getting WhatsApp messages.
---

# Diagnose a broken source

Scraped sources break routinely — that is expected, not alarming. Work from
evidence, and be honest when a source is simply unfixable.

## 1. Get the current picture

```bash
.venv/bin/python -m jobtracker sources -v
```

Read the columns carefully — they localise the fault immediately:

| Symptom | What it means |
|---|---|
| `JOBS` is 0, status `FAILED` | Fetch itself broke — network, markup, or bot wall |
| `JOBS` high, `ISRAELI` 0 | Fetch fine; location filter or geo parameter wrong |
| `ISRAELI` high, `MATCHED` 0 | Location fine; title patterns are the problem → use `tune-matching` |
| All sources fail at once | Almost always local network, not the sites |

## 2. Diagnose by source type

**`careers` (ATS)** — the reliable one. If a *single* company fails, its token
probably changed; re-run `/add-company` for it. If *every* company fails, check
network first. A 404 on one board means the company left that ATS.

**`linkedin`** — expect intermittent failure by design.
- `429` → rate limited. The throttle in `jobtracker/http.py` (`_HOST_DELAYS`)
  is already 6s; raise it, don't lower it. It recovers on its own.
- 200 but 0 jobs → LinkedIn changed its markup. Fetch the endpoint by hand,
  inspect the returned HTML, and update the CSS selectors in
  `jobtracker/sources/linkedin.py`.

**`glassdoor` / `facebook`** — known-weak, off by default.
Glassdoor is behind Cloudflare; a block is the expected outcome, not a bug to
fix. Facebook needs a live `FACEBOOK_COOKIE` and expires often. Do not invest
effort making these robust — tell the user the honest cost/benefit.

## 3. Check delivery separately

A working pipeline with a silent phone is a *notifier* problem:

```bash
.venv/bin/python -m jobtracker test-notify
```

- "No delivery channel configured" → `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
  missing from `.env` (or from GitHub repo secrets, for Actions runs). Re-run
  `setup-telegram`.
- `chat not found` → the chat id is wrong, or the user blocked the bot.
- `can't parse entities` → the digest contains HTML that Telegram rejects. Some
  source field escaped `notify/format.py`; that function must escape every
  source-supplied string. This fails the *whole* message, so it looks exactly
  like "no jobs today".
- Never paste a bot token into a log or a commit. `http.redact` exists because
  the token sits in the request path — keep new code paths going through it.

## 4. Check it isn't just "nothing new"

Silence is often correct — every match was already sent. Confirm with:

```bash
.venv/bin/python -m jobtracker list --limit 20
```

To re-test the full path end to end without waiting for a new posting, run a
dry run, which ignores nothing and prints what it would send:

```bash
.venv/bin/python -m jobtracker run --dry-run
```

## 5. For GitHub Actions failures

```bash
gh run list --workflow=job-check.yml --limit 5
gh run view <run-id> --log-failed
```

Common causes: missing repo secrets, or a push conflict on `state/seen_jobs.json`
(the workflow already retries with rebase three times).

## Reporting

State plainly which sources work, which don't, and which aren't worth repairing.
Never "fix" a source by making it silently return an empty list — a source that
fails loudly is strictly better than one that pretends to work.
