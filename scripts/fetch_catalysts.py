#!/usr/bin/env python3
"""Catalyst tracking layer for the Phase 3 catalyst tracker.

Queries ClinicalTrials.gov API v2 for Phase 3, industry-sponsored,
interventional trials in two windows:
  (a) primary completion in the next UPCOMING_WINDOW_DAYS days
  (b) primary completion between LOOKBACK_MIN_DAYS and LOOKBACK_MAX_DAYS
      days ago (likely sitting on unreleased topline data)

Cross-references each trial's sponsor against SEC EDGAR full-text search
for recent 8-Ks mentioning "topline" or "primary endpoint", writes the
combined result to data/catalysts.json (with a dated snapshot in
data/history/), and diffs against the previous run into
data/new_since_last_run.json.

This script does not import enrich_drugs.py (and vice versa) so either one
can be changed independently -- they only share the on-disk JSON contract.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CATALYSTS_FILE = DATA_DIR / "catalysts.json"
HISTORY_DIR = DATA_DIR / "history"
NEW_SINCE_FILE = DATA_DIR / "new_since_last_run.json"

CTGOV_BASE = "https://clinicaltrials.gov/api/v2/studies"
EDGAR_FTS_BASE = "https://efts.sec.gov/LATEST/search-index"

UPCOMING_WINDOW_DAYS = int(os.environ.get("UPCOMING_WINDOW_DAYS", "14"))
LOOKBACK_MIN_DAYS = int(os.environ.get("LOOKBACK_MIN_DAYS", "30"))
LOOKBACK_MAX_DAYS = int(os.environ.get("LOOKBACK_MAX_DAYS", "75"))
EDGAR_LOOKBACK_DAYS = int(os.environ.get("EDGAR_LOOKBACK_DAYS", "120"))
HISTORY_RETENTION = int(os.environ.get("HISTORY_RETENTION", "90"))

CTGOV_PAGE_SIZE = 100
CTGOV_MAX_PAGES = 30  # safety cap: 3000 studies per window is far beyond any real query

CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "your-email@example.com")
CTGOV_USER_AGENT = f"biotech-phase3-catalyst-tracker/1.0 (contact: {CONTACT_EMAIL})"
# SEC asks every automated requester to identify itself: "Sample Company Name AdminContact@domain.com"
SEC_EDGAR_USER_AGENT = os.environ.get("SEC_EDGAR_USER_AGENT", f"biotech-phase3-catalyst-tracker {CONTACT_EMAIL}")

REQUEST_TIMEOUT = 30
RETRY_COUNT = 3
SLEEP_BETWEEN_CALLS = float(os.environ.get("FETCH_SLEEP_SECONDS", "0.3"))

DRUG_INTERVENTION_TYPES = {"DRUG", "BIOLOGICAL"}


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def http_get_json(url, params=None, headers=None, retries=RETRY_COUNT):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req_headers = {"Accept": "application/json"}
    req_headers.update(headers or {})
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read()
            time.sleep(SLEEP_BETWEEN_CALLS)
            if not body:
                return None
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                time.sleep(SLEEP_BETWEEN_CALLS)
                return None
            last_err = e
            if e.code == 429 or e.code >= 500:
                time.sleep(SLEEP_BETWEEN_CALLS * (2 ** attempt))
                continue
            time.sleep(SLEEP_BETWEEN_CALLS)
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(SLEEP_BETWEEN_CALLS * (2 ** attempt))
    print(f"  ! request failed after {retries} attempts: {url} ({last_err})", file=sys.stderr)
    return None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# ClinicalTrials.gov API v2
# ---------------------------------------------------------------------------

def search_studies(query_term):
    """Returns (studies, complete) -- complete is False if a page failed to
    fetch, so callers can avoid treating a partial outage as "zero results"."""
    studies = []
    params = {"query.term": query_term, "pageSize": str(CTGOV_PAGE_SIZE), "format": "json"}
    page_token = None
    for _ in range(CTGOV_MAX_PAGES):
        p = dict(params)
        if page_token:
            p["pageToken"] = page_token
        data = http_get_json(CTGOV_BASE, params=p, headers={"User-Agent": CTGOV_USER_AGENT})
        if data is None:
            return studies, False
        batch = data.get("studies") or []
        studies.extend(batch)
        page_token = data.get("nextPageToken")
        if not page_token or not batch:
            break
    return studies, True


def parse_study(study, window_label):
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    status = ps.get("statusModule", {}) or {}
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {}) or {}
    conditions_mod = ps.get("conditionsModule", {}) or {}
    design_mod = ps.get("designModule", {}) or {}
    arms_mod = ps.get("armsInterventionsModule", {}) or {}

    nct_id = ident.get("nctId")
    lead_sponsor = sponsor_mod.get("leadSponsor", {}) or {}
    pc_struct = status.get("primaryCompletionDateStruct", {}) or {}
    results_post_struct = status.get("resultsFirstPostDateStruct", {}) or {}

    interventions = arms_mod.get("interventions", []) or []
    drug_names = []
    for iv in interventions:
        if (iv.get("type") or "").upper() in DRUG_INTERVENTION_TYPES:
            nm = iv.get("name")
            if nm and nm not in drug_names:
                drug_names.append(nm)

    return {
        "nct_id": nct_id,
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "phases": design_mod.get("phases", []) or [],
        "sponsor": lead_sponsor.get("name"),
        "sponsor_class": lead_sponsor.get("class"),
        "primary_completion_date": pc_struct.get("date"),
        "primary_completion_date_type": pc_struct.get("type"),
        "window": window_label,
        "conditions": conditions_mod.get("conditions", []) or [],
        "interventions": [{"name": iv.get("name"), "type": iv.get("type")} for iv in interventions],
        "drug_names": drug_names,
        "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None,
        "has_results": bool(study.get("hasResults")),
        "results_first_post_date": results_post_struct.get("date"),
    }


# ---------------------------------------------------------------------------
# SEC EDGAR full-text search cross-reference
# ---------------------------------------------------------------------------

def normalize_sponsor(name):
    if not name:
        return ""
    s = re.sub(
        r"[,.]?\s*(Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|plc|PLC|LLC|L\.L\.C\.|S\.A\.|N\.V\.|AG|Co\.?|Company)\s*$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    )
    return s.strip().rstrip(",.").strip()


def edgar_hit_url(hit):
    src = hit.get("_source", {}) or {}
    hit_id = hit.get("_id") or ""
    accession_no, _, filename = hit_id.partition(":")
    ciks = src.get("ciks") or []
    if ciks and accession_no and filename:
        cik = ciks[0].lstrip("0") or "0"
        acc_nodash = accession_no.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{filename}"
    return None


def edgar_search_sponsor(sponsor_name, cache):
    if sponsor_name in cache:
        return cache[sponsor_name]

    norm = normalize_sponsor(sponsor_name)
    if not norm:
        cache[sponsor_name] = []
        return []

    start_date = (date.today() - timedelta(days=EDGAR_LOOKBACK_DAYS)).isoformat()
    end_date = date.today().isoformat()

    matches = []
    for keyword in ("topline", "primary endpoint"):
        params = {
            "q": f'"{norm}" "{keyword}"',
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
        }
        data = http_get_json(EDGAR_FTS_BASE, params=params, headers={"User-Agent": SEC_EDGAR_USER_AGENT})
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        for h in hits:
            src = h.get("_source", {}) or {}
            matches.append(
                {
                    "form": src.get("root_form") or src.get("form") or "8-K",
                    "filed_at": src.get("file_date"),
                    "company": (src.get("display_names") or [None])[0],
                    "url": edgar_hit_url(h),
                    "keyword_matched": keyword,
                }
            )

    seen = set()
    deduped = []
    for m in matches:
        key = (m["company"], m["filed_at"], m["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)

    cache[sponsor_name] = deduped[:5]
    return cache[sponsor_name]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_dataset():
    today = date.today()
    upcoming_start = today.isoformat()
    upcoming_end = (today + timedelta(days=UPCOMING_WINDOW_DAYS)).isoformat()
    lookback_start = (today - timedelta(days=LOOKBACK_MAX_DAYS)).isoformat()
    lookback_end = (today - timedelta(days=LOOKBACK_MIN_DAYS)).isoformat()

    base_filter = "AREA[Phase]PHASE3 AND AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyType]INTERVENTIONAL"
    upcoming_query = f"{base_filter} AND AREA[PrimaryCompletionDate]RANGE[{upcoming_start},{upcoming_end}]"
    lookback_query = f"{base_filter} AND AREA[PrimaryCompletionDate]RANGE[{lookback_start},{lookback_end}]"

    print(f"Querying CT.gov: upcoming window {upcoming_start}..{upcoming_end}")
    upcoming_studies, upcoming_ok = search_studies(upcoming_query)
    print(f"  -> {len(upcoming_studies)} studies (complete={upcoming_ok})")

    print(f"Querying CT.gov: lookback window {lookback_start}..{lookback_end}")
    lookback_studies, lookback_ok = search_studies(lookback_query)
    print(f"  -> {len(lookback_studies)} studies (complete={lookback_ok})")

    trials = {}
    for study in upcoming_studies:
        t = parse_study(study, "upcoming")
        if t["nct_id"]:
            trials[t["nct_id"]] = t
    for study in lookback_studies:
        t = parse_study(study, "lookback")
        if t["nct_id"] and t["nct_id"] not in trials:
            trials[t["nct_id"]] = t

    edgar_cache = {}
    sponsors = sorted({t["sponsor"] for t in trials.values() if t["sponsor"]})
    print(f"Cross-referencing {len(sponsors)} sponsor(s) against SEC EDGAR full-text search...")
    for i, sponsor in enumerate(sponsors, 1):
        print(f"  [{i}/{len(sponsors)}] {sponsor}")
        matches = edgar_search_sponsor(sponsor, edgar_cache)
        for t in trials.values():
            if t["sponsor"] == sponsor:
                t["sec_8k_matches"] = matches
    for t in trials.values():
        t.setdefault("sec_8k_matches", [])

    dataset = {
        "generated_at": now_iso(),
        "window_upcoming_days": UPCOMING_WINDOW_DAYS,
        "window_lookback_days": [LOOKBACK_MIN_DAYS, LOOKBACK_MAX_DAYS],
        "trial_count": len(trials),
        "trials": sorted(trials.values(), key=lambda t: (t["primary_completion_date"] or "", t["nct_id"] or "")),
    }
    complete = upcoming_ok and lookback_ok
    return dataset, complete


def compute_diff(previous, current):
    prev_ids = {t["nct_id"] for t in (previous or {}).get("trials", [])}
    curr_ids = {t["nct_id"] for t in current["trials"]}
    new_trials = [t for t in current["trials"] if t["nct_id"] not in prev_ids]
    removed_ids = sorted(prev_ids - curr_ids)
    return {
        "generated_at": current["generated_at"],
        "new_trial_count": len(new_trials),
        "new_trials": new_trials,
        "removed_trial_ids": removed_ids,
    }


def prune_history(keep=HISTORY_RETENTION):
    files = sorted(HISTORY_DIR.glob("catalysts-*.json"))
    excess = len(files) - keep
    for f in files[:max(excess, 0)]:
        f.unlink()


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    previous = None
    if CATALYSTS_FILE.exists():
        try:
            previous = json.loads(CATALYSTS_FILE.read_text())
        except json.JSONDecodeError:
            previous = None

    current, complete = build_dataset()

    if not current["trials"] and previous and previous.get("trials"):
        print(
            "! CT.gov returned zero trials but a previous non-empty dataset exists -- "
            "this looks like an API outage, not a genuine empty result. Leaving existing "
            "data/catalysts.json untouched.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not complete:
        print("! One or more CT.gov query pages failed to fetch; dataset may be incomplete.", file=sys.stderr)

    CATALYSTS_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (HISTORY_DIR / f"catalysts-{ts}.json").write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    prune_history()

    diff = compute_diff(previous, current)
    NEW_SINCE_FILE.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n")

    print(f"Wrote {len(current['trials'])} trial(s) to {CATALYSTS_FILE}")
    print(f"New since last run: {diff['new_trial_count']}, removed: {len(diff['removed_trial_ids'])}")


if __name__ == "__main__":
    main()
