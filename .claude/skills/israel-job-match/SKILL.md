---
name: israel-job-match
description: Decide whether a job posting is Israel-based and matches the target roles, and whether it is new. Use when writing or debugging the filter/matching layer, tuning keywords, chasing false positives or missed jobs, or handling dedup so the same job is not alerted twice.
---

# Matching: is it in Israel, is it the right role, is it new

Three independent gates. A posting alerts only if it passes all three. Each has a distinct
failure mode, so keep them separate and testable — never collapse them into one regex.

## Gate 1 — Israel

ATS providers do **not** agree on how to express location. Two verified shapes break naive
matching:

- **Greenhouse** returns a bare city with no country: `"Tel Aviv-Yafo"`. Matching on `"israel"`
  finds nothing.
- **Comeet** returns an ISO code: `location.country == "IL"`.

So the test is: *structured country field is IL/Israel* **OR** *the location string contains a
known Israeli city*.

### The `IL` trap

`IL` is Israel's ISO code **and** the US postal abbreviation for Illinois. `"Chicago, IL"` and
`"Deerfield, IL"` are extremely common in these feeds.

Accept `IL` **only** from a structured country field (`location.country`, Lever's `country`).
Never accept it from a free-text location match. A bare `\bIL\b` regex over location strings will
quietly fill the alerts with Illinois jobs.

### City list

Match case-insensitively, tolerate the spelling variants, and include Hebrew — a few boards
localize:

```
israel, ישראל, tel aviv (tel-aviv, tel aviv-yafo, tlv), jerusalem, haifa, herzliya (herzeliya),
ra'anana (raanana), netanya, petah tikva (petach/petah-tikva), ramat gan, givatayim, rehovot,
be'er sheva (beer-sheva, beersheba), yokneam (yoqneam), kfar saba, hod hasharon, rosh ha'ayin,
airport city, modi'in, caesarea, lod, ashdod, migdal ha'emek, or yehuda, bnei brak, holon
```

Apostrophes and hyphens are inconsistent across boards — normalize them out before comparing, or
make them optional in the pattern (`ra'?anana`, `be'?er.?sheva`).

### Remote

`"Remote"` alone is not Israel. `"Remote, Israel"` and `"Remote (TLV)"` are. Treat a bare
`Remote` / `isRemote: true` with no Israeli marker as a miss — including it floods the feed with
US and EU roles. If a global-remote role is wanted, that is a separate opt-in flag, not a
loosening of this gate.

## Gate 2 — role

Targets: **Data Engineer**, **Senior Data Analyst**, **Junior Data Scientist**.

### Substring matching is the main bug source

`"Data Engineer"` is a substring of `"Data Engineering Manager"`, `"Director of Data
Engineering"`, and `"Data Engineering Team Lead"`. Plain `in` / `contains` silently promotes
management roles into the feed.

Match on a word-boundary pattern, then apply a **negative guard** on seniority:

```
reject if title matches: manager, director, head of, vp, chief, principal, staff,
                         team lead, tech lead, group lead, architect
```

...unless the target role itself is a lead role. Run the guard on the *title*, not the JD body.

### Seniority for the junior/senior variants

Israeli boards rarely write "Junior". A junior Data Scientist role usually surfaces as plain
`"Data Scientist"`, or as `Associate` / `Entry Level` / `Student` / `Intern` / `New Grad`, or
with `0-2 years` in the body. Conversely, plain `"Data Scientist"` is often mid-level.

Practical approach:
- `Junior Data Scientist` → match `Data Scientist` titles, then **reject** `senior`, `sr.`,
  `lead`, `staff`, `principal`, `expert`, `II`/`III`. Accept the rest and let the seniority be
  confirmed by reading the posting.
- `Senior Data Analyst` → require an explicit senior marker (`senior`, `sr\.?`) alongside
  `Data Analyst`.

Flag near-misses rather than dropping them silently — a `Data Scientist` role that fails the
junior test is worth logging so the keyword rules can be tuned against real data.

### Adjacent titles

Decide deliberately, once, and write it down in config: `Analytics Engineer`, `BI Developer`,
`BI Analyst`, `ML Engineer`, `Big Data Engineer`, `Data Platform Engineer`, `DWH Engineer`.
`Big Data Engineer` and `Data Platform Engineer` are usually genuine Data Engineer roles in the
Israeli market; `BI Developer` usually is not what a Data Engineer search wants. Keep this list in
config, not in code — it will be tuned repeatedly.

Hebrew titles appear occasionally (`מהנדס/ת דאטה`, `אנליסט/ית`); the `/ת` gender suffix breaks
exact matching, so match the stem.

## Gate 3 — new

### Fingerprint, not just ID

Provider job IDs change when a posting is closed and reposted — which happens constantly and would
re-alert the same job. Store both:

- `provider:token:job_id` — exact identity
- `sha256(company + normalized_title + normalized_location)` — survives repost

Normalize before hashing: lowercase, collapse whitespace, strip punctuation and `(m/f)`,
`- Tel Aviv`, req numbers, and trailing `#1234`.

A job is new only if **neither** key has been seen.

### Seed the first run

On an empty database every matching job is "new" — a first run would fire hundreds of alerts.
The first run must record everything it sees and send **nothing** (or a single summary). Make this
explicit, not an accident of an empty table: a `--seed` / `first_run` flag that persists state and
skips delivery.

### Never mark sent before it is sent

Write the seen-record only **after** delivery succeeds. If the order is reversed, one failed send
loses that job permanently — it will never be new again. On a delivery failure, leave the record
absent and let the next run retry it.

Keep closed postings in the store; pruning them re-opens the repost hole. Prune on age (say 180
days) instead.

## Testing

Fixture-driven, no network. Keep saved JSON responses from each provider — including the awkward
real cases: `"Tel Aviv-Yafo"`, `country: "IL"`, `"Chicago, IL"`, `"Remote"`,
`"Data Engineering Manager"`, `"Senior Data Scientist"`. Every gotcha above should have a test
that fails if the guard is removed.
