#!/usr/bin/env python3
"""Offline fixture tests for scripts/enrich_drugs.py.

These monkeypatch the HTTP layer with canned responses so the enrichment
logic (especially the "no FDA label -> null fields + PubMed further_reading,
never fabricate" rule) can be verified without live network access. Run
with: python3 tests/test_enrich_fixtures.py

Two drugs are exercised:
  - "Approvimab"    -- has a PubChem/RxNorm/ChEMBL/openFDA/DailyMed hit,
                       simulating an approved drug.
  - "Investigazumab" -- resolves nowhere except PubMed, simulating an
                       investigational/unapproved compound. Also exercises
                       the ChEMBL-has-no-target -> CT.gov same-condition
                       fallback for "related drugs".
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import enrich_drugs as ed  # noqa: E402

# Redirect the MedlinePlus condition cache to a scratch dir so running the
# test suite never writes into the real data/ tree.
_TMP_CACHE_DIR = Path(tempfile.mkdtemp(prefix="enrich-test-cache-"))
ed.CONDITIONS_CACHE_DIR = _TMP_CACHE_DIR

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# Fixture HTTP layer
# ---------------------------------------------------------------------------

MEDLINEPLUS_XML = """<?xml version="1.0"?>
<nlmSearchResult>
  <list>
    <document url="https://medlineplus.gov/example.html">
      <content name="title">Example Condition</content>
      <content name="FullSummary">&lt;p&gt;Example condition affects the lungs and causes cough.&lt;/p&gt;</content>
    </document>
  </list>
</nlmSearchResult>
"""


def fake_http_get_json(url, params=None, retries=3):
    params = params or {}

    if "pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" in url:
        if "Approvimab" in url:
            return {
                "PropertyTable": {
                    "Properties": [
                        {
                            "CID": 424242,
                            "IUPACName": "example iupac name",
                            "MolecularFormula": "C10H10N2O2",
                            "MolecularWeight": "218.2",
                            "CanonicalSMILES": "C1=CC=CC=C1",
                            "InChIKey": "ABCDEFGHIJKLMN-OPQRSTUVWX-Y",
                        }
                    ]
                }
            }
        return None  # Investigazumab: not in PubChem

    if url.endswith("/rxcui.json"):
        if params.get("name") == "Approvimab":
            return {"idGroup": {"rxnormId": ["111111"]}}
        return {"idGroup": {}}

    if url.endswith("/approximateTerm.json"):
        return {"approximateGroup": {"candidate": []}}

    if url.endswith("/properties.json"):
        return {"properties": {"name": "approvimab"}}

    if url.endswith("/related.json"):
        return {
            "relatedGroup": {
                "conceptGroup": [{"conceptProperties": [{"name": "Approvibrand"}]}]
            }
        }

    if url.endswith("/molecule/search"):
        if params.get("q") == "Approvimab":
            return {"molecules": [{"molecule_chembl_id": "CHEMBL999", "pref_name": "APPROVIMAB"}]}
        return {"molecules": []}

    if url.endswith("/molecule.json"):
        return {"molecules": []}

    if url.endswith("/mechanism"):
        if params.get("molecule_chembl_id") == "CHEMBL999":
            return {"mechanisms": [{"mechanism_of_action": "PD-1 inhibitor", "target_chembl_id": "CHEMBL2001"}]}
        if params.get("target_chembl_id") == "CHEMBL2001":
            return {"mechanisms": [{"molecule_chembl_id": "CHEMBL999"}, {"molecule_chembl_id": "CHEMBL888"}]}
        return {"mechanisms": []}

    if url.endswith("/target/CHEMBL2001.json"):
        return {"pref_name": "Programmed cell death protein 1"}

    if url.endswith("/molecule") and "molecule_chembl_id__in" in params:
        return {"molecules": [{"molecule_chembl_id": "CHEMBL888", "pref_name": "RIVALIMAB"}]}

    if url == "https://api.fda.gov/drug/label.json":
        search = params.get("search", "")
        if "Approvimab" in search and "generic_name" in search:
            return {
                "results": [
                    {
                        "mechanism_of_action": ["Approvimab binds PD-1 and blocks its interaction with PD-L1."],
                        "pharmacokinetics": ["Half-life is approximately 20 days."],
                        "indications_and_usage": ["Indicated for advanced solid tumors."],
                        "boxed_warning": ["Immune-mediated adverse reactions may occur."],
                        "openfda": {"brand_name": ["APPROVIMAB"], "generic_name": ["approvimab"]},
                    }
                ]
            }
        return {"results": []}

    if url.endswith("/spls.json"):
        if params.get("drug_name") == "Approvimab":
            return {"data": [{"setid": "SETID-1"}]}
        return {"data": []}

    if url.endswith("esearch.fcgi"):
        return {"esearchresult": {"idlist": ["1001", "1002"]}}

    if url.endswith("esummary.fcgi"):
        ids = params.get("id", "").split(",")
        result = {"uids": ids}
        for i in ids:
            result[i] = {"title": f"Study of drug in phase 3 trial (PMID {i})", "pubdate": "2026 Jul"}
        return {"result": result}

    return None


def fake_http_get_text(url, params=None, retries=3):
    if "wsearch.nlm.nih.gov" in url:
        return MEDLINEPLUS_XML
    return None


ed.http_get_json = fake_http_get_json
ed.http_get_text = fake_http_get_text
ed.CONDITIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Fixture catalysts data
# ---------------------------------------------------------------------------

TRIALS_INDEX = {
    "NCT00000001": {
        "nct_id": "NCT00000001",
        "sponsor": "Acme Biopharma",
        "conditions": ["Example Condition"],
        "drug_names": ["Approvimab"],
        "has_results": True,
        "company_info": {
            "ticker": "ACME",
            "market_cap_display": "$4.20B",
            "edgar_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001234567&type=10-K&dateb=&owner=include&count=40",
        },
    },
    "NCT00000002": {
        "nct_id": "NCT00000002",
        "sponsor": "Beta Therapeutics",
        "conditions": ["Example Condition"],
        "drug_names": ["Investigazumab"],
        "has_results": False,
        "company_info": {
            "ticker": None,
            "market_cap_display": None,
            "edgar_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=Beta+Therapeutics&type=10-K&dateb=&owner=include&count=40",
        },
    },
    "NCT00000003": {
        "nct_id": "NCT00000003",
        "sponsor": "Gamma Rx",
        "conditions": ["Example Condition"],
        "drug_names": ["Otherdrugimab"],
        "has_results": False,
    },
}


def run():
    approved = ed.enrich_drug(
        "approvimab", "Approvimab", "Example Condition", "Acme Biopharma", {"NCT00000001"}, TRIALS_INDEX
    )
    unapproved = ed.enrich_drug(
        "investigazumab", "Investigazumab", "Example Condition", "Beta Therapeutics", {"NCT00000002"}, TRIALS_INDEX
    )

    # --- approved drug: real label data, no fabrication guard needed ---
    check("approved: pubchem CID resolved", approved["identity"].get("pubchem_cid") == 424242)
    check("approved: rxnorm generic name resolved", approved["identity"]["rxnorm"]["generic_name"] == "approvimab")
    check("approved: chembl mechanism resolved", approved["mechanism"]["mechanism_of_action_chembl"] == "PD-1 inhibitor")
    check("approved: fda_label.has_label is True", approved["fda_label"]["has_label"] is True)
    check("approved: fda_label.source is openFDA", approved["fda_label"]["source"] == "openFDA")
    check(
        "approved: mechanism_of_action text present (not null)",
        approved["fda_label"]["mechanism_of_action"] is not None,
    )
    check("approved: pharmacokinetics text present (not null)", approved["fda_label"]["pharmacokinetics"] is not None)
    check("approved: further_reading is None (label exists)", approved["fda_label"]["further_reading"] is None)
    check(
        "approved: related_drugs uses chembl_same_target method",
        approved["related_drugs"]["method"] == "chembl_same_target",
    )
    check(
        "approved: related_drugs includes RIVALIMAB via shared target",
        any(i["name"] == "RIVALIMAB" for i in approved["related_drugs"]["items"]),
    )
    check("approved: ctgov results url populated (has_results=True)", len(approved["prior_results"]["ctgov_results_urls"]) == 1)
    check("approved: pubmed articles fetched", len(approved["prior_results"]["pubmed_articles"]) == 2)
    check("approved: condition summary cached", (ed.CONDITIONS_CACHE_DIR / "example-condition.json").exists())
    check("approved: sponsors carries ticker from trial's company_info", approved["sponsors"][0]["ticker"] == "ACME")
    check("approved: sponsors carries market_cap_display", approved["sponsors"][0]["market_cap_display"] == "$4.20B")
    check("approved: sponsors carries edgar_url", approved["sponsors"][0]["edgar_url"].startswith("https://www.sec.gov/"))

    # --- unapproved drug: THE critical no-fabrication contract ---
    check("unapproved: fda_label.has_label is False", unapproved["fda_label"]["has_label"] is False)
    check("unapproved: mechanism_of_action is null (not guessed)", unapproved["fda_label"]["mechanism_of_action"] is None)
    check("unapproved: pharmacokinetics is null (not guessed)", unapproved["fda_label"]["pharmacokinetics"] is None)
    check("unapproved: indications_and_usage is null (not guessed)", unapproved["fda_label"]["indications_and_usage"] is None)
    check("unapproved: boxed_warning is null (not guessed)", unapproved["fda_label"]["boxed_warning"] is None)
    check("unapproved: further_reading block is populated", unapproved["fda_label"]["further_reading"] is not None)
    fr = unapproved["fda_label"]["further_reading"] or {}
    check(
        "unapproved: further_reading mechanism_search is a working PubMed URL shape",
        fr.get("mechanism_search", "").startswith("https://pubmed.ncbi.nlm.nih.gov/?term=")
        and "Investigazumab" in fr.get("mechanism_search", ""),
    )
    check("unapproved: further_reading has pharmacokinetics_search", "pharmacokinetics" in fr.get("pharmacokinetics_search", ""))
    check("unapproved: further_reading has efficacy_search", "efficacy" in fr.get("efficacy_search", ""))
    check(
        "unapproved: related_drugs falls back to ctgov_same_condition (ChEMBL had nothing)",
        unapproved["related_drugs"]["method"] == "ctgov_same_condition",
    )
    check(
        "unapproved: fallback related drug pulled from same-condition trial with different sponsor",
        any(i["name"] == "Otherdrugimab" for i in unapproved["related_drugs"]["items"]),
    )
    check(
        "unapproved: fallback related drugs exclude same-sponsor trial's own drug",
        all(i["name"] != "Investigazumab" for i in unapproved["related_drugs"]["items"]),
    )
    check("unapproved: no ctgov results url (has_results=False)", unapproved["prior_results"]["ctgov_results_urls"] == [])
    check("unapproved: sponsor with no matched ticker stays null (not guessed)", unapproved["sponsors"][0]["ticker"] is None)
    check("unapproved: sponsor market cap stays null (not guessed)", unapproved["sponsors"][0]["market_cap_display"] is None)
    check("unapproved: sponsor still gets an edgar_url fallback link", unapproved["sponsors"][0]["edgar_url"] is not None)

    # --- slugify + refresh helpers ---
    check("slugify basic", ed.slugify("Investigazumab (INV-101)") == "investigazumab-inv-101")
    check("slugify empty -> unknown", ed.slugify("") == "unknown")
    check("needs_refresh: missing profile always refreshes", ed.needs_refresh(None, 30) is True)
    check(
        "needs_refresh: fresh profile within window is skipped",
        ed.needs_refresh({"enriched_at": ed.now_iso()}, 30) is False,
    )
    check(
        "needs_refresh: stale profile triggers refresh",
        ed.needs_refresh({"enriched_at": "2000-01-01T00:00:00Z"}, 30) is True,
    )

    # --- ACTIVE_NOT_RECRUITING drugs jump the enrichment queue ---
    priority_trials = {
        "NCT1": {"status": "ACTIVE_NOT_RECRUITING"},
        "NCT2": {"status": "RECRUITING"},
        "NCT3": {"status": "ACTIVE_NOT_RECRUITING"},
    }
    priority_drugs = {
        "zeta": {"trial_ids": {"NCT2"}},
        "alpha": {"trial_ids": {"NCT1"}},
        "mid": {"trial_ids": {"NCT3"}},
    }
    ordered_slugs = [
        s for s, _ in sorted(priority_drugs.items(), key=lambda si: ed.drug_priority_key(si[0], si[1], priority_trials))
    ]
    check(
        "drug_priority_key: ACTIVE_NOT_RECRUITING drugs sort before others, alphabetical within each tier",
        ordered_slugs == ["alpha", "mid", "zeta"],
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="enrich-test-main-"))
    orig_drugs_dir, orig_conditions_dir, orig_catalysts_file, orig_max = (
        ed.DRUGS_DIR,
        ed.CONDITIONS_CACHE_DIR,
        ed.CATALYSTS_FILE,
        ed.MAX_DRUGS_PER_RUN,
    )
    ed.DRUGS_DIR = tmp_dir / "drugs"
    ed.CONDITIONS_CACHE_DIR = tmp_dir / "drugs" / "_conditions_cache"
    ed.CATALYSTS_FILE = tmp_dir / "catalysts.json"
    ed.MAX_DRUGS_PER_RUN = 2
    ed.CATALYSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ed.CATALYSTS_FILE.write_text(
        json.dumps(
            {
                "trials": [
                    {"nct_id": "NCT1", "status": "ACTIVE_NOT_RECRUITING", "sponsor": "S1", "conditions": ["C"], "drug_names": ["Zdrug"], "has_results": False, "title": "T1", "url": "u1"},
                    {"nct_id": "NCT2", "status": "RECRUITING", "sponsor": "S2", "conditions": ["C"], "drug_names": ["Adrug"], "has_results": False, "title": "T2", "url": "u2"},
                    {"nct_id": "NCT3", "status": "COMPLETED", "sponsor": "S3", "conditions": ["C"], "drug_names": ["Mdrug"], "has_results": False, "title": "T3", "url": "u3"},
                ]
            }
        )
    )

    call_order = []
    orig_enrich_drug = ed.enrich_drug

    def stub_enrich_drug(slug, name, condition, sponsor, trial_ids, trials_index):
        call_order.append(slug)
        return {
            "drug_name": name, "slug": slug, "enriched_at": ed.now_iso(), "source_trial_ids": sorted(trial_ids),
            "sponsors": [], "identity": {}, "mechanism": {}, "fda_label": {"has_label": False},
            "condition": {"raw": condition, "slug": None}, "source_trials": [],
            "prior_results": {"ctgov_results_urls": [], "pubmed_articles": []},
            "related_drugs": {"method": "x", "label": "x", "items": []},
        }

    ed.enrich_drug = stub_enrich_drug
    try:
        ed.main()
    finally:
        ed.enrich_drug = orig_enrich_drug
        ed.DRUGS_DIR, ed.CONDITIONS_CACHE_DIR, ed.CATALYSTS_FILE, ed.MAX_DRUGS_PER_RUN = (
            orig_drugs_dir,
            orig_conditions_dir,
            orig_catalysts_file,
            orig_max,
        )

    check("main(): ACTIVE_NOT_RECRUITING drug processed first despite alphabetical order", call_order[:1] == ["zdrug"])
    check("main(): MAX_DRUGS_PER_RUN caps candidates processed this run", len(call_order) == 2)
    check("main(): remaining slot goes to next-priority drug in alphabetical order", call_order == ["zdrug", "adrug"])

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
