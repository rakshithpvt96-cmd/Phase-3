#!/usr/bin/env python3
"""Offline fixture tests for scripts/fetch_catalysts.py.

Monkeypatches the HTTP layer with a shape-accurate CT.gov API v2 study
payload and a fake SEC EDGAR full-text-search response, since this sandbox
cannot reach either host directly. Run with:
    python3 tests/test_fetch_fixtures.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_catalysts as fc  # noqa: E402

# Redirect the company-info cache to a scratch dir so running the test suite
# never writes into the real data/ tree.
fc.COMPANIES_DIR = Path(tempfile.mkdtemp(prefix="fetch-test-companies-"))

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


SAMPLE_STUDY = {
    "hasResults": False,
    "protocolSection": {
        "identificationModule": {"nctId": "NCT09999999", "briefTitle": "A Study of Drugimab in NSCLC"},
        "statusModule": {
            "overallStatus": "ACTIVE_NOT_RECRUITING",
            "primaryCompletionDateStruct": {"date": "2026-08-20", "type": "ESTIMATED"},
            "resultsFirstPostDateStruct": {},
        },
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Biopharma, Inc.", "class": "INDUSTRY"}},
        "conditionsModule": {"conditions": ["Non-Small Cell Lung Cancer"]},
        "designModule": {"phases": ["PHASE3"]},
        "armsInterventionsModule": {
            "interventions": [
                {"type": "DRUG", "name": "Drugimab"},
                {"type": "OTHER", "name": "Placebo"},
            ]
        },
    },
}


SAMPLE_STUDY_2 = {
    "hasResults": False,
    "protocolSection": {
        "identificationModule": {"nctId": "NCT08888888", "briefTitle": "A Study of Cashimab in Melanoma"},
        "statusModule": {
            "overallStatus": "RECRUITING",
            "primaryCompletionDateStruct": {"date": "2026-09-01", "type": "ESTIMATED"},
            "resultsFirstPostDateStruct": {},
        },
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Cash Flow Therapeutics Corp", "class": "INDUSTRY"}},
        "conditionsModule": {"conditions": ["Melanoma"]},
        "designModule": {"phases": ["PHASE3"]},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Cashimab"}]},
    },
}

SEC_TICKERS_RESPONSE = {
    "0": {"cik_str": 1234567, "ticker": "CFTX", "title": "Cash Flow Therapeutics Corp"},
}

XBRL_CALLS = []
STOOQ_CALLS = []


def fake_http_get_json(url, params=None, headers=None, retries=3):
    params = params or {}
    if url == fc.CTGOV_BASE:
        if "pageToken" in params:
            return {"studies": [], "nextPageToken": None}
        return {"studies": [SAMPLE_STUDY, SAMPLE_STUDY_2], "nextPageToken": None}
    if url == fc.EDGAR_FTS_BASE:
        if "topline" in params.get("q", ""):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "0001193125-26-000123:acme-8k.htm",
                            "_source": {
                                "root_form": "8-K",
                                "file_date": "2026-08-01",
                                "display_names": ["ACME BIOPHARMA, INC. (CIK 0001234567)"],
                                "ciks": ["0001234567"],
                            },
                        }
                    ]
                }
            }
        return {"hits": {"hits": []}}
    if url == fc.SEC_TICKERS_URL:
        return SEC_TICKERS_RESPONSE
    if url == f"{fc.SEC_XBRL_FACTS_BASE}/CIK0001234567.json":
        XBRL_CALLS.append(url)
        return {
            "facts": {
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [{"end": "2026-06-30", "val": 50_000_000, "filed": "2026-07-15"}]}
                    }
                }
            }
        }
    return None


def fake_http_get_text(url, params=None, headers=None, retries=3):
    params = params or {}
    if url == "https://stooq.com/q/l/" and params.get("s") == "cftx.us":
        STOOQ_CALLS.append(url)
        return "Symbol,Date,Time,Open,High,Low,Close,Volume\nCFTX.US,2026-08-15,21:00:00,10,10.5,9.8,10.20,1000000\n"
    return None


fc.http_get_json = fake_http_get_json
fc.http_get_text = fake_http_get_text


def run():
    parsed = fc.parse_study(SAMPLE_STUDY, "upcoming")
    check("parse_study: nct_id", parsed["nct_id"] == "NCT09999999")
    check("parse_study: sponsor", parsed["sponsor"] == "Acme Biopharma, Inc.")
    check("parse_study: sponsor_class", parsed["sponsor_class"] == "INDUSTRY")
    check("parse_study: primary_completion_date", parsed["primary_completion_date"] == "2026-08-20")
    check("parse_study: drug_names only includes DRUG/BIOLOGICAL", parsed["drug_names"] == ["Drugimab"])
    check("parse_study: conditions", parsed["conditions"] == ["Non-Small Cell Lung Cancer"])
    check("parse_study: url built from nct_id", parsed["url"] == "https://clinicaltrials.gov/study/NCT09999999")
    check("parse_study: has_results False", parsed["has_results"] is False)

    check(
        "normalize_sponsor strips legal suffix",
        fc.normalize_sponsor("Acme Biopharma, Inc.") == "Acme Biopharma",
    )
    check("normalize_sponsor handles None", fc.normalize_sponsor(None) == "")

    edgar_cache = {}
    matches = fc.edgar_search_sponsor("Acme Biopharma, Inc.", edgar_cache)
    check("edgar_search_sponsor: found a topline 8-K match", len(matches) == 1)
    check("edgar_search_sponsor: keyword recorded", matches[0]["keyword_matched"] == "topline")
    check(
        "edgar_search_sponsor: url built from cik + accession",
        matches[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1234567/000119312526000123/acme-8k.htm",
    )
    check("edgar_search_sponsor: caches by sponsor name", fc.edgar_search_sponsor("Acme Biopharma, Inc.", edgar_cache) is matches)

    check("canonical_key normalizes punctuation/case", fc.canonical_key("Cash Flow Therapeutics, Corp.") == "CASH FLOW THERAPEUTICS")
    check(
        "canonical_key matches differently-formatted SEC title",
        fc.canonical_key("Cash Flow Therapeutics Corp") == fc.canonical_key("Cash Flow Therapeutics, Corp."),
    )

    ticker_map = fc.load_sec_ticker_map()
    check("load_sec_ticker_map: parses the tickers file", "CASH FLOW THERAPEUTICS" in ticker_map)
    check("load_sec_ticker_map: cik zero-padded to 10 digits", ticker_map["CASH FLOW THERAPEUTICS"]["cik"] == "0001234567")

    check("format_market_cap: billions", fc.format_market_cap(4_200_000_000) == "$4.20B")
    check("format_market_cap: millions", fc.format_market_cap(850_000_000 * 0.1) == "$85.0M")
    check("format_market_cap: trillions", fc.format_market_cap(2_500_000_000_000) == "$2.50T")
    check("format_market_cap: None stays None", fc.format_market_cap(None) is None)

    matched = fc.resolve_company("Cash Flow Therapeutics Corp", ticker_map)
    check("resolve_company: matched ticker", matched["ticker"] == "CFTX")
    check("resolve_company: matched cik", matched["cik"] == "0001234567")
    check("resolve_company: market cap computed (50M shares x $10.20)", matched["market_cap_display"] == "$510.0M")
    check(
        "resolve_company: matched edgar_url points at the CIK",
        matched["edgar_url"] == "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001234567&type=10-K&dateb=&owner=include&count=40",
    )

    calls_before = (len(XBRL_CALLS), len(STOOQ_CALLS))
    matched_again = fc.resolve_company("Cash Flow Therapeutics Corp", ticker_map)
    check("resolve_company: cache hit avoids refetching XBRL/price", (len(XBRL_CALLS), len(STOOQ_CALLS)) == calls_before)
    check("resolve_company: cached result matches original", matched_again["market_cap_display"] == matched["market_cap_display"])

    unmatched = fc.resolve_company("Acme Biopharma, Inc.", ticker_map)
    check("resolve_company: unmatched sponsor has no ticker (never guessed)", unmatched["ticker"] is None)
    check("resolve_company: unmatched sponsor has no market cap (never guessed)", unmatched["market_cap_display"] is None)
    check(
        "resolve_company: unmatched sponsor still gets a name-search EDGAR fallback link",
        unmatched["edgar_url"].startswith("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company="),
    )

    dataset, complete = fc.build_dataset()
    check("build_dataset: complete flag True", complete is True)
    check("build_dataset: two deduped trials across both windows", dataset["trial_count"] == 2)
    check(
        "build_dataset: sec_8k_matches attached to the trial",
        len([t for t in dataset["trials"] if t["sponsor"] == "Acme Biopharma, Inc."][0]["sec_8k_matches"]) == 1,
    )
    cash_trial = [t for t in dataset["trials"] if t["sponsor"] == "Cash Flow Therapeutics Corp"][0]
    check("build_dataset: matched sponsor's trial carries company_info", cash_trial["company_info"]["ticker"] == "CFTX")
    acme_trial = [t for t in dataset["trials"] if t["sponsor"] == "Acme Biopharma, Inc."][0]
    check("build_dataset: unmatched sponsor's trial still carries a company_info block", acme_trial["company_info"] is not None)
    check("build_dataset: unmatched sponsor's company_info has no fabricated market cap", acme_trial["company_info"]["market_cap_display"] is None)

    previous = {"trials": []}
    diff = fc.compute_diff(previous, dataset)
    check("compute_diff: new trials detected against empty previous run", diff["new_trial_count"] == 2)
    diff2 = fc.compute_diff(dataset, dataset)
    check("compute_diff: no new trials against identical previous run", diff2["new_trial_count"] == 0)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    run()
