#!/usr/bin/env python3
"""Drug enrichment layer for the Phase 3 catalyst tracker.

Reads data/catalysts.json, and for every unique drug name found across its
trials, builds/updates a profile at data/drugs/{slug}.json by querying (in
order) PubChem, RxNorm, ChEMBL, openFDA + DailyMed, PubMed, and MedlinePlus.

Hard rule: mechanism, pharmacokinetics and efficacy text are NEVER
fabricated for a drug that has no approved FDA label. When no label
exists those fields are set to null and a "further_reading" block with
pre-built PubMed search links is populated instead.

This script does not import fetch_catalysts.py (and vice versa) so either
one can be changed independently -- they only share the on-disk JSON
contract in data/catalysts.json and data/drugs/*.json.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CATALYSTS_FILE = DATA_DIR / "catalysts.json"
DRUGS_DIR = DATA_DIR / "drugs"
CONDITIONS_CACHE_DIR = DRUGS_DIR / "_conditions_cache"

# Set CONTACT_EMAIL in the workflow/repo secrets (or env locally) so calls
# to NCBI/SEC-style "please identify yourself" APIs carry a real contact.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "your-email@example.com")
USER_AGENT = f"biotech-phase3-catalyst-tracker/1.0 (contact: {CONTACT_EMAIL})"

# Optional, free, registered API keys that raise rate limits. Never required.
#   NCBI_API_KEY  -> https://www.ncbi.nlm.nih.gov/account/  (3/s -> 10/s on E-utilities)
#   FDA_API_KEY   -> https://open.fda.gov/apis/authentication/ (240/min -> 240/min per key)
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
FDA_API_KEY = os.environ.get("FDA_API_KEY", "").strip()

REFRESH_DAYS_APPROVED = int(os.environ.get("REFRESH_DAYS_APPROVED", "30"))
REFRESH_DAYS_UNAPPROVED = int(os.environ.get("REFRESH_DAYS_UNAPPROVED", "7"))
MAX_DRUGS_PER_RUN = int(os.environ.get("MAX_DRUGS_PER_RUN", "40"))

REQUEST_TIMEOUT = 20
RETRY_COUNT = 3
SLEEP_BETWEEN_CALLS = float(os.environ.get("ENRICH_SLEEP_SECONDS", "0.4"))

PUBMED_SEARCH_BASE = "https://pubmed.ncbi.nlm.nih.gov/?term="


# ---------------------------------------------------------------------------
# HTTP helpers (polite: descriptive UA, retries with backoff, rate-limited)
# ---------------------------------------------------------------------------

def _sleep():
    time.sleep(SLEEP_BETWEEN_CALLS)


def http_get_json(url, params=None, retries=RETRY_COUNT):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read()
            _sleep()
            if not body:
                return None
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _sleep()
                return None
            last_err = e
            if e.code == 429 or e.code >= 500:
                time.sleep(SLEEP_BETWEEN_CALLS * (2 ** attempt))
                continue
            _sleep()
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(SLEEP_BETWEEN_CALLS * (2 ** attempt))
    print(f"  ! request failed after {retries} attempts: {url} ({last_err})", file=sys.stderr)
    return None


def http_get_text(url, params=None, retries=RETRY_COUNT):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": USER_AGENT}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            _sleep()
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _sleep()
                return None
            last_err = e
            time.sleep(SLEEP_BETWEEN_CALLS * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(SLEEP_BETWEEN_CALLS * (2 ** attempt))
    print(f"  ! text request failed after {retries} attempts: {url} ({last_err})", file=sys.stderr)
    return None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(name):
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "unknown"


# ---------------------------------------------------------------------------
# a) PubChem PUG REST
# ---------------------------------------------------------------------------

def pubchem_lookup(name):
    encoded = urllib.parse.quote(name, safe="")
    props = "IUPACName,MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey"
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/property/{props}/JSON"
    data = http_get_json(url)
    try:
        prop = data["PropertyTable"]["Properties"][0]
    except (KeyError, IndexError, TypeError):
        return None
    return {
        "pubchem_cid": prop.get("CID"),
        "iupac_name": prop.get("IUPACName"),
        "molecular_formula": prop.get("MolecularFormula"),
        "molecular_weight": prop.get("MolecularWeight"),
        "canonical_smiles": prop.get("CanonicalSMILES"),
        "inchikey": prop.get("InChIKey"),
    }


# ---------------------------------------------------------------------------
# b) RxNorm (NLM)
# ---------------------------------------------------------------------------

def rxnorm_lookup(name):
    data = http_get_json("https://rxnav.nlm.nih.gov/REST/rxcui.json", params={"name": name, "search": "2"})
    rxcui = None
    try:
        ids = data["idGroup"].get("rxnormId")
        if ids:
            rxcui = ids[0]
    except (KeyError, TypeError, AttributeError):
        pass

    if not rxcui:
        data = http_get_json(
            "https://rxnav.nlm.nih.gov/REST/approximateTerm.json", params={"term": name, "maxEntries": "1"}
        )
        try:
            candidates = data["approximateGroup"].get("candidate")
            if candidates:
                rxcui = candidates[0].get("rxcui")
        except (KeyError, TypeError, AttributeError):
            pass

    if not rxcui:
        return None

    result = {"rxcui": rxcui, "generic_name": None, "brand_names": []}

    props = http_get_json(f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json")
    try:
        result["generic_name"] = props["properties"]["name"]
    except (KeyError, TypeError):
        pass

    related = http_get_json(
        f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/related.json", params={"tty": "SBD+BN"}
    )
    brand_names = set()
    try:
        for group in related["relatedGroup"].get("conceptGroup") or []:
            for prop in group.get("conceptProperties", []) or []:
                nm = prop.get("name")
                if nm:
                    brand_names.add(nm)
    except (KeyError, TypeError):
        pass
    result["brand_names"] = sorted(brand_names)[:10]
    return result


# ---------------------------------------------------------------------------
# c) ChEMBL -- mechanism/target + target-sharing "competitor" drugs
# ---------------------------------------------------------------------------

def chembl_lookup(name):
    molecule_chembl_id = None
    pref_name = None

    data = http_get_json("https://www.ebi.ac.uk/chembl/api/data/molecule/search", params={"q": name, "format": "json"})
    try:
        mols = (data or {}).get("molecules") or []
        if mols:
            molecule_chembl_id = mols[0].get("molecule_chembl_id")
            pref_name = mols[0].get("pref_name")
    except AttributeError:
        pass

    if not molecule_chembl_id:
        data = http_get_json(
            "https://www.ebi.ac.uk/chembl/api/data/molecule.json", params={"pref_name__iexact": name}
        )
        try:
            mols = (data or {}).get("molecules") or []
            if mols:
                molecule_chembl_id = mols[0].get("molecule_chembl_id")
                pref_name = mols[0].get("pref_name")
        except AttributeError:
            pass

    if not molecule_chembl_id:
        return None

    result = {
        "chembl_id": molecule_chembl_id,
        "pref_name": pref_name,
        "target_chembl_id": None,
        "target_name": None,
        "mechanism_of_action": None,
        "related_targets_drugs": [],
    }

    mech = http_get_json(
        "https://www.ebi.ac.uk/chembl/api/data/mechanism",
        params={"molecule_chembl_id": molecule_chembl_id, "format": "json"},
    )
    target_chembl_id = None
    try:
        mechanisms = (mech or {}).get("mechanisms") or []
        if mechanisms:
            m0 = mechanisms[0]
            result["mechanism_of_action"] = m0.get("mechanism_of_action")
            target_chembl_id = m0.get("target_chembl_id")
    except AttributeError:
        pass

    if not target_chembl_id:
        return result

    result["target_chembl_id"] = target_chembl_id
    target = http_get_json(f"https://www.ebi.ac.uk/chembl/api/data/target/{target_chembl_id}.json")
    if target:
        result["target_name"] = target.get("pref_name")

    other = http_get_json(
        "https://www.ebi.ac.uk/chembl/api/data/mechanism",
        params={"target_chembl_id": target_chembl_id, "format": "json", "limit": "25"},
    )
    competitor_ids = []
    seen = {molecule_chembl_id}
    try:
        for m in (other or {}).get("mechanisms") or []:
            mid = m.get("molecule_chembl_id")
            if mid and mid not in seen:
                seen.add(mid)
                competitor_ids.append(mid)
    except AttributeError:
        pass
    competitor_ids = competitor_ids[:10]

    competitors = []
    if competitor_ids:
        mols = http_get_json(
            "https://www.ebi.ac.uk/chembl/api/data/molecule",
            params={"molecule_chembl_id__in": ",".join(competitor_ids), "format": "json"},
        )
        name_by_id = {}
        try:
            for mol in (mols or {}).get("molecules") or []:
                name_by_id[mol.get("molecule_chembl_id")] = mol.get("pref_name")
        except AttributeError:
            pass
        target_label = result["target_name"] or target_chembl_id
        for mid in competitor_ids:
            competitors.append(
                {"name": name_by_id.get(mid) or mid, "chembl_id": mid, "reason": f"same target: {target_label}"}
            )

    result["related_targets_drugs"] = competitors
    return result


def ctgov_fallback_competitors(drug_name, condition, sponsor, trials):
    """Used only when ChEMBL has no target/mechanism data for this drug."""
    items = []
    seen = set()
    cond_norm = (condition or "").strip().lower()
    for t in trials.values():
        if not cond_norm:
            break
        if t.get("sponsor") == sponsor:
            continue
        conds = [(c or "").strip().lower() for c in t.get("conditions", []) or []]
        if cond_norm not in conds:
            continue
        for dn in t.get("drug_names", []) or []:
            if dn.lower() == drug_name.lower():
                continue
            key = dn.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({"name": dn, "chembl_id": None, "reason": f"same indication trial, sponsor: {t.get('sponsor')}"})
    return items[:8]


# ---------------------------------------------------------------------------
# d) openFDA drug label + DailyMed SPL (approved drugs only; else -> null +
#    further_reading PubMed links, never fabricated text)
# ---------------------------------------------------------------------------

def pubmed_further_reading(name):
    q = urllib.parse.quote(f"{name}")
    return {
        "mechanism_search": f"{PUBMED_SEARCH_BASE}{q}+mechanism+of+action",
        "pharmacokinetics_search": f"{PUBMED_SEARCH_BASE}{q}+pharmacokinetics",
        "efficacy_search": f"{PUBMED_SEARCH_BASE}{q}+efficacy+phase+3",
    }


def openfda_label_lookup(name):
    safe_name = name.replace('"', "'")
    for field in ("openfda.generic_name", "openfda.brand_name", "openfda.substance_name"):
        params = {"search": f'{field}:"{safe_name}"', "limit": "1"}
        if FDA_API_KEY:
            params["api_key"] = FDA_API_KEY
        data = http_get_json("https://api.fda.gov/drug/label.json", params=params)
        results = (data or {}).get("results") if isinstance(data, dict) else None
        if results:
            r = results[0]
            openfda = r.get("openfda", {}) or {}

            def first(key):
                v = r.get(key)
                return v[0] if isinstance(v, list) and v else None

            return {
                "has_label": True,
                "source": "openFDA",
                "mechanism_of_action": first("mechanism_of_action"),
                "pharmacokinetics": first("pharmacokinetics"),
                "indications_and_usage": first("indications_and_usage"),
                "boxed_warning": first("boxed_warning"),
                "openfda_brand_names": openfda.get("brand_name", []),
                "openfda_generic_names": openfda.get("generic_name", []),
            }
    return None


def dailymed_lookup(name):
    data = http_get_json(
        "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json", params={"drug_name": name, "pagesize": "1"}
    )
    entries = (data or {}).get("data") or []
    if not entries:
        return None
    setid = entries[0].get("setid")
    if not setid:
        return None
    return {"setid": setid, "dailymed_url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"}


def build_fda_label(name):
    label = openfda_label_lookup(name)
    dailymed = dailymed_lookup(name)

    if label:
        label["dailymed_url"] = (dailymed or {}).get("dailymed_url")
        label["note"] = None
        label["further_reading"] = None
        return label

    if dailymed:
        # A DailyMed SPL exists (the drug is marketed) but openFDA didn't
        # return parsed section text. Link to the real label instead of
        # guessing at its contents.
        return {
            "has_label": True,
            "source": "DailyMed",
            "mechanism_of_action": None,
            "pharmacokinetics": None,
            "indications_and_usage": None,
            "boxed_warning": None,
            "openfda_brand_names": [],
            "openfda_generic_names": [],
            "dailymed_url": dailymed["dailymed_url"],
            "note": "A label exists in DailyMed but structured sections could not be parsed automatically -- see dailymed_url for the full label.",
            "further_reading": pubmed_further_reading(name),
        }

    return {
        "has_label": False,
        "source": None,
        "mechanism_of_action": None,
        "pharmacokinetics": None,
        "indications_and_usage": None,
        "boxed_warning": None,
        "openfda_brand_names": [],
        "openfda_generic_names": [],
        "dailymed_url": None,
        "note": "No FDA label found -- likely investigational/unapproved. Data intentionally left blank rather than guessed.",
        "further_reading": pubmed_further_reading(name),
    }


# ---------------------------------------------------------------------------
# e) PubMed E-utilities
# ---------------------------------------------------------------------------

def pubmed_search(name, retmax=5):
    term = f'({name}) AND ("phase 3"[Title/Abstract] OR "clinical trial"[Title/Abstract])'
    params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": str(retmax), "sort": "date"}
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    search = http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
    try:
        ids = search["esearchresult"]["idlist"]
    except (KeyError, TypeError):
        ids = []
    if not ids:
        return []

    sum_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
    if NCBI_API_KEY:
        sum_params["api_key"] = NCBI_API_KEY
    summary = http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", params=sum_params)

    articles = []
    try:
        result = summary["result"]
        for pmid in result.get("uids", ids):
            item = result.get(pmid) or {}
            articles.append(
                {
                    "pmid": pmid,
                    "title": item.get("title"),
                    "pubdate": item.get("pubdate"),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                }
            )
    except (KeyError, TypeError):
        pass
    return articles


# ---------------------------------------------------------------------------
# f) MedlinePlus -- plain-language condition summary, cached once per
#    condition (not per drug)
# ---------------------------------------------------------------------------

def medlineplus_condition_summary(condition):
    slug = slugify(condition)
    cache_file = CONDITIONS_CACHE_DIR / f"{slug}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            pass

    xml_text = http_get_text("https://wsearch.nlm.nih.gov/ws/query", params={"db": "healthTopics", "term": condition, "retmax": "1"})
    result = {"condition": condition, "title": None, "summary": None, "url": None, "cached_at": now_iso()}
    if xml_text:
        try:
            root = ET.fromstring(xml_text)
            doc = root.find(".//document")
            if doc is not None:
                result["url"] = doc.get("url")
                for content in doc.findall("content"):
                    text = "".join(content.itertext())
                    text = re.sub(r"\s+", " ", text).strip()
                    if content.get("name") == "title":
                        result["title"] = re.sub(r"<[^>]+>", "", text)
                    elif content.get("name") == "FullSummary":
                        result["summary"] = re.sub(r"<[^>]+>", "", text)[:1200]
        except ET.ParseError:
            pass

    CONDITIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def load_catalysts():
    if not CATALYSTS_FILE.exists():
        print(f"No {CATALYSTS_FILE} found -- run fetch_catalysts.py first.", file=sys.stderr)
        return {"trials": []}
    return json.loads(CATALYSTS_FILE.read_text())


def collect_unique_drugs(catalysts):
    drugs = {}
    for t in catalysts.get("trials", []):
        conds = t.get("conditions") or []
        cond = conds[0] if conds else None
        for dn in t.get("drug_names", []) or []:
            slug = slugify(dn)
            entry = drugs.setdefault(slug, {"name": dn, "trial_ids": set(), "condition": cond, "sponsor": t.get("sponsor")})
            entry["trial_ids"].add(t.get("nct_id"))
            if not entry["condition"]:
                entry["condition"] = cond
    return drugs


def needs_refresh(existing, refresh_days):
    if existing is None:
        return True
    enriched_at = existing.get("enriched_at")
    if not enriched_at:
        return True
    try:
        ts = datetime.strptime(enriched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - ts) > timedelta(days=refresh_days)


def enrich_drug(slug, name, condition, sponsor, trial_ids, trials_index):
    print(f"Enriching {name} ({slug})...")

    identity = pubchem_lookup(name) or {}
    identity["rxnorm"] = rxnorm_lookup(name)

    chembl = chembl_lookup(name)
    mechanism = {
        "chembl_id": (chembl or {}).get("chembl_id"),
        "target_chembl_id": (chembl or {}).get("target_chembl_id"),
        "target_name": (chembl or {}).get("target_name"),
        "mechanism_of_action_chembl": (chembl or {}).get("mechanism_of_action"),
    }

    fda_label = build_fda_label(name)

    if chembl and chembl.get("related_targets_drugs"):
        related = {
            "method": "chembl_same_target",
            "label": "Drugs with related mechanism or same indication -- not a curated competitive analysis",
            "items": chembl["related_targets_drugs"],
        }
    else:
        related = {
            "method": "ctgov_same_condition",
            "label": "Drugs with related mechanism or same indication -- not a curated competitive analysis",
            "items": ctgov_fallback_competitors(name, condition, sponsor, trials_index),
        }

    pubmed_articles = pubmed_search(name)

    condition_slug = None
    if condition:
        condition_slug = slugify(condition)
        medlineplus_condition_summary(condition)  # cached as a side effect

    results_urls = []
    source_trials = []
    sponsors_by_name = {}
    for tid in sorted(trial_ids):
        trial = trials_index.get(tid)
        if not trial:
            continue
        if trial.get("has_results"):
            results_urls.append(f"https://clinicaltrials.gov/study/{tid}?tab=results")
        sponsor_name = trial.get("sponsor")
        if sponsor_name and sponsor_name not in sponsors_by_name:
            company_info = trial.get("company_info") or {}
            sponsors_by_name[sponsor_name] = {
                "name": sponsor_name,
                "ticker": company_info.get("ticker"),
                "market_cap_display": company_info.get("market_cap_display"),
                "edgar_url": company_info.get("edgar_url"),
            }
        source_trials.append(
            {
                "nct_id": tid,
                "title": trial.get("title"),
                "status": trial.get("status"),
                "url": trial.get("url"),
                "has_results": bool(trial.get("has_results")),
            }
        )
    sponsors = [sponsors_by_name[n] for n in sorted(sponsors_by_name)]

    return {
        "drug_name": name,
        "slug": slug,
        "enriched_at": now_iso(),
        "source_trial_ids": sorted(trial_ids),
        "sponsors": sponsors,
        "identity": identity,
        "mechanism": mechanism,
        "fda_label": fda_label,
        "condition": {"raw": condition, "slug": condition_slug},
        "source_trials": source_trials,
        "prior_results": {"ctgov_results_urls": results_urls, "pubmed_articles": pubmed_articles},
        "related_drugs": related,
    }


def main():
    DRUGS_DIR.mkdir(parents=True, exist_ok=True)
    CONDITIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    catalysts = load_catalysts()
    trials_index = {t.get("nct_id"): t for t in catalysts.get("trials", [])}
    drugs = collect_unique_drugs(catalysts)

    processed = 0
    skipped = 0
    for slug, info in sorted(drugs.items()):
        if processed >= MAX_DRUGS_PER_RUN:
            print(f"Reached MAX_DRUGS_PER_RUN={MAX_DRUGS_PER_RUN}; remaining drugs will be picked up next run.")
            break

        out_file = DRUGS_DIR / f"{slug}.json"
        existing = None
        if out_file.exists():
            try:
                existing = json.loads(out_file.read_text())
            except json.JSONDecodeError:
                existing = None

        has_label = bool((existing or {}).get("fda_label", {}).get("has_label")) if existing else False
        refresh_days = REFRESH_DAYS_APPROVED if has_label else REFRESH_DAYS_UNAPPROVED
        has_new_trials = bool(existing) and not info["trial_ids"].issubset(set(existing.get("source_trial_ids", [])))

        if existing and not needs_refresh(existing, refresh_days) and not has_new_trials:
            skipped += 1
            continue

        profile = enrich_drug(slug, info["name"], info["condition"], info["sponsor"], info["trial_ids"], trials_index)
        out_file.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
        processed += 1

    print(f"Enrichment complete: {processed} drug(s) processed, {skipped} skipped (cached).")


if __name__ == "__main__":
    main()
