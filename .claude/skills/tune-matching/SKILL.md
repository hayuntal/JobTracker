---
name: tune-matching
description: Tune JobTracker's job-matching rules in config/search.yaml. Use when the user gets irrelevant WhatsApp alerts, misses jobs they should have been told about, changes target roles, or wants to add/remove keywords, locations or filters.
---

# Tune what counts as a match

All matching lives in `config/search.yaml` — **never** hardcode rules in
`jobtracker/matching.py`. The three gates are: title → exclusions → location.

## 1. Reproduce before changing anything

`match` explains the verdict for any title, so you can see the actual cause
instead of guessing:

```bash
.venv/bin/python -m jobtracker match "Senior Analytics Engineer" --location "Herzliya"
.venv/bin/python -m jobtracker match "Data Center Technician"
```

The rejection reason names the gate that fired — fix that gate, not another.

## 2. Too much noise (irrelevant alerts)

Work in this order, least destructive first:

1. **Add to `exclude_titles`.** Best tool for a specific recurring annoyance
   ("Data Center", "Payroll Analyst"). Precise, no collateral damage.
2. **Narrow a role's `patterns`.** If `business analyst` is dragging in finance
   roles, remove that pattern rather than excluding a dozen title variants.
3. **Enable `seniority_filter`** to drop Principal/Staff/VP/intern levels. It's
   off by default because the user chose to see every level.
4. **Enable `agency_filter`** to drop recruiting-firm postings.

## 3. Missing jobs that should have matched

1. Confirm the source even returned it — `sources -v`. If the job never arrived,
   this is a *source* problem, not a matching one → use `diagnose-sources`.
2. If it arrived but was rejected, run `match` on its exact title to see which
   gate fired.
3. Add the real-world title variant to the right role group in `roles:`.
   Keep Hebrew and English variants together — both are matched.
4. If the location was the blocker, add the city to `location.israel_terms`.
   Remember Greenhouse often returns a bare city like `Tel Aviv-Yafo` with **no
   country**, so city coverage matters more than the word "Israel".

## 4. Pattern-writing rules

- Patterns match **whole words**, case-insensitively, and tolerate `-`, `_`, `/`
  and extra spaces between words. `data engineer` matches "Data-Engineer" and
  "Senior Data Engineer".
- They do **not** match inside words: `bi` will not match "ambient".
- Longest pattern wins, so `senior data analyst` is reported in preference to
  `data analyst`. Keep specific variants in the list even if a shorter one would
  also match — it makes the WhatsApp alert more informative.
- Hebrew works the same way; word boundaries handle Hebrew letters correctly.

## 5. Always verify the blast radius

A pattern change can silently affect far more than the one job you were fixing.
Compare match counts against live data before and after:

```bash
.venv/bin/python -m jobtracker run --only careers --dry-run 2>&1 | tail -5
```

Check the `matched` count moved the way you intended. If you widened a pattern
and matches jumped from 25 to 300, you've over-matched — tighten it.

`--dry-run` never writes state and never sends WhatsApp, so it's safe to repeat.

## 6. Re-test the specific cases

Re-run the exact `match` commands from step 1 and confirm each now gives the
intended verdict — including the ones that were already correct, to prove you
didn't regress them.
