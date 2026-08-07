# JobTracker

Telegram alerts when a data role opens in Israel.

Every hour it polls 25 Israeli tech companies' careers pages plus LinkedIn,
keeps only the roles you're actually looking for, drops anything it has already
told you about, and sends the rest to your phone.

```
🛠 Data Engineer (2)
• Data Engineer — Taboola  senior
  Tel Aviv-Yafo · greenhouse
• Analytics Engineer — Wiz
  Tel Aviv · greenhouse

📊 Senior Data Analyst (1)
• Product Analyst — Melio  remote
  Remote - Israel · linkedin
```

---

## Setup

### 1. Install

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### 2. Create a Telegram bot

In Telegram, message **@BotFather** and send `/newbot`. Pick a name, then a
username ending in `bot`. It replies with a token like `8123456789:AAH...`.

Put it in `.env` (copy `.env.example` if you don't have one yet):

```
TELEGRAM_BOT_TOKEN=8123456789:AAH...
```

Now **send your new bot any message** — `/start` is fine. Telegram won't let a
bot message you until you've messaged it first, and this is also how the next
step discovers your chat id.

### 3. Connect

```bash
.venv/bin/python -m jobtracker setup-telegram
```

This verifies the token, finds your chat id, writes it to `.env`, and sends a
test message. If you don't get the message, nothing else in this README will
work either — fix it here.

### 4. Seed, so you don't get a flood

The first run would otherwise deliver every currently-open matching role at
once — around 60 of them. Seed instead:

```bash
.venv/bin/python -m jobtracker seed
```

That records today's matches as already-seen without messaging you. From then
on you only hear about roles posted *after* this point. Skip this step if you
do want the current backlog (it will arrive over several runs, capped by
`DIGEST_MAX_JOBS`).

### 5. Run it

```bash
.venv/bin/python -m jobtracker run
```

---

## Running it hourly on GitHub Actions

The point of this project is that it runs while you're not thinking about it.
`.github/workflows/job-check.yml` does that, hourly, whether or not your laptop
is on.

1. Create an **empty private repo** on GitHub.
2. Push:

```bash
git remote add origin git@github.com:<you>/JobTracker.git && git push -u origin main
```

3. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, and add both:

   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

   Both values are in your local `.env`. `.env` itself is gitignored and never
   leaves your machine.

4. **Actions → Job check → Run workflow** to try it immediately.

The workflow commits `state/seen_jobs.json` back to the repo after each run —
that file is what stops you being told about the same job twice, so it has to
persist between runs. It fails loudly if the secrets are missing, rather than
quietly printing the digest into the build log and delivering nothing.

---

## Commands

| Command | What it does |
|---|---|
| `run` | Check every source, message anything new |
| `run --dry-run` | Print the digest instead of sending; touches no state |
| `sources` | Health-check each source: jobs found, Israeli, matched |
| `setup-telegram` | Verify the bot token, save your chat id |
| `test-notify` | Send a test message and prove delivery works |
| `seed` | Mark current matches as seen, without messaging |
| `list` | Show what you've already been alerted about |
| `match "<title>"` | Explain why a title would or wouldn't alert you |

`match` is the fastest way to tune things:

```bash
.venv/bin/python -m jobtracker match "Staff Analytics Engineer"
.venv/bin/python -m jobtracker match "Data Engineer" --location "Remote - US"
```

---

## What it watches

**`careers`** — 25 companies in `config/companies.yaml`, read through their ATS
APIs (Greenhouse, Comeet, Lever, Ashby, SmartRecruiters, Workday). This is the
same data the company's own careers page renders, as JSON: no login, no
scraping, nothing to break when they restyle the page. It is the reliable half
of the system. To add a company, use the `ats-source` skill.

**`linkedin`** — the public guest job-search endpoint, no account needed. Rate
limits hard and can change markup without notice, so it is throttled to one
request per 6 seconds and fails soft: if LinkedIn breaks, `careers` still runs.

Two more sources exist but are **off by default** and stay that way unless you
pass `--enable`: `glassdoor` sits behind Cloudflare and is normally blocked, and
`facebook` needs a logged-in cookie that expires constantly. Neither is worth
relying on.

The Israeli boards (Drushim, AllJobs, JobMaster) were removed — they carry
mostly recruiting-agency reposts of the same roles. `git log` has them if you
change your mind.

## What counts as a match

All of this lives in `config/search.yaml`, and every change is testable with
`match` before you commit to it.

1. **Title** must hit one of three role groups — Data Engineer, Senior Data
   Analyst, Junior Data Scientist — including the equivalent titles Israeli
   companies actually use, in Hebrew and English.
2. **Not excluded** — "Data Center Technician", "Financial Analyst" and friends
   contain the keywords but are never the job.
3. **Location** resolves to Israel, or to remote that's genuinely open to
   Israel. Bare "Remote" is checked against the job description first, because
   most bare-remote roles turn out to be US-scoped.

Seniority and agency filters exist but are **off**, so nothing is hidden from
you; alerts are tagged `senior` / `junior` / `remote` so you can skim.

## Not being told twice

A job's identity is `company + title + location`, deliberately not its URL — the
same role shows up on both the company's ATS and LinkedIn with different links,
and you only want to hear about it once. Fingerprints live in
`state/seen_jobs.json` and expire after 120 days, so a genuinely re-listed role
can alert again.

Two rules that matter, both covered by tests:

- **Nothing is marked seen until delivery is confirmed.** A failed send is
  retried in full on the next run rather than silently swallowing the jobs.
- **Jobs over `DIGEST_MAX_JOBS` are deferred, not dropped.** A backlog drains at
  that rate per run.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | From @BotFather |
| `TELEGRAM_CHAT_ID` | — | Filled in by `setup-telegram` |
| `DIGEST_MAX_JOBS` | `12` | Jobs per batch; `0` means no cap |
| `CALLMEBOT_PHONE` / `CALLMEBOT_APIKEY` | — | Optional WhatsApp fallback, used only if Telegram is unset |
| `FACEBOOK_COOKIE` | — | Only for `--enable facebook` |

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

88 tests, fully offline — no source is polled, so a site being down can't turn
them red. They run on every push via `.github/workflows/tests.yml`.

## When alerts go quiet

Silence and "no new jobs" look identical, so check in this order:

```bash
.venv/bin/python -m jobtracker sources     # is anything still returning jobs?
.venv/bin/python -m jobtracker test-notify # does delivery still work?
.venv/bin/python -m jobtracker list        # or was it just all seen already?
```

The `sources` columns localise the fault: `JOBS 0` means the fetch broke,
`JOBS` high with `ISRAELI 0` means the location filter is wrong, and `ISRAELI`
high with `MATCHED 0` means the title patterns are. The `diagnose-sources` and
`tune-matching` skills in `.claude/skills/` go deeper.
