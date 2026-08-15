(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function val(x) {
    return x === null || x === undefined || x === "" ? "—" : escapeHtml(x);
  }

  function fmtList(arr) {
    return arr && arr.length ? arr.join(", ") : null;
  }

  function qs(name) {
    return new URLSearchParams(location.search).get(name);
  }

  function sectionTag(confirmed) {
    return confirmed
      ? '<span class="source-tag confirmed">Confirmed FDA label</span>'
      : '<span class="source-tag literature-only">Literature only</span>';
  }

  function renderIdentity(drug) {
    const id = drug.identity || {};
    const rx = id.rxnorm || {};
    return `
      <section class="section">
        <h2>Identity</h2>
        <dl class="kv">
          <dt>Drug name (as in trial)</dt><dd>${val(drug.drug_name)}</dd>
          <dt>RxNorm generic name</dt><dd>${val(rx.generic_name)}</dd>
          <dt>Brand name(s)</dt><dd>${val(fmtList(rx.brand_names))}</dd>
          <dt>Molecular formula</dt><dd>${val(id.molecular_formula)}</dd>
          <dt>Molecular weight</dt><dd>${id.molecular_weight ? `${escapeHtml(id.molecular_weight)} g/mol` : "—"}</dd>
          <dt>IUPAC name</dt><dd>${val(id.iupac_name)}</dd>
          <dt>PubChem CID</dt><dd>${
            id.pubchem_cid
              ? `<a href="https://pubchem.ncbi.nlm.nih.gov/compound/${id.pubchem_cid}" target="_blank" rel="noopener">${id.pubchem_cid}</a>`
              : "—"
          }</dd>
          <dt>Sponsor(s)</dt><dd>${val(fmtList(drug.sponsors))}</dd>
        </dl>
      </section>`;
  }

  function renderIndication(drug, conditionSummary) {
    const cond = drug.condition || {};
    const hasSummary = conditionSummary && conditionSummary.summary;
    return `
      <section class="section">
        <h2>Indication &amp; Disease Overview</h2>
        <dl class="kv"><dt>Indication (from trial)</dt><dd>${val(cond.raw)}</dd></dl>
        ${
          hasSummary
            ? `<div class="field-block">
                 <h3>Plain-language overview (MedlinePlus)</h3>
                 <p>${escapeHtml(conditionSummary.summary)}</p>
                 ${conditionSummary.url ? `<p><a href="${escapeHtml(conditionSummary.url)}" target="_blank" rel="noopener">Read more on MedlinePlus →</a></p>` : ""}
               </div>`
            : `<p class="disclaimer">No MedlinePlus summary cached yet for this condition.</p>`
        }
      </section>`;
  }

  function renderMechanism(drug) {
    const label = drug.fda_label || {};
    const mech = drug.mechanism || {};
    const confirmed = !!label.mechanism_of_action;
    const fr = label.further_reading || {};
    const chemblBlock =
      mech.mechanism_of_action_chembl || mech.target_name
        ? `<div class="field-block">
             <h3>ChEMBL research annotation (not an FDA-confirmed label)</h3>
             <p>${mech.mechanism_of_action_chembl ? escapeHtml(mech.mechanism_of_action_chembl) : "No mechanism text on file."}${
             mech.target_name ? ` Target: ${escapeHtml(mech.target_name)}.` : ""
           }</p>
           </div>`
        : "";
    return `
      <section class="section ${confirmed ? "confirmed" : "literature-only"}">
        <h2>Mechanism of Action ${sectionTag(confirmed)}</h2>
        ${
          confirmed
            ? `<p>${escapeHtml(label.mechanism_of_action)}</p>
               <p class="disclaimer">Source: FDA-approved label, Section 12.1 (${escapeHtml(label.source)}).</p>`
            : `<p><strong>Not yet available</strong> — no FDA-approved label exists for this drug yet.</p>
               ${fr.mechanism_search ? `<p><a href="${escapeHtml(fr.mechanism_search)}" target="_blank" rel="noopener">Search PubMed for mechanism-of-action literature →</a></p>` : ""}`
        }
        ${chemblBlock}
      </section>`;
  }

  function renderPK(drug) {
    const label = drug.fda_label || {};
    const confirmed = !!label.pharmacokinetics;
    const fr = label.further_reading || {};
    return `
      <section class="section ${confirmed ? "confirmed" : "literature-only"}">
        <h2>Pharmacokinetics ${sectionTag(confirmed)}</h2>
        ${
          confirmed
            ? `<p>${escapeHtml(label.pharmacokinetics)}</p>
               <p class="disclaimer">Source: FDA-approved label, Section 12.3 (${escapeHtml(label.source)}).</p>`
            : `<p><strong>Not yet available</strong> — no FDA-approved label exists for this drug yet.</p>
               ${fr.pharmacokinetics_search ? `<p><a href="${escapeHtml(fr.pharmacokinetics_search)}" target="_blank" rel="noopener">Search PubMed for pharmacokinetics literature →</a></p>` : ""}`
        }
      </section>`;
  }

  function renderLabelExtras(drug) {
    const label = drug.fda_label || {};
    if (!label.has_label) {
      return `
        <section class="section literature-only">
          <h2>Indications &amp; Safety ${sectionTag(false)}</h2>
          <p><strong>Not yet available</strong> — no FDA-approved label exists for this drug yet.</p>
          ${label.further_reading && label.further_reading.efficacy_search ? `<p><a href="${escapeHtml(label.further_reading.efficacy_search)}" target="_blank" rel="noopener">Search PubMed for efficacy literature →</a></p>` : ""}
        </section>`;
    }
    const confirmedIndications = !!label.indications_and_usage;
    const confirmedWarning = !!label.boxed_warning;
    return `
      <section class="section confirmed">
        <h2>Indications &amp; Safety ${sectionTag(true)}</h2>
        <div class="field-block">
          <h3>Indications and Usage</h3>
          <p>${confirmedIndications ? escapeHtml(label.indications_and_usage) : "—"}</p>
        </div>
        <div class="field-block">
          <h3>Boxed Warning</h3>
          <p>${confirmedWarning ? escapeHtml(label.boxed_warning) : "None on file."}</p>
        </div>
        ${label.note ? `<p class="disclaimer">${escapeHtml(label.note)}</p>` : ""}
        ${label.dailymed_url ? `<p><a href="${escapeHtml(label.dailymed_url)}" target="_blank" rel="noopener">View full label on DailyMed →</a></p>` : ""}
      </section>`;
  }

  function renderPriorResults(drug) {
    const trials = drug.source_trials || [];
    const articles = (drug.prior_results || {}).pubmed_articles || [];
    return `
      <section class="section">
        <h2>Prior Trial Results</h2>
        ${
          trials.length
            ? `<ul class="pill-list">${trials
                .map(
                  (t) => `<li>
                    <a href="${escapeHtml(t.has_results ? t.url + "?tab=results" : t.url)}" target="_blank" rel="noopener">${escapeHtml(t.nct_id)}</a>
                    — ${escapeHtml(t.title || "")}
                    <div class="pill-meta">${escapeHtml(t.status || "")}${t.has_results ? " · Results posted on CT.gov" : " · No results posted yet"}</div>
                  </li>`
                )
                .join("")}</ul>`
            : '<p class="disclaimer">No source trials on file.</p>'
        }
        <div class="field-block">
          <h3>Related PubMed abstracts</h3>
          ${
            articles.length
              ? `<ul class="pill-list">${articles
                  .map(
                    (a) => `<li>
                      <a href="${escapeHtml(a.url)}" target="_blank" rel="noopener">${escapeHtml(a.title || "Untitled")}</a>
                      <div class="pill-meta">PMID ${escapeHtml(a.pmid)} · ${escapeHtml(a.pubdate || "")}</div>
                    </li>`
                  )
                  .join("")}</ul>`
              : '<p class="disclaimer">No matching PubMed abstracts found yet.</p>'
          }
        </div>
      </section>`;
  }

  function renderRelated(drug) {
    const rel = drug.related_drugs || {};
    const items = rel.items || [];
    return `
      <section class="section">
        <h2>Related / Competitor Drugs</h2>
        <p class="disclaimer">${escapeHtml(rel.label || "Approximate list — not a curated competitive analysis.")}</p>
        ${
          items.length
            ? `<div class="chip-list">${items
                .map((i) => `<span class="chip" title="${escapeHtml(i.reason || "")}">${escapeHtml(i.name)}</span>`)
                .join("")}</div>`
            : '<p class="disclaimer">No related drugs identified yet.</p>'
        }
      </section>`;
  }

  async function init() {
    const slug = qs("drug");
    const content = document.getElementById("content");
    if (!slug) {
      content.innerHTML = '<div class="error-state">No drug specified. Use drug.html?drug=&lt;slug&gt;.</div>';
      return;
    }
    try {
      const res = await fetch(`data/drugs/${encodeURIComponent(slug)}.json`, { cache: "no-store" });
      if (!res.ok) throw new Error(`profile not found (${res.status})`);
      const drug = await res.json();

      let conditionSummary = null;
      if (drug.condition && drug.condition.slug) {
        try {
          const cres = await fetch(`data/drugs/_conditions_cache/${encodeURIComponent(drug.condition.slug)}.json`, {
            cache: "no-store",
          });
          if (cres.ok) conditionSummary = await cres.json();
        } catch (e) {
          /* condition summary is optional */
        }
      }

      document.title = `${drug.drug_name} — Phase 3 Catalyst Tracker`;
      content.innerHTML = `
        <div class="drug-header">
          <div>
            <h1>${escapeHtml(drug.drug_name)}</h1>
            <div class="subtitle">${val(drug.condition && drug.condition.raw)}</div>
          </div>
        </div>
        ${renderIdentity(drug)}
        ${renderIndication(drug, conditionSummary)}
        ${renderMechanism(drug)}
        ${renderPK(drug)}
        ${renderLabelExtras(drug)}
        ${renderPriorResults(drug)}
        ${renderRelated(drug)}
        <p class="disclaimer">Last enriched: ${val(drug.enriched_at)}</p>
      `;
    } catch (err) {
      content.innerHTML = `<div class="error-state">Could not load a profile for "${escapeHtml(
        slug
      )}". It may not have been enriched yet — check back after the next scheduled run.</div>`;
      console.error(err);
    }
  }

  init();
})();
