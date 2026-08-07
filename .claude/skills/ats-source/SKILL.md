---
name: ats-source
description: Add, verify, or repair a company job source in JobTracker. Use when adding companies to the registry, when a source returns 0 jobs or 404s, when a company changes ATS, or when discovering which ATS an Israeli company uses. Covers Comeet, Greenhouse, Lever, Ashby, SmartRecruiters, Workday.
---

# ATS source adapters

JobTracker pulls jobs from public ATS JSON feeds — not from LinkedIn/Glassdoor scraping. Each
company resolves to a `(provider, token)` pair in the registry. This skill covers adding one,
and fixing one that broke.

## Golden rule: never trust a token that returns 0 Israeli jobs

Slug guessing produces **silent false positives** — `lever/via`, `greenhouse/next` and friends
resolve to a real but *different* company's board. A token is only valid once it returns at least
one posting in a real Israeli location. Validate before writing to the registry.

## Endpoint + field map

All verified live. `Accept: application/json` and a desktop `User-Agent` — some providers 403 a
bare urllib UA.

| Provider | Endpoint | Root | Title | Location |
|---|---|---|---|---|
| Comeet | `https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}` | bare array | `name` | `location.city`, `location.country` (**ISO: `"IL"`**) |
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs` | `jobs[]` | `title` | `location.name` (**no country — `"Tel Aviv-Yafo"`**) |
| Lever | `https://api.lever.co/v0/postings/{token}?mode=json` | bare array | `text` | `categories.location`, plus `country` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{token}` | `jobs[]` | `title` | `location`, `address`, `isRemote` |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100` | `content[]` | `name` | `location.city`, `location.country` |

Apply-URL field: Comeet `url_active_page` · Greenhouse `absolute_url` · Lever `hostedUrl` ·
Ashby `jobUrl` · SmartRecruiters build from `id`.

Notes that matter:
- **Greenhouse** `?content=true` adds the full JD (`content`, HTML-escaped) and `departments`.
  Only request it when the matcher needs the body — it multiplies payload size.
- **SmartRecruiters** paginates via `offset`; `limit` caps at 100.
- **Lever** returns *all* offices; filter per-posting, never per-board.
- **Workday** (`{tenant}.wd{N}.myworkdayjobs.com`) needs a `POST` to its CXS endpoint with a JSON
  body and pagination — noticeably more work. Only add it for a company worth the effort.

## Comeet is the one that matters in Israel

Comeet is the most common ATS on Israeli careers pages — in a sweep of 18 well-known Israeli
companies, 10 embedded Comeet. It is **not** slug-addressable: it needs a `company-uid` **and** a
`token`, both embedded in the company's own careers page. Requesting without the token returns
`{"status":400,"message":"Token is missing"}`.

Extract both from the careers page HTML:

```python
re.search(r'COMEET\.init\(\s*\{[^}]*?"token"\s*:\s*"([0-9A-F]+)"[^}]*?'
          r'"company-uid"\s*:\s*"([0-9A-F]{2}\.[0-9A-F]{3})"', html, re.S)
```

The page ships it inline as:

```js
COMEET.init({ "token": "<40-hex-chars>", "company-uid": "E6.00C", "company-name": "Acme" });
```

Key order varies between sites — match both keys independently rather than assuming `token` comes
first. Some pages instead expose `url_comeet_hosted_page`
(`https://www.comeet.com/jobs/{slug}/{uid}`), which yields the uid but not the token.

## Adding a company

1. **Sniff the careers page first.** Fetch `https://<company>/careers` and regex for
   `comeet`, `boards.greenhouse.io/(\w+)`, `jobs.lever.co/(\w+)`, `jobs.ashbyhq.com/(\w+)`,
   `careers.smartrecruiters.com/(\w+)`, `\.wd\d+\.myworkdayjobs\.com`. This beats slug-guessing
   and is the only way to catch Comeet.
2. **Fall back to token probing** across providers only if the page yields nothing —
   try `slug`, `slug-with-dashes`, `slugwithoutpunct`, `slug` + `inc`/`io`/`com`
   (`Wiz` → `wizinc`, `Gong` → `gongio`, `Aidoc` → `aidocmedical`).
3. **Validate**: fetch, count postings matching the Israeli-location test, require ≥ 1.
4. Write `{provider, token, uid?}` to the registry. Record `israel_jobs` at discovery time —
   a later drop to 0 is the signal the source broke.

Probe concurrently (`ThreadPoolExecutor`, ~12 workers) and treat any non-200 as a miss; these
endpoints are public and unauthenticated, so a modest sweep is fine, but don't hammer them on
every run.

## Seed registry

`reference/companies.json` holds companies already verified against live Israeli postings, plus a
`weak` list that resolved but showed no Israeli jobs — those need re-checking against their careers
page before use. Comeet companies are deliberately absent: their tokens must be extracted fresh,
not hardcoded from someone else's page.

## When a source breaks

| Symptom | Cause | Fix |
|---|---|---|
| 404 | company left the ATS, or token renamed | re-sniff the careers page |
| 200, `jobs: []` | board emptied, or wrong-company token | check `total`; re-validate against Israeli locations |
| 200, jobs, 0 Israeli | **false-positive token** — different company | re-sniff; do not keep the token |
| 400 `Token is missing` | Comeet token dropped/rotated | re-extract `COMEET.init` |
| 403 | UA blocked / Cloudflare | set a browser UA; if it persists, the source is not viable |

Fail one source loudly and independently — a single broken company must never abort the run or
silently shrink the result set.
