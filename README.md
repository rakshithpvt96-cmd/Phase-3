# Phase 3 Catalyst Tracker

A free, self-hosted biotech catalyst tracker: Phase 3, industry-sponsored
clinical trials with primary completion dates in a "catalyst window" (about
to read out, or likely sitting on unreleased topline data), cross-referenced
against SEC EDGAR 8-K filings, plus a per-drug enrichment/detail page.

Runs entirely on **GitHub Actions + GitHub Pages** — no backend server, no
paid services, no API keys required (a couple of free optional keys raise
rate limits; see below).

## How it works

```
scripts/fetch_catalysts.py   -> data/catalysts.json, data/history/*.json, data/new_since_last_run.json
scripts/enrich_drugs.py      -> data/drugs/{slug}.json, data/drugs/_conditions_cache/{slug}.json
index.html + assets/app.js   -> sortable/filterable catalyst table, links to drug.html
drug.html + assets/drug.js   -> per-drug detail page, reads data/drugs/{slug}.json
```

`.github/workflows/fetch.yml` runs both scripts twice a day (06:00 and
18:00 UTC) and on manual dispatch, and commits any changed `data/*.json`
back to the repo using the built-in `GITHUB_TOKEN` — no personal access
token needed.

`.github/workflows/pages.yml` deploys the static site (root of the repo) to
GitHub Pages on every push to `main`.

Both Python scripts use **only the standard library** (`urllib`, `json`,
`xml.etree`) — nothing to `pip install`.

## One-time setup

1. **Enable Pages**: repo Settings → Pages → Source: "GitHub Actions".
2. **(Recommended) Set a contact email**: repo Settings → Secrets and
   variables → Actions → Variables → New repository variable
   `CONTACT_EMAIL` = an email you control. This is sent as part of the
   User-Agent header to ClinicalTrials.gov, SEC EDGAR, and NCBI — all three
   ask automated clients to self-identify, and SEC EDGAR will reject
   requests without a descriptive User-Agent. It is not required for the
   scripts to *run*, but is required for SEC EDGAR to reliably accept
   requests from your run.
3. **Kick off the first run**: Actions tab → "Fetch catalysts & enrich
   drugs" → Run workflow. Subsequent runs happen automatically twice daily.
4. Once `data/catalysts.json` has content and Pages has deployed, your site
   is live at `https://<owner>.github.io/<repo>/`.

### Optional free API keys (higher rate limits only — never required)

| Env var | Where to get it | What it raises |
|---|---|---|
| `NCBI_API_KEY` | https://www.ncbi.nlm.nih.gov/account/ (free account) | PubMed E-utilities: 3 req/s → 10 req/s |
| `FDA_API_KEY` | https://open.fda.gov/apis/authentication/ (free, no approval wait) | openFDA: shared pool → per-key quota |

Set either as a repository **secret** (Settings → Secrets and variables →
Actions → Secrets) with the same name; `fetch.yml` picks them up
automatically if present and runs fine without them.

Other tunables (repository variables, all optional, all have sane
defaults): `SEC_EDGAR_USER_AGENT`, `MAX_DRUGS_PER_RUN`.

## Data contract

- `data/catalysts.json` — every tracked trial, each trial's `drug_names`
  list is what `enrich_drugs.py` and the frontend key off of (slugified,
  lowercase, non-alphanumerics → `-`).
- `data/new_since_last_run.json` — diff of the current run vs. the previous
  one, for anyone who wants to watch just what's new (e.g. via a feed
  reader pointed at the raw file, or a follow-up automation).
- `data/history/catalysts-<timestamp>.json` — rolling snapshots (last 90 by
  default; oldest are pruned automatically).
- `data/drugs/{slug}.json` — one enrichment profile per unique drug name.
  Re-enriched every `REFRESH_DAYS_APPROVED` (default 30) days once a drug
  has an FDA label, or every `REFRESH_DAYS_UNAPPROVED` (default 7) days
  while it doesn't yet — investigational drugs are cheap to recheck for a
  newly-posted label; approved-drug label text rarely changes.
- `data/drugs/_conditions_cache/{condition-slug}.json` — MedlinePlus
  disease summary, cached once per condition (not per drug, since many
  trials in the same indication share one).

## The no-fabrication rule

`fda_label.mechanism_of_action`, `.pharmacokinetics`, `.indications_and_usage`,
and `.boxed_warning` are only ever populated from an actual FDA label
(openFDA, with DailyMed as a fallback existence check). If a drug has no
approved label, every one of those fields is `null` and
`fda_label.further_reading` carries pre-built PubMed search URLs instead.
The frontend enforces the same split visually: sections sourced from a real
FDA label get a green left border and a "Confirmed FDA label" tag; sections
that fell back to literature search get an amber border and a "Literature
only" tag. See `tests/test_enrich_fixtures.py` for the fixture tests that
pin this behavior for both an approved and an unapproved drug.

## Related / competitor drugs

`related_drugs.items` is built from ChEMBL drugs sharing the same
mechanistic target when available, or — if ChEMBL has no target data for
the drug — other ClinicalTrials.gov Phase 3 trials in the same condition
sponsored by someone else. Either way it is labeled "drugs with related
mechanism or same indication — not a curated competitive analysis," never
presented as a vetted competitor list.

## Running the tests

Both fixture test suites monkeypatch the HTTP layer with canned,
schema-accurate API responses (this repo's dev sandbox network is
restricted, and the point of the tests is to pin parsing/fallback logic
without depending on live network anyway):

```
python3 tests/test_enrich_fixtures.py
python3 tests/test_fetch_fixtures.py
```

## Manually running the scripts locally

```
export CONTACT_EMAIL="you@example.com"
python3 scripts/fetch_catalysts.py
python3 scripts/enrich_drugs.py
python3 -m http.server 8000   # then open http://localhost:8000/
```

## Disclaimer

Data is sourced from ClinicalTrials.gov, SEC EDGAR, PubChem, RxNorm,
ChEMBL, openFDA, DailyMed, PubMed, and MedlinePlus. It is informational
only and not investment advice. Primary completion dates, especially
`ESTIMATED` ones, frequently slip — always verify against the linked
source before acting on anything here.
