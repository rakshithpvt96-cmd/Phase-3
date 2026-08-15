(function () {
  "use strict";

  const state = {
    trials: [],
    sortKey: "primary_completion_date",
    sortAsc: true,
    search: "",
    windowFilter: "all",
    signalFilter: "all",
  };

  function slugify(name) {
    return (
      (name || "")
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/(^-|-$)/g, "") || "unknown"
    );
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function computeSignals(t) {
    const sig = [];
    if ((t.sec_8k_matches || []).length) sig.push({ cls: "badge-8k", label: `[ ${t.sec_8k_matches.length} 8-K ]` });
    if (t.has_results) sig.push({ cls: "badge-results", label: "[ RESULTS ]" });
    return sig;
  }

  const PILL_ICON =
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round"><rect x="3" y="9" width="18" height="6" rx="3" transform="rotate(-45 12 12)"/><line x1="12" y1="7.5" x2="12" y2="16.5" transform="rotate(-45 12 12)"/></svg>';

  function sponsorLink(t) {
    if (!t.sponsor) return "—";
    const info = t.company_info || {};
    const capSuffix = info.market_cap_display ? ` <span class="mcap">(${escapeHtml(info.market_cap_display)})</span>` : "";
    if (!info.edgar_url) return `${escapeHtml(t.sponsor)}${capSuffix}`;
    return `<a href="${escapeHtml(info.edgar_url)}" target="_blank" rel="noopener" title="View SEC filings">${escapeHtml(
      t.sponsor
    )}</a>${capSuffix}`;
  }

  function drugLinks(t) {
    if (!t.drug_names || !t.drug_names.length) return "—";
    return t.drug_names
      .map(
        (name) =>
          `<a class="drug-link" href="drug.html?drug=${encodeURIComponent(slugify(name))}">${PILL_ICON}${escapeHtml(name)}</a>`
      )
      .join(", ");
  }

  function matchesFilters(t) {
    if (state.windowFilter !== "all" && t.window !== state.windowFilter) return false;
    if (state.signalFilter === "8k" && !(t.sec_8k_matches || []).length) return false;
    if (state.signalFilter === "results" && !t.has_results) return false;
    if (state.search) {
      const hay = [...(t.drug_names || []), t.sponsor, ...(t.conditions || []), t.nct_id, t.title]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(state.search)) return false;
    }
    return true;
  }

  function sorter(a, b) {
    const key = state.sortKey;
    let av = a[key];
    let bv = b[key];
    if (key === "drug_names" || key === "conditions") {
      av = (av || [])[0] || "";
      bv = (bv || [])[0] || "";
    }
    if (key === "signals") {
      av = computeSignals(a).length;
      bv = computeSignals(b).length;
    }
    av = av == null ? "" : av;
    bv = bv == null ? "" : bv;
    const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
    return state.sortAsc ? cmp : -cmp;
  }

  function render() {
    const tbody = document.getElementById("table-body");
    const emptyState = document.getElementById("empty-state");
    const rows = state.trials.filter(matchesFilters).slice().sort(sorter);

    if (!rows.length) {
      tbody.innerHTML = "";
      emptyState.style.display = "block";
      return;
    }
    emptyState.style.display = "none";

    tbody.innerHTML = rows
      .map((t) => {
        const signals = computeSignals(t)
          .map((s) => `<span class="badge ${s.cls}">${s.label}</span>`)
          .join(" ");
        const windowBadge =
          t.window === "upcoming"
            ? '<span class="badge badge-upcoming">[ UPCOMING ]</span>'
            : '<span class="badge badge-lookback">[ LOOKBACK ]</span>';
        const dateType = t.primary_completion_date_type
          ? ` <span class="pill-meta">(${escapeHtml(t.primary_completion_date_type)})</span>`
          : "";
        return `<tr>
          <td>${drugLinks(t)}</td>
          <td>${sponsorLink(t)}</td>
          <td>${escapeHtml((t.conditions || [])[0] || "—")}</td>
          <td>${escapeHtml(t.primary_completion_date || "—")}${dateType}</td>
          <td>${windowBadge}</td>
          <td>${escapeHtml(t.status || "—")}</td>
          <td>${signals || "—"}</td>
          <td><a href="${escapeHtml(t.url || "#")}" target="_blank" rel="noopener">${escapeHtml(t.nct_id || "—")}</a></td>
        </tr>`;
      })
      .join("");
  }

  function setupSorting() {
    document.querySelectorAll("#catalyst-table thead th").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (state.sortKey === key) state.sortAsc = !state.sortAsc;
        else {
          state.sortKey = key;
          state.sortAsc = true;
        }
        document.querySelectorAll("#catalyst-table thead th").forEach((h) => h.classList.remove("sorted", "asc"));
        th.classList.add("sorted");
        if (state.sortAsc) th.classList.add("asc");
        render();
      });
    });
  }

  function setupControls() {
    document.getElementById("search").addEventListener("input", (e) => {
      state.search = e.target.value.trim().toLowerCase();
      render();
    });
    document.getElementById("window-filter").addEventListener("change", (e) => {
      state.windowFilter = e.target.value;
      render();
    });
    document.getElementById("signal-filter").addEventListener("change", (e) => {
      state.signalFilter = e.target.value;
      render();
    });
  }

  function renderStats(data) {
    const upcoming = data.trials.filter((t) => t.window === "upcoming").length;
    const lookback = data.trials.filter((t) => t.window === "lookback").length;
    const withSignal = data.trials.filter((t) => (t.sec_8k_matches || []).length).length;
    document.getElementById("stat-row").innerHTML = `
      <div class="stat-tile"><div class="n">${data.trials.length}</div><div class="label">Total tracked trials</div></div>
      <div class="stat-tile"><div class="n">${upcoming}</div><div class="label">Completing in next ${data.window_upcoming_days} days</div></div>
      <div class="stat-tile"><div class="n">${lookback}</div><div class="label">Completed ${data.window_lookback_days[0]}–${data.window_lookback_days[1]} days ago</div></div>
      <div class="stat-tile"><div class="n">${withSignal}</div><div class="label">With SEC 8-K signal</div></div>
    `;
  }

  const TICKER_PX_PER_SECOND = 45; // comfortable reading speed, independent of content length

  function renderTicker(data) {
    const track = document.getElementById("ticker-track");
    const trials = data.trials || [];
    if (!trials.length) {
      track.innerHTML = '<span class="seg">NO ACTIVE CATALYSTS ON FILE — AWAITING NEXT SCHEDULED FETCH FROM CLINICALTRIALS.GOV + SEC EDGAR</span>';
      track.style.animation = "none";
      return;
    }
    const segs = trials.slice(0, 60).map((t) => {
      const drug = escapeHtml((t.drug_names || [])[0] || t.title || "—");
      const cls = (t.sec_8k_matches || []).length ? "sig" : t.window === "upcoming" ? "up" : "";
      const flag = (t.sec_8k_matches || []).length ? " ⚠8-K" : "";
      return `<span class="seg ${cls}"><span class="n">${drug}</span> ${escapeHtml(t.sponsor || "—")} · ${escapeHtml(
        t.primary_completion_date || "—"
      )}${flag}</span><span class="sep">|</span>`;
    });
    track.innerHTML = segs.join("");

    // Fixed-duration CSS animations move faster as content gets longer (more
    // distance covered in the same time). Derive duration from actual content
    // width instead, so scroll speed stays constant no matter how many
    // trials are on the ticker.
    requestAnimationFrame(() => {
      const containerWidth = track.parentElement.clientWidth;
      const trackWidth = track.scrollWidth;
      if (trackWidth <= containerWidth) {
        track.style.animation = "none";
        return;
      }
      const duration = (trackWidth + containerWidth) / TICKER_PX_PER_SECOND;
      track.style.animationDuration = `${duration}s`;
    });
  }

  async function init() {
    setupSorting();
    setupControls();
    try {
      const res = await fetch("data/catalysts.json", { cache: "no-store" });
      const data = await res.json();
      state.trials = data.trials || [];
      renderStats(data);
      renderTicker(data);
      document.getElementById("generated-meta").textContent = data.generated_at
        ? `Last updated ${data.generated_at}`
        : "Not yet populated -- waiting on the first scheduled fetch.";
      render();
    } catch (err) {
      const emptyState = document.getElementById("empty-state");
      document.getElementById("table-body").innerHTML = "";
      emptyState.style.display = "block";
      emptyState.textContent = "Failed to load data/catalysts.json.";
      console.error(err);
    }
  }

  init();
})();
