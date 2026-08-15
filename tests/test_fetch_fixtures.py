#!/usr/bin/env python3
"""Offline fixture tests for scripts/fetch_catalysts.py.

Monkeypatches the HTTP layer with a shape-accurate CT.gov API v2 study
payload and a fake SEC EDGAR full-text-search response, since this sandbox
cannot reach either host directly. Run with:
    python3 tests/test_fetch_fixtures.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_catalysts as fc  # noqa: E402

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


def fake_http_get_json(url, params=None, headers=None, retries=3):
    params = params or {}
    if url == fc.CTGOV_BASE:
        if "pageToken" in params:
            return {"studies": [], "nextPageToken": None}
        return {"studies": [SAMPLE_STUDY], "nextPageToken": None}
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
    return None


fc.http_get_json = fake_http_get_json


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

    dataset, complete = fc.build_dataset()
    check("build_dataset: complete flag True", complete is True)
    check("build_dataset: one deduped trial across both windows", dataset["trial_count"] == 1)
    check(
        "build_dataset: sec_8k_matches attached to the trial",
        len(dataset["trials"][0]["sec_8k_matches"]) == 1,
    )

    previous = {"trials": []}
    diff = fc.compute_diff(previous, dataset)
    check("compute_diff: new trial detected against empty previous run", diff["new_trial_count"] == 1)
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
