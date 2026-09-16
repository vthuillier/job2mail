import {
  STATUS_LABELS,
  STATUS_COLORS,
  TRANCHE_EFFECTIFS,
  state,
  toast,
  esc,
  normalizeText,
  currentTravelOrigin,
  formatTravelInfo,
  buildLinkedinPeopleUrl,
} from "./shared.js";

function activatePage(page) {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
    if (page === "entreprises" && state.view === "map") {
      setTimeout(() => {
        if (state.map) state.map.invalidateSize();
        renderMap();
      }, 80);
    }
  }

  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => activatePage(btn.dataset.page));
  });

  async function pollJob(jobId, { intervalMs = 1500 } = {}) {
    while (true) {
      const res = await fetch(`/api/jobs/${jobId}`).then((r) => r.json());
      if (res.status === "done") return res.result;
      if (res.status === "error") throw new Error(res.error || "Le job a échoué");
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  }

  async function startJob(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then((r) => r.json());
    if (res.error) throw new Error(res.error);
    return pollJob(res.job_id);
  }

  function setView(view) {
    state.view = view;
    document.getElementById("view-list").classList.toggle("active", view === "list");
    document.getElementById("view-map").classList.toggle("active", view === "map");
    document.getElementById("map-panel").classList.toggle("active", view === "map");
    document.getElementById("list-panel").classList.toggle("hidden", view === "map");
    if (view === "map") {
      ensureMap().then(() => renderMap());
    }
  }

  document.getElementById("view-list").addEventListener("click", () => setView("list"));
  document.getElementById("view-map").addEventListener("click", () => setView("map"));

  function renderMapLegend() {
    const legend = document.getElementById("map-legend");
    legend.innerHTML = Object.keys(STATUS_LABELS)
      .filter((k) => k !== "tous")
      .map(
        (k) =>
          `<span><i style="background:${STATUS_COLORS[k] || "#8b9aab"}"></i>${STATUS_LABELS[k]}</span>`
      )
      .join("");
  }

  let leafletLoadPromise = null;

  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (leafletLoadPromise) return leafletLoadPromise;
    leafletLoadPromise = new Promise((resolve, reject) => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      link.crossOrigin = "";
      document.head.appendChild(link);

      const script = document.createElement("script");
      script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      script.crossOrigin = "";
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("Chargement de Leaflet impossible"));
      document.body.appendChild(script);
    });
    return leafletLoadPromise;
  }

  async function ensureMap() {
    if (state.mapReady) {
      state.map.invalidateSize();
      return;
    }
    await loadLeaflet();
    state.map = L.map("map", { zoomControl: true });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; CARTO',
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(state.map);
    state.mapLayer = L.layerGroup().addTo(state.map);
    state.map.setView([43.14, 6.07], 10);
    state.mapReady = true;
    renderMapLegend();
  }

  async function geocodeMissing() {
    if (state.geocodePromise) return state.geocodePromise;
    state.geocodePromise = (async () => {
      let remaining = 1;
      let total = 0;
      while (remaining > 0) {
        const res = await fetch("/api/entreprises/geocode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit: 40 }),
        }).then((r) => r.json());
        total += res.geocoded || 0;
        remaining = res.remaining || 0;
        if (!res.processed || (res.processed > 0 && !res.geocoded)) break;
        if (res.geocoded > 0) {
          await loadEntreprises({ skipMap: true });
          await updateMapMarkers();
        }
      }
      return total;
    })();
    try {
      return await state.geocodePromise;
    } finally {
      state.geocodePromise = null;
    }
  }

  function countActiveAdvFilters() {
    const f = state.advFilters;
    let n = 0;
    if (f.nafs.size) n++;
    if (f.effectifs.size) n++;
    if (f.commune.trim()) n++;
    if (f.categorie) n++;
    if (f.nature) n++;
    if (f.scoreMin != null && f.scoreMin !== "") n++;
    if (f.travelMaxMin != null && f.travelMaxMin !== "") n++;
    if (f.siegeOnly) n++;
    if (f.hasContact) n++;
    if (f.hasEmail) n++;
    if (f.serpapiScanned) n++;
    return n;
  }

  function readAdvFiltersFromUI() {
    const f = state.advFilters;
    f.nafs = new Set(
      [...document.querySelectorAll("#filter-naf input:checked")].map((i) => i.value)
    );
    f.effectifs = new Set(
      [...document.querySelectorAll("#filter-effectif input:checked")].map((i) => i.value)
    );
    f.commune = document.getElementById("filter-commune").value.trim();
    f.categorie = document.getElementById("filter-categorie").value;
    f.nature = document.getElementById("filter-nature").value;
    const scoreRaw = document.getElementById("filter-score-min").value.trim();
    f.scoreMin = scoreRaw === "" ? null : Number(scoreRaw);
    const travelRaw = document.getElementById("filter-travel-max").value.trim();
    f.travelMaxMin = travelRaw === "" ? null : Number(travelRaw);
    f.siegeOnly = document.getElementById("filter-siege").checked;
    f.hasContact = document.getElementById("filter-contact").checked;
    f.hasEmail = document.getElementById("filter-email").checked;
    f.serpapiScanned = document.getElementById("filter-serpapi").checked;
  }

  function buildFilterQueryParams({ includeTravel = true } = {}) {
    const params = new URLSearchParams();
    if (state.filter && state.filter !== "tous") params.set("status", state.filter);
    if (state.search.trim()) params.set("search", state.search.trim());
    const f = state.advFilters;
    f.nafs.forEach((code) => params.append("naf", code));
    f.effectifs.forEach((code) => params.append("effectif", code));
    if (f.commune) params.set("commune", f.commune);
    if (f.categorie) params.set("categorie", f.categorie);
    if (f.nature) params.set("nature", f.nature);
    if (f.scoreMin != null && !Number.isNaN(f.scoreMin)) params.set("score_min", f.scoreMin);
    if (includeTravel && f.travelMaxMin != null && !Number.isNaN(f.travelMaxMin)) {
      params.set("travel_max_min", f.travelMaxMin);
    }
    if (f.siegeOnly) params.set("siege_only", "1");
    if (f.hasContact) params.set("has_contact", "1");
    if (f.hasEmail) params.set("has_email", "1");
    if (f.serpapiScanned) params.set("serpapi_scanned", "1");
    return params;
  }

  function updateAdvFilterUI() {
    const active = countActiveAdvFilters();
    const badge = document.getElementById("adv-active-count");
    badge.style.display = active ? "inline" : "none";
    badge.textContent = `${active} actif${active > 1 ? "s" : ""}`;

    const total = state.stats.counts.tous || 0;
    const shown = state.total;
    const el = document.getElementById("filter-result");
    if (active || state.search.trim() || state.filter !== "tous") {
      const loading = state.travelLoading ? " · calcul trajets en cours…" : "";
      el.innerHTML = `<strong>${shown}</strong> / ${total} entreprise${total > 1 ? "s" : ""} affichée${shown > 1 ? "s" : ""}${loading}`;
    } else {
      el.textContent = `${total} entreprise${total > 1 ? "s" : ""} en base`;
    }
  }

  function renderAdvFilterOptions() {
    const nafBox = document.getElementById("filter-naf");
    const effBox = document.getElementById("filter-effectif");
    const prevNafs = new Set(state.advFilters.nafs);
    const prevEffs = new Set(state.advFilters.effectifs);

    const nafMap = new Map();
    state.nafCodesUsed.forEach(({ code, libelle }) => {
      if (!code) return;
      nafMap.set(code, libelle || state.nafs[code] || code);
    });
    Object.entries(state.nafs).forEach(([code, lib]) => {
      if (!nafMap.has(code)) nafMap.set(code, lib);
    });

    nafBox.innerHTML = [...nafMap.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(
        ([code, lib]) =>
          `<label class="filter-check-item">
            <input type="checkbox" value="${esc(code)}" ${prevNafs.has(code) ? "checked" : ""}>
            <span><strong>${esc(code)}</strong> — ${esc(lib)}</span>
          </label>`
      )
      .join("") || `<span style="color:var(--faint);font-size:0.82rem;padding:4px;">Aucun NAF en base</span>`;

    effBox.innerHTML = Object.entries(TRANCHE_EFFECTIFS)
      .map(
        ([code, lib]) =>
          `<label class="filter-check-item">
            <input type="checkbox" value="${esc(code)}" ${prevEffs.has(code) ? "checked" : ""}>
            <span><strong>${esc(code)}</strong> — ${esc(lib)}</span>
          </label>`
      )
      .join("");

    nafBox.querySelectorAll("input").forEach((cb) => {
      cb.addEventListener("change", onAdvFilterChange);
    });
    effBox.querySelectorAll("input").forEach((cb) => {
      cb.addEventListener("change", onAdvFilterChange);
    });
  }

  function resetAdvFilters() {
    state.advFilters = {
      nafs: new Set(),
      effectifs: new Set(),
      commune: "",
      categorie: "",
      nature: "",
      scoreMin: null,
      travelMaxMin: null,
      siegeOnly: false,
      hasContact: false,
      hasEmail: false,
      serpapiScanned: false,
    };
    document.getElementById("filter-commune").value = "";
    document.getElementById("filter-categorie").value = "";
    document.getElementById("filter-nature").value = "";
    document.getElementById("filter-score-min").value = "";
    document.getElementById("filter-travel-max").value = "";
    document.getElementById("filter-siege").checked = false;
    document.getElementById("filter-contact").checked = false;
    document.getElementById("filter-email").checked = false;
    document.getElementById("filter-serpapi").checked = false;
    renderAdvFilterOptions();
    loadEntreprises({ resetPage: true });
  }

  function onAdvFilterChange() {
    readAdvFiltersFromUI();
    loadEntreprises({ resetPage: true });
    maybeRefreshTravelFilter();
  }

  async function fetchTravelCandidates() {
    const params = buildFilterQueryParams({ includeTravel: false });
    const res = await fetch(`/api/entreprises/lite?${params.toString()}`).then((r) => r.json());
    return res.entreprises || [];
  }

  function travelNeedsRefresh(e) {
    const originOk = normalizeText(e.travel_origin) === normalizeText(currentTravelOrigin());
    const noTolls = e.travel_without_tolls === 1 || e.travel_without_tolls === true;
    return !originOk || !noTolls || e.travel_duration_min == null || e.travel_distance_km == null;
  }

  async function computeTravelForCurrentFilter({ force = false } = {}) {
    const btn = document.getElementById("btn-compute-travel");
    const candidates = await fetchTravelCandidates();
    const sirets = force
      ? candidates.map((e) => e.siret)
      : candidates.filter(travelNeedsRefresh).map((e) => e.siret);
    if (!sirets.length) {
      renderTable({ skipMap: true });
      return;
    }

    state.travelLoading = true;
    btn.disabled = true;
    btn.textContent = "Calcul trajets…";
    renderTable({ skipMap: true });
    toast(`Calcul des trajets sans péage depuis ${currentTravelOrigin()}…`);
    try {
      const res = await fetch("/api/entreprises/trajets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sirets, origin: currentTravelOrigin(), force }),
      }).then((r) => r.json());
      if (res.error) return toast(res.error, "error");
      toast(`Trajets mis à jour : ${res.refreshed || 0} calculé(s), ${res.unavailable || 0} indisponible(s)`);
      await loadEntreprises({ skipMap: true });
    } catch (err) {
      toast(String(err), "error");
    } finally {
      state.travelLoading = false;
      btn.disabled = false;
      btn.textContent = "Calculer les trajets";
      renderTable({ skipMap: true });
      if (state.view === "map") updateMapMarkers();
    }
  }

  function maybeRefreshTravelFilter() {
    if (state.travelLoading) return;
    if (state.advFilters.travelMaxMin != null && !Number.isNaN(state.advFilters.travelMaxMin)) {
      computeTravelForCurrentFilter();
    }
  }

  document.getElementById("adv-filters-toggle").addEventListener("click", () => {
    state.advFiltersOpen = !state.advFiltersOpen;
    document.getElementById("adv-filters").classList.toggle("open", state.advFiltersOpen);
  });

  ["filter-commune", "filter-categorie", "filter-nature", "filter-score-min"].forEach((id) => {
    document.getElementById(id).addEventListener("input", onAdvFilterChange);
    document.getElementById(id).addEventListener("change", onAdvFilterChange);
  });
  document.getElementById("filter-travel-max").addEventListener("input", () => {
    readAdvFiltersFromUI();
    renderTable({ skipMap: true });
  });
  document.getElementById("filter-travel-max").addEventListener("change", async () => {
    readAdvFiltersFromUI();
    renderTable({ skipMap: true });
    if (state.advFilters.travelMaxMin != null && !Number.isNaN(state.advFilters.travelMaxMin)) {
      await computeTravelForCurrentFilter();
    }
  });
  ["filter-siege", "filter-contact", "filter-email", "filter-serpapi"].forEach((id) => {
    document.getElementById(id).addEventListener("change", onAdvFilterChange);
  });
  document.getElementById("btn-compute-travel").addEventListener("click", () => computeTravelForCurrentFilter({ force: true }));
  document.getElementById("btn-reset-filters").addEventListener("click", resetAdvFilters);

  async function fetchMapRows() {
    const params = buildFilterQueryParams({ includeTravel: true });
    const res = await fetch(`/api/entreprises/lite?${params.toString()}`).then((r) => r.json());
    return res.entreprises || [];
  }

  async function updateMapMarkers() {
    if (!state.mapLayer) return;
    const allRows = await fetchMapRows();
    state.mapMissingCount = allRows.filter((e) => e.latitude == null || e.longitude == null).length;
    const rows = allRows.filter((e) => e.latitude != null && e.longitude != null);
    state.mapLayer.clearLayers();
    if (!rows.length) {
      state.map.setView([43.14, 6.07], 10);
      return;
    }

    const bounds = [];
    rows.forEach((e) => {
      const status = e.status || "a_postuler";
      const color = STATUS_COLORS[status] || "#8b9aab";
      const lat = Number(e.latitude);
      const lon = Number(e.longitude);
      const marker = L.circleMarker([lat, lon], {
        radius: 7,
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      });
      const label = STATUS_LABELS[status] || status;
      marker.bindPopup(`
        <div class="map-popup-title">${esc(e.denomination)}</div>
        <div class="map-popup-sub">${esc(e.commune || "")} · score ${e.score_pertinence != null ? Number(e.score_pertinence).toFixed(0) : "—"}</div>
        <span class="badge badge-${esc(status)}">${esc(label)}</span>
        <div class="map-popup-actions">
          <button class="btn btn-secondary btn-sm map-open" data-siret="${esc(e.siret)}">Gérer</button>
        </div>
      `);
      marker.on("popupopen", (ev) => {
        const btn = ev.popup.getElement()?.querySelector(".map-open");
        if (btn) btn.addEventListener("click", () => openDrawer(e.siret));
      });
      marker.addTo(state.mapLayer);
      bounds.push([lat, lon]);
    });

    state.map.fitBounds(bounds, { padding: [36, 36], maxZoom: 14 });
  }

  async function renderMap() {
    await ensureMap();
    await updateMapMarkers();

    if (state.mapMissingCount > 0) {
      toast(`Géocodage de ${state.mapMissingCount} entreprise(s) en cours…`);
      geocodeMissing().then((total) => {
        if (total > 0) toast(`${total} entreprise(s) positionnée(s) sur la carte`);
      });
    }
  }

  async function loadConfig() {
    const cfg = await fetch("/api/config").then((r) => r.json());
    state.config = cfg;
    state.nafs = { ...(cfg.nafs || {}) };

    document.getElementById("cfg-insee").value = cfg.INSEE_TOKEN || "";
    document.getElementById("cfg-serp").value = cfg.SERPAPI_KEY || "";
    document.getElementById("cfg-hunter").value = cfg.TOKEN_HUNTER_IO || "";
    const gmailStatus = document.getElementById("cfg-gmail-status");
    if (gmailStatus) {
      gmailStatus.textContent = cfg.google_connected_email
        ? `Compte Gmail connecté : ${cfg.google_connected_email}`
        : "Aucun compte Gmail connecté — clique sur « Reconnecter Gmail ».";
    }
    document.getElementById("cfg-candidate-name").value = cfg.candidate_name || "";
    document.getElementById("cfg-point").value = cfg.point_ref || "La Crau";
    document.getElementById("cfg-rayon").value = cfg.rayon_km || "20";
    document.getElementById("cfg-depts").value = cfg.departements || "83, 13";
    document.getElementById("cfg-mail-subject").value = cfg.mail_subject || "";
    document.getElementById("cfg-mail-body").value = cfg.mail_body || "";
    document.getElementById("cfg-relance-body").value = cfg.relance_body || "";
    const rel2 = document.getElementById("cfg-relance2-body");
    if (rel2) rel2.value = cfg.relance2_body || "";

    document.getElementById("scan-point").value = cfg.point_ref || "La Crau";
    document.getElementById("scan-rayon").value = cfg.rayon_km || "20";
    document.getElementById("scan-depts").value = cfg.departements || "83, 13";
    const scanMairies = document.getElementById("scan-mairies");
    const scanAssos = document.getElementById("scan-associations");
    if (scanMairies) scanMairies.checked = cfg.scan_mairies !== "0";
    if (scanAssos) scanAssos.checked = cfg.scan_associations !== "0";
    const travelLabel = document.getElementById("filter-travel-label");
    if (travelLabel) {
      travelLabel.textContent = `Trajet max sans péage depuis ${cfg.point_ref || "La Crau"} (min)`;
    }
    const ollamaEl = document.getElementById("ollama-status");
    if (ollamaEl) {
      ollamaEl.textContent = cfg.ollama_available
        ? `Ollama OK — modèle ${cfg.OLLAMA_MODEL || "qwen3:0.6b"}`
        : `Ollama indisponible — fallback heuristique (modèle attendu : ${cfg.OLLAMA_MODEL || "qwen3:0.6b"})`;
    }
    renderNafs(true);
    renderCfgNafs(true);
    renderCvProfileStatus(cfg.cv_profile);

    if (cfg.needs_setup) {
      activatePage("config");
      toast("Bienvenue ! Renseigne tes paramètres pour commencer (ou passe-les par un fichier .env).");
    }
    loadDbBackend();
  }

  function renderNafBox(boxId, checkedAll = false) {
    const box = document.getElementById(boxId);
    if (!box) return;
    const previously = new Set(
      [...box.querySelectorAll("input:checked")].map((i) => i.value)
    );
    box.innerHTML = "";
    Object.entries(state.nafs).forEach(([code, lib]) => {
      const label = document.createElement("label");
      label.className = "naf-item";
      const checked = checkedAll || previously.has(code) || previously.size === 0;
      label.innerHTML = `<input type="checkbox" value="${esc(code)}" ${checked ? "checked" : ""}>
        <span><strong>${esc(code)}</strong> — ${esc(lib)}</span>`;
      box.appendChild(label);
    });
  }

  function renderNafs(checkedAll = false) {
    renderNafBox("naf-list", checkedAll);
  }

  function renderCfgNafs(checkedAll = false) {
    renderNafBox("cfg-naf-list", checkedAll);
  }

  async function loadEntreprises({ skipMap = false, resetPage = false } = {}) {
    if (resetPage) state.page = 1;
    const params = buildFilterQueryParams();
    params.set("page", state.page);
    params.set("per_page", state.perPage);
    const [listRes, statsRes] = await Promise.all([
      fetch(`/api/entreprises?${params.toString()}`).then((r) => r.json()),
      fetch("/api/entreprises/stats").then((r) => r.json()),
    ]);
    state.entreprises = listRes.entreprises || [];
    state.total = listRes.total || 0;
    state.page = listRes.page || 1;
    state.pages = listRes.pages || 1;
    state.stats = {
      counts: statsRes.counts || { tous: 0 },
      relances_dues: statsRes.relances_dues || 0,
    };
    renderTable({ skipMap });
  }

  async function loadNafCodesUsed() {
    const res = await fetch("/api/entreprises/naf-codes-used").then((r) => r.json());
    state.nafCodesUsed = res.codes || [];
    renderAdvFilterOptions();
  }

  function renderFilters(counts) {
    const wrap = document.getElementById("filters");
    wrap.innerHTML = "";
    Object.keys(STATUS_LABELS).forEach((key) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `chip${state.filter === key ? " active" : ""}`;
      chip.textContent = `${STATUS_LABELS[key]} (${counts[key] || 0})`;
      chip.addEventListener("click", () => {
        state.filter = key;
        loadEntreprises({ resetPage: true });
        maybeRefreshTravelFilter();
      });
      wrap.appendChild(chip);
    });
  }

  function renderPagination() {
    const el = document.getElementById("pagination");
    if (!el) return;
    if (state.pages <= 1) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = `
      <button type="button" class="btn btn-secondary btn-sm" id="page-prev" ${state.page <= 1 ? "disabled" : ""}>‹ Précédent</button>
      <span class="pagination-info">Page ${state.page} / ${state.pages} — ${state.total} résultat${state.total > 1 ? "s" : ""}</span>
      <button type="button" class="btn btn-secondary btn-sm" id="page-next" ${state.page >= state.pages ? "disabled" : ""}>Suivant ›</button>
    `;
    document.getElementById("page-prev")?.addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        loadEntreprises({ skipMap: true });
      }
    });
    document.getElementById("page-next")?.addEventListener("click", () => {
      if (state.page < state.pages) {
        state.page += 1;
        loadEntreprises({ skipMap: true });
      }
    });
  }

  function renderTable({ skipMap = false } = {}) {
    const counts = state.stats.counts || { tous: 0 };

    document.getElementById("st-total").textContent = counts.tous || 0;
    document.getElementById("st-todo").textContent = counts.a_postuler || 0;
    document.getElementById("st-sent").textContent = (counts.postule || 0) + (counts.relance || 0);
    const dues = state.stats.relances_dues || 0;
    const entretiens = (counts.entretien || 0) + (counts.offre || 0);
    const stRel = document.getElementById("st-relances");
    const stEnt = document.getElementById("st-entretiens");
    if (stRel) stRel.textContent = dues;
    if (stEnt) stEnt.textContent = entretiens;
    renderFilters(counts);

    const rows = state.entreprises;
    updateAdvFilterUI();
    renderPagination();

    const tbody = document.getElementById("tbody");
    if (!rows.length) {
      const hasFilters =
        countActiveAdvFilters() > 0 || state.search.trim() || state.filter !== "tous";
      const msg = hasFilters
        ? "Aucune entreprise ne correspond aux filtres."
        : "Aucune entreprise. Lance un scan Sirene.";
      tbody.innerHTML = `<tr><td class="empty" colspan="9">${msg}</td></tr>`;
      if (state.view === "map" && !skipMap) updateMapMarkers();
      return;
    }

    tbody.innerHTML = rows
      .map((e) => {
        const site = e.site_web
          ? `<a class="ext" href="${esc(e.site_web)}" target="_blank" rel="noopener">${esc(e.site_web)} ↗</a>`
          : `<span style="color:var(--faint)">${e.serpapi_scanned ? "Pas de site" : "SerpAPI non fait"}</span>`;
        const contact = e.contact_prenom || e.contact_nom
          ? `${esc(e.contact_prenom || "")} ${esc(e.contact_nom || "")}`.trim()
          : `<span style="color:var(--faint)">—</span>`;
        const score = e.score_pertinence != null ? Number(e.score_pertinence).toFixed(0) : "—";
        const travelInfo = formatTravelInfo(e);
        const nature = e.nature || "entreprise";
        const natureBadge =
          nature === "mairie"
            ? `<span class="badge badge-nature-mairie">Mairie</span>`
            : nature === "association"
              ? `<span class="badge badge-nature-association">Association</span>`
              : "";
        return `<tr>
          <td data-label=""><input type="checkbox" class="row-select-cb" data-siret="${esc(e.siret)}" ${state.selectedSirets.has(e.siret) ? "checked" : ""}></td>
          <td class="mono" data-label="Score">${score}</td>
          <td data-label="Entreprise">
            <div class="title">${esc(e.denomination)} ${natureBadge}</div>
            <div class="sub">${site}</div>
            <div class="sub mono">${esc(e.naf_code || "")} ${esc(e.naf_libelle || "")}</div>
          </td>
          <td data-label="Localisation">
            <div>${esc(e.commune || "—")}</div>
            <div class="sub">${esc(e.adresse || "")}</div>
            ${travelInfo ? `<div class="sub">${esc(travelInfo)}</div>` : ""}
          </td>
          <td class="mono" data-label="Effectif">${esc(e.effectif_libelle || "—")}</td>
          <td data-label="LinkedIn">
            <a class="btn btn-secondary btn-sm" href="${esc(buildLinkedinPeopleUrl(e.denomination, e.commune))}" target="_blank" rel="noopener">People ↗</a>
            ${e.linkedin_company ? `<div class="sub"><a class="ext" href="${esc(e.linkedin_company)}" target="_blank" rel="noopener">Company</a></div>` : ""}
          </td>
          <td data-label="Contact">
            <div>${contact}</div>
            <div class="sub">${esc(e.contact_email || e.contact_poste || "")}</div>
          </td>
          <td data-label="Statut"><span class="badge badge-${esc(e.status || "a_postuler")}">${esc((e.status || "a_postuler").replace("_", " "))}</span></td>
          <td data-label="">
            <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
              <button class="btn btn-secondary btn-sm btn-edit" data-siret="${esc(e.siret)}">Gérer</button>
              <button class="btn btn-sky btn-sm btn-scan-one" data-siret="${esc(e.siret)}">Scan</button>
              <button class="btn btn-secondary btn-sm btn-export-one" data-siret="${esc(e.siret)}">PDF</button>
            </div>
          </td>
        </tr>`;
      })
      .join("");

    tbody.querySelectorAll(".btn-edit").forEach((btn) => {
      btn.addEventListener("click", () => openDrawer(btn.dataset.siret));
    });
    tbody.querySelectorAll(".btn-scan-one").forEach((btn) => {
      btn.addEventListener("click", () => scanOneEntreprise(btn.dataset.siret));
    });
    tbody.querySelectorAll(".btn-export-one").forEach((btn) => {
      btn.addEventListener("click", () => downloadEntreprisePdf(btn.dataset.siret));
    });
    tbody.querySelectorAll(".row-select-cb").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) state.selectedSirets.add(cb.dataset.siret);
        else state.selectedSirets.delete(cb.dataset.siret);
        updateExportSelectedButton();
        updateSelectAllCheckbox(rows);
      });
    });
    updateSelectAllCheckbox(rows);
    if (state.view === "map" && !skipMap) updateMapMarkers();
  }

  function updateExportSelectedButton() {
    const btn = document.getElementById("btn-export-selected");
    if (!btn) return;
    const n = state.selectedSirets.size;
    btn.textContent = `Exporter PDF (${n})`;
    btn.style.display = n > 0 ? "" : "none";
  }

  function updateSelectAllCheckbox(visibleRows) {
    const selectAll = document.getElementById("select-all-cb");
    if (!selectAll) return;
    const visibleSirets = visibleRows.map((e) => e.siret);
    const allSelected = visibleSirets.length > 0 && visibleSirets.every((s) => state.selectedSirets.has(s));
    selectAll.checked = allSelected;
    selectAll.indeterminate = !allSelected && visibleSirets.some((s) => state.selectedSirets.has(s));
  }

  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function downloadEntreprisePdf(siret) {
    const a = document.createElement("a");
    a.href = `/api/entreprises/${encodeURIComponent(siret)}/export.pdf`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function exportSelectedPdf() {
    const sirets = [...state.selectedSirets];
    if (!sirets.length) return;
    const btn = document.getElementById("btn-export-selected");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/entreprises/export.pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sirets }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Export impossible");
      }
      const blob = await res.blob();
      triggerDownload(blob, `export-entreprises-${sirets.length}.pdf`);
      toast(`${sirets.length} fiche(s) exportée(s)`);
    } catch (err) {
      toast(err.message || "Export impossible", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  document.getElementById("select-all-cb").addEventListener("change", (ev) => {
    const pageSirets = state.entreprises.map((e) => e.siret);
    if (ev.target.checked) pageSirets.forEach((s) => state.selectedSirets.add(s));
    else pageSirets.forEach((s) => state.selectedSirets.delete(s));
    renderTable();
    updateExportSelectedButton();
  });

  document.getElementById("btn-export-selected").addEventListener("click", exportSelectedPdf);

  function formatDateFr(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  }

  function toDatetimeLocal(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) {
      // déjà au format date ou datetime-local
      if (/^\d{4}-\d{2}-\d{2}/.test(iso)) return iso.slice(0, 16);
      return "";
    }
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function toDateInput(iso) {
    if (!iso) return "";
    if (/^\d{4}-\d{2}-\d{2}/.test(iso)) return iso.slice(0, 10);
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function renderEmailQuality(quality, note) {
    const el = document.getElementById("d-email-quality");
    if (!el) return;
    if (!quality) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.className = `quality-badge quality-${quality}`;
    el.textContent = note || quality;
    el.style.display = "inline-flex";
  }

  function toggleEntretienBox(status) {
    const box = document.getElementById("d-entretien-box");
    if (!box) return;
    box.classList.toggle("visible", ["entretien", "offre"].includes(status));
  }

  function updateDrawerMailActions(e) {
    const status = e.status || "a_postuler";
    const isHorsChamps = status === "hors_champs";
    const canRelance = ["postule", "relance"].includes(status) && !!(e.contact_email || "").trim();
    const btnMail = document.getElementById("btn-mail");
    const btnRelance = document.getElementById("btn-relance");
    const info = document.getElementById("d-mail-info");
    const ri = e.relance_info || {};

    btnMail.style.display = canRelance || isHorsChamps ? "none" : "flex";
    btnRelance.style.display = canRelance ? "flex" : "none";
    if (canRelance) {
      if (ri.blocked) {
        btnRelance.textContent = "Relance bloquée";
        btnRelance.disabled = true;
      } else if (ri.due) {
        btnRelance.textContent = `Relancer (n°${ri.angle || 1})`;
        btnRelance.disabled = false;
      } else {
        btnRelance.textContent = "Relancer (trop tôt)";
        btnRelance.disabled = false;
      }
    } else {
      btnRelance.disabled = false;
      btnRelance.textContent = "Relancer";
    }

    const parts = [];
    if (e.email_sent_at) parts.push(`Premier mail : ${formatDateFr(e.email_sent_at)}`);
    if (e.relance_count > 0) {
      parts.push(`Relances : ${e.relance_count}/${ri.max_relances || 2}`);
      if (e.last_relance_at) parts.push(`(dernière : ${formatDateFr(e.last_relance_at)})`);
    }
    if (ri.reason) parts.push(ri.reason);
    if (e.email_message_id) parts.push("Fil Gmail : OK");
    else if (canRelance) parts.push("Pas de Message-ID — relance hors fil");
    if (e.reply_class) {
      const lbl = STATUS_LABELS[e.reply_class] || e.reply_class;
      parts.push(`Réponse : ${lbl}`);
      if (e.reply_from) parts.push(`de ${e.reply_from}`);
    }
    if (e.contact_source) parts.push(`Contact : ${e.contact_source}`);
    if (parts.length) {
      info.textContent = parts.join(" · ");
      info.style.display = "block";
    } else {
      info.style.display = "none";
      info.textContent = "";
    }
  }

  async function openDrawer(siret) {
    let e = state.entreprises.find((x) => x.siret === siret);
    if (!e) {
      const res = await fetch(`/api/entreprises/${encodeURIComponent(siret)}`).then((r) => r.json());
      if (res.error || !res.entreprise) {
        toast(res.error || "Entreprise introuvable", "error");
        return;
      }
      e = res.entreprise;
      state.entreprises.push(e);
    }
    closeAddModal({ keepOverlay: true });
    document.getElementById("d-siret").value = e.siret;
    document.getElementById("d-denom").textContent = e.denomination;
    document.getElementById("d-adresse").textContent = `${e.adresse || ""} (${e.commune || ""})`;
    document.getElementById("d-li-people").href = buildLinkedinPeopleUrl(e.denomination, e.commune);
    document.getElementById("d-site").value = e.site_web || "";
    document.getElementById("d-li-company").value = e.linkedin_company || "";
    document.getElementById("d-prenom").value = e.contact_prenom || "";
    document.getElementById("d-nom").value = e.contact_nom || "";
    document.getElementById("d-genre").value = e.contact_genre || "m";
    document.getElementById("d-poste").value = e.contact_poste || "";
    document.getElementById("d-email").value = e.contact_email || "";
    document.getElementById("d-li-contact").value = e.contact_linkedin || "";
    document.getElementById("d-contact-source").value = e.contact_source || "";
    document.getElementById("d-status").value = e.status || "a_postuler";
    document.getElementById("d-notes").value = e.notes || "";
    document.getElementById("d-accroche").value = e.accroche || "";
    document.getElementById("d-entretien-date").value = toDatetimeLocal(e.entretien_date);
    document.getElementById("d-entretien-rappel").value = toDateInput(e.entretien_rappel_at);
    document.getElementById("d-entretien-next").value = e.entretien_next_step || "";
    renderEmailQuality(e.email_quality, e.email_quality_note);
    toggleEntretienBox(e.status || "a_postuler");
    updateDrawerMailActions(e);
    document.getElementById("overlay").classList.add("active");
    document.getElementById("drawer").classList.add("active");
    document.body.style.overflow = "hidden";
  }

  function closeDrawer() {
    document.getElementById("drawer").classList.remove("active");
    if (!document.getElementById("modal-add").classList.contains("active")) {
      document.getElementById("overlay").classList.remove("active");
      document.body.style.overflow = "";
    }
  }

  function fillAddEffectifSelect() {
    const sel = document.getElementById("add-effectif");
    if (!sel || sel.options.length) return;
    sel.innerHTML = Object.entries(TRANCHE_EFFECTIFS)
      .map(([code, label]) => `<option value="${esc(code)}">${esc(label)}</option>`)
      .join("");
  }

  function openAddModal() {
    fillAddEffectifSelect();
    closeDrawer();
    document.getElementById("add-denom").value = "";
    document.getElementById("add-siret").value = "";
    document.getElementById("add-commune").value = "";
    document.getElementById("add-adresse").value = "";
    document.getElementById("add-effectif").value = "NN";
    document.getElementById("add-categorie").value = "";
    document.getElementById("add-naf").value = "";
    document.getElementById("add-naf-lib").value = "";
    document.getElementById("add-site").value = "";
    document.getElementById("add-li").value = "";
    document.getElementById("add-notes").value = "";
    document.getElementById("overlay").classList.add("active");
    document.getElementById("modal-add").classList.add("active");
    document.body.style.overflow = "hidden";
    setTimeout(() => document.getElementById("add-denom").focus(), 50);
  }

  function closeAddModal({ keepOverlay = false } = {}) {
    document.getElementById("modal-add").classList.remove("active");
    if (!keepOverlay && !document.getElementById("drawer").classList.contains("active")) {
      document.getElementById("overlay").classList.remove("active");
      document.body.style.overflow = "";
    }
  }

  function closeOverlayPanels() {
    closeAddModal();
    closeDrawer();
  }

  function drawerPayload() {
    return {
      site_web: document.getElementById("d-site").value.trim(),
      linkedin_company: document.getElementById("d-li-company").value.trim(),
      contact_prenom: document.getElementById("d-prenom").value.trim(),
      contact_nom: document.getElementById("d-nom").value.trim(),
      contact_genre: document.getElementById("d-genre").value,
      contact_poste: document.getElementById("d-poste").value.trim(),
      contact_email: document.getElementById("d-email").value.trim(),
      contact_linkedin: document.getElementById("d-li-contact").value.trim(),
      contact_source: document.getElementById("d-contact-source").value.trim() || undefined,
      status: document.getElementById("d-status").value,
      notes: document.getElementById("d-notes").value.trim(),
      accroche: document.getElementById("d-accroche").value.trim(),
      entretien_date: document.getElementById("d-entretien-date").value || "",
      entretien_rappel_at: document.getElementById("d-entretien-rappel").value || "",
      entretien_next_step: document.getElementById("d-entretien-next").value.trim(),
    };
  }

  document.getElementById("d-status").addEventListener("change", (ev) => {
    toggleEntretienBox(ev.target.value);
  });

  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("overlay").addEventListener("click", closeOverlayPanels);
  document.getElementById("modal-add-close").addEventListener("click", () => closeAddModal());
  document.getElementById("modal-add-cancel").addEventListener("click", () => closeAddModal());
  document.getElementById("btn-add-entreprise").addEventListener("click", openAddModal);

  document.getElementById("modal-add-submit").addEventListener("click", async () => {
    const denomination = document.getElementById("add-denom").value.trim();
    if (!denomination) {
      toast("La dénomination est obligatoire", "error");
      document.getElementById("add-denom").focus();
      return;
    }
    const btn = document.getElementById("modal-add-submit");
    btn.disabled = true;
    try {
      const res = await fetch("/api/entreprises", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          denomination,
          siret: document.getElementById("add-siret").value.trim(),
          commune: document.getElementById("add-commune").value.trim(),
          adresse: document.getElementById("add-adresse").value.trim(),
          effectif_code: document.getElementById("add-effectif").value,
          categorie_entreprise: document.getElementById("add-categorie").value,
          naf_code: document.getElementById("add-naf").value.trim(),
          naf_libelle: document.getElementById("add-naf-lib").value.trim(),
          site_web: document.getElementById("add-site").value.trim(),
          linkedin_company: document.getElementById("add-li").value.trim(),
          notes: document.getElementById("add-notes").value.trim(),
        }),
      }).then((r) => r.json().then((data) => ({ ok: r.ok, status: r.status, data })));

      if (!res.ok) {
        toast(res.data.error || "Ajout impossible", "error");
        return;
      }
      toast(`« ${denomination} » ajoutée`);
      closeAddModal();
      await loadEntreprises();
      if (res.data.entreprise?.siret) openDrawer(res.data.entreprise.siret);
    } catch (err) {
      toast("Erreur réseau lors de l'ajout", "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("add-denom").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      document.getElementById("modal-add-submit").click();
    }
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (document.getElementById("modal-add").classList.contains("active")) {
      closeAddModal();
    } else if (document.getElementById("drawer").classList.contains("active")) {
      closeDrawer();
    }
  });

  document.getElementById("btn-save").addEventListener("click", async () => {
    const siret = document.getElementById("d-siret").value;
    const res = await fetch(`/api/entreprises/${siret}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(drawerPayload()),
    }).then((r) => r.json());
    if (res.error) return toast(res.error, "error");
    toast("Fiche enregistrée");
    closeDrawer();
    await loadEntreprises();
  });

  document.getElementById("btn-delete").addEventListener("click", async () => {
    const siret = document.getElementById("d-siret").value;
    if (!confirm("Supprimer cette entreprise de la base ?")) return;
    await fetch(`/api/entreprises/${siret}`, { method: "DELETE" });
    toast("Entreprise supprimée");
    closeDrawer();
    await loadEntreprises();
  });

  document.getElementById("btn-smtp").addEventListener("click", async () => {
    const btn = document.getElementById("btn-smtp");
    const domain = document.getElementById("d-site").value.trim();
    const prenom = document.getElementById("d-prenom").value.trim();
    const nom = document.getElementById("d-nom").value.trim();
    const siret = document.getElementById("d-siret").value;
    console.log("[btn-smtp] click", { domain, prenom, nom, siret });
    if (!domain || !prenom || !nom) {
      console.warn("[btn-smtp] champs manquants, requête annulée");
      toast("Site, prénom et nom requis", "error");
      return;
    }
    toast("Recherche SMTP…");
    btn.disabled = true;
    try {
      const response = await fetch("/api/manual/find-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, prenom, nom, siret }),
      });
      console.log("[btn-smtp] réponse HTTP", response.status);
      const res = await response.json();
      console.log("[btn-smtp] payload", res);
      if (res.error) return toast(res.error, "error");
      if (res.data && res.data.email) {
        document.getElementById("d-email").value = res.data.email;
        if (res.quality) renderEmailQuality(res.quality.quality, res.quality.note);
        toast(`Email trouvé : ${res.data.email} (score ${res.data.score})`);
      } else {
        toast("Aucun email trouvé", "error");
      }
    } catch (err) {
      console.error("[btn-smtp] échec de la requête", err);
      toast("Erreur réseau lors de la recherche SMTP", "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("btn-hunter").addEventListener("click", async () => {
    const domain = document.getElementById("d-site").value.trim();
    const prenom = document.getElementById("d-prenom").value.trim();
    const nom = document.getElementById("d-nom").value.trim();
    const siret = document.getElementById("d-siret").value;
    if (!domain || !prenom || !nom) {
      toast("Site, prénom et nom requis", "error");
      return;
    }
    toast("Recherche Hunter.io…");
    const res = await fetch("/api/hunter/find-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain,
        prenom,
        nom,
        siret,
        TOKEN_HUNTER_IO: state.config.TOKEN_HUNTER_IO,
      }),
    }).then((r) => r.json());
    if (res.error) return toast(res.error, "error");
    if (res.data && res.data.email) {
      document.getElementById("d-email").value = res.data.email;
      if (res.quality) renderEmailQuality(res.quality.quality, res.quality.note);
      toast(`Email trouvé : ${res.data.email} (score ${res.data.score})`);
    } else {
      toast("Aucun email trouvé", "error");
    }
  });

  document.getElementById("btn-accroche").addEventListener("click", async () => {
    const siret = document.getElementById("d-siret").value;
    toast("Génération accroche…");
    const res = await fetch("/api/mail/accroche", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        siret,
        denomination: document.getElementById("d-denom").textContent.trim(),
        site_web: document.getElementById("d-site").value.trim(),
        poste: document.getElementById("d-poste").value.trim(),
      }),
    }).then((r) => r.json());
    if (res.error) return toast(res.error, "error");
    document.getElementById("d-accroche").value = res.accroche || "";
    toast("Accroche générée");
  });

  document.getElementById("btn-check-replies").addEventListener("click", async () => {
    const btn = document.getElementById("btn-check-replies");
    btn.disabled = true;
    btn.textContent = "Vérification…";
    try {
      const res = await fetch("/api/check-replies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).then((r) => r.json());
      if (res.error) {
        toast(res.error, "error");
        return;
      }
      if (res.message && !res.updated) {
        toast(res.message);
      } else if (!res.updated) {
        toast(`Aucune nouvelle réponse (scanné ${res.scanned || 0} mail(s), non lus conservés)`);
      } else {
        const summary = (res.results || [])
          .map((r) => {
            const lbl = STATUS_LABELS[r.classification] || r.classification;
            const why = r.reason ? ` — ${r.reason}` : "";
            return `${r.denomination || r.siret} → ${lbl}${why}`;
          })
          .join(" · ");
        toast(`${res.updated} réponse(s) classée(s) : ${summary}`);
      }
      await loadEntreprises();
    } catch (err) {
      toast(String(err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Vérifier réponses";
    }
  });

  document.getElementById("btn-mail").addEventListener("click", async () => {
    const email = document.getElementById("d-email").value.trim();
    const nom = document.getElementById("d-nom").value.trim();
    const prenom = document.getElementById("d-prenom").value.trim();
    const genre = document.getElementById("d-genre").value;
    const poste = document.getElementById("d-poste").value.trim();
    const siret = document.getElementById("d-siret").value;
    const denomination = document.getElementById("d-denom").textContent.trim();
    const accroche = document.getElementById("d-accroche").value.trim();
    if (!email) return toast("Renseigne un email d'abord", "error");
    if (!confirm(`Envoyer le mail de candidature à ${email} ?`)) return;

    await fetch(`/api/entreprises/${siret}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(drawerPayload()),
    });

    async function doSend({ force = false, accept_warn = false } = {}) {
      return fetch("/api/send-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          nom,
          prenom,
          genre,
          poste,
          denomination,
          siret,
          accroche,
          force,
          accept_warn,
          auto_accroche: !accroche,
        }),
      }).then(async (r) => ({ status: r.status, ...(await r.json()) }));
    }

    let res = await doSend();
    if (res.needs_confirm && res.quality) {
      if (!confirm(`Email douteux (${res.quality.note}). Envoyer quand même ?`)) return;
      res = await doSend({ accept_warn: true });
    }
    if (res.needs_force && res.quality) {
      if (!confirm(`Email de mauvaise qualité (${res.quality.note}). Forcer l'envoi ?`)) return;
      res = await doSend({ force: true, accept_warn: true });
    }
    if (res.error) return toast(res.error, "error");
    if (res.accroche) document.getElementById("d-accroche").value = res.accroche;
    toast("Mail envoyé — statut → Postulé");
    document.getElementById("d-status").value = "postule";
    closeDrawer();
    await loadEntreprises();
  });

  document.getElementById("btn-relance").addEventListener("click", async () => {
    const email = document.getElementById("d-email").value.trim();
    const nom = document.getElementById("d-nom").value.trim();
    const prenom = document.getElementById("d-prenom").value.trim();
    const genre = document.getElementById("d-genre").value;
    const poste = document.getElementById("d-poste").value.trim();
    const siret = document.getElementById("d-siret").value;
    const denomination = document.getElementById("d-denom").textContent.trim();
    const e = state.entreprises.find((x) => x.siret === siret);
    const ri = (e && e.relance_info) || {};
    if (!email) return toast("Renseigne un email d'abord", "error");

    let force = false;
    if (ri.blocked) {
      return toast(ri.reason || "Relance bloquée", "error");
    }
    if (!ri.due) {
      if (!confirm(`${ri.reason || "Trop tôt"}. Forcer la relance quand même ?`)) return;
      force = true;
    } else if (!confirm(`Envoyer la relance n°${ri.angle || 1} à ${email} ?`)) {
      return;
    }

    await fetch(`/api/entreprises/${siret}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(drawerPayload()),
    });

    const res = await fetch("/api/send-relance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        nom,
        prenom,
        genre,
        poste,
        denomination,
        siret,
        force,
      }),
    }).then(async (r) => ({ status: r.status, ...(await r.json()) }));

    if (res.needs_force && !force) {
      if (!confirm(`${res.error}. Forcer ?`)) return;
      const res2 = await fetch("/api/send-relance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email, nom, prenom, genre, poste, denomination, siret, force: true,
        }),
      }).then((r) => r.json());
      if (res2.error) return toast(res2.error, "error");
      toast(`Relance n°${res2.relance_number || "?"} envoyée (angle ${res2.angle || "?"})`);
    } else if (res.error) {
      return toast(res.error, "error");
    } else {
      const threaded = res.threaded ? " (fil Gmail)" : "";
      toast(`Relance n°${res.relance_number || "?"} envoyée${threaded}`);
    }
    document.getElementById("d-status").value = "relance";
    closeDrawer();
    await loadEntreprises();
  });

  document.getElementById("btn-save-cfg").addEventListener("click", async () => {
    const selectedNafs = {};
    document.querySelectorAll("#cfg-naf-list input:checked").forEach((cb) => {
      selectedNafs[cb.value] = state.nafs[cb.value] || cb.value;
    });
    const payload = {
      INSEE_TOKEN: document.getElementById("cfg-insee").value.trim(),
      SERPAPI_KEY: document.getElementById("cfg-serp").value.trim(),
      TOKEN_HUNTER_IO: document.getElementById("cfg-hunter").value.trim(),
      candidate_name: document.getElementById("cfg-candidate-name").value.trim(),
      point_ref: document.getElementById("cfg-point").value.trim(),
      rayon_km: document.getElementById("cfg-rayon").value,
      departements: document.getElementById("cfg-depts").value.trim(),
      mail_subject: document.getElementById("cfg-mail-subject").value.trim(),
      mail_body: document.getElementById("cfg-mail-body").value,
      relance_body: document.getElementById("cfg-relance-body").value,
      relance2_body: (document.getElementById("cfg-relance2-body") || {}).value || "",
      nafs: Object.keys(selectedNafs).length ? selectedNafs : state.nafs,
    };
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast("Configuration sauvegardée");
    await loadConfig();
  });

  function renderCvProfileStatus(profile) {
    const el = document.getElementById("cv-profile-status");
    if (!el) return;
    if (!profile) {
      el.textContent = "Extrait des mots-clés de cv.pdf pour affiner le score de pertinence des entreprises.";
      return;
    }
    const n = (profile.keywords || []).length;
    el.textContent = `${n} mot(s)-clé(s) extrait(s) (${profile.source}) — ${new Date(profile.extracted_at).toLocaleString()}`;
  }

  document.getElementById("btn-analyze-cv")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-analyze-cv");
    btn.disabled = true;
    const prevText = btn.textContent;
    btn.textContent = "Analyse en cours…";
    try {
      const res = await fetch("/api/cv/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      }).then((r) => r.json());
      if (res.error) return toast(res.error, "error");
      renderCvProfileStatus(res);
      toast(`CV analysé — ${(res.keywords || []).length} mot(s)-clé(s) (${res.source})`);
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prevText;
    }
  });

  async function loadDbBackend() {
    const res = await fetch("/api/db/backend").then((r) => r.json());
    const select = document.getElementById("db-backend-select");
    const status = document.getElementById("db-backend-status");
    const fields = document.getElementById("db-backend-fields");
    const saveBtn = document.getElementById("btn-db-backend-save");
    if (!select || !status || !fields) return;

    select.value = res.backend || "sqlite";
    fields.style.display = res.backend === "sqlite" ? "none" : "block";
    document.getElementById("db-host").value = res.host || "";
    document.getElementById("db-port").value = res.port || "";
    document.getElementById("db-user").value = res.user || "";
    document.getElementById("db-dbname").value = res.dbname || "";

    if (res.locked) {
      status.textContent = `Backend actuel : ${res.backend} (imposé par variable d'environnement DB_BACKEND).`;
      select.disabled = true;
      if (saveBtn) saveBtn.disabled = true;
    } else {
      status.textContent = `Backend actuel : ${res.backend} (source : ${res.source === "file" ? "config locale" : "défaut"}).`;
      select.disabled = false;
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  document.getElementById("db-backend-select")?.addEventListener("change", (e) => {
    const fields = document.getElementById("db-backend-fields");
    if (fields) fields.style.display = e.target.value === "sqlite" ? "none" : "block";
  });

  document.getElementById("btn-db-backend-save")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-db-backend-save");
    const backend = document.getElementById("db-backend-select").value;
    const payload = {
      backend,
      host: document.getElementById("db-host").value.trim(),
      port: document.getElementById("db-port").value.trim(),
      user: document.getElementById("db-user").value.trim(),
      password: document.getElementById("db-password").value,
      dbname: document.getElementById("db-dbname").value.trim(),
    };
    btn.disabled = true;
    const prevText = btn.textContent;
    btn.textContent = "Test de connexion…";
    try {
      const res = await fetch("/api/db/backend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (res.error) return toast(res.error, "error");
      toast(`Backend DB changé : ${res.backend}`);
      await loadDbBackend();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prevText;
    }
  });

  function addNafCode(code, lib, { checkBoxId } = {}) {
    if (!code) return toast("Code NAF requis", "error");
    state.nafs[code] = lib || code;
    renderNafs();
    renderCfgNafs();
    ["naf-list", "cfg-naf-list"].forEach((boxId) => {
      if (checkBoxId && boxId !== checkBoxId) return;
      const input = document.querySelector(`#${boxId} input[value="${CSS.escape(code)}"]`);
      if (input) input.checked = true;
    });
    toast(`NAF ${code} ajouté`);
  }

  function wireNafSearch(inputId, resultsBoxId, checkBoxId) {
    const input = document.getElementById(inputId);
    const box = document.getElementById(resultsBoxId);
    let timer = null;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) {
        box.innerHTML = "";
        return;
      }
      timer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/naf/search?q=${encodeURIComponent(q)}`);
          const data = await res.json();
          const results = data.results || [];
          box.innerHTML = "";
          if (!results.length) {
            box.innerHTML = `<p class="help">Aucun résultat — essaie un autre mot, ou saisis le code NAF directement ci-dessous si tu le connais.</p>`;
            return;
          }
          results.forEach((r) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn btn-secondary";
            btn.style.width = "100%";
            btn.style.textAlign = "left";
            const strong = document.createElement("strong");
            strong.textContent = r.code;
            btn.appendChild(strong);
            btn.appendChild(document.createTextNode(` — ${r.libelle}`));
            btn.addEventListener("click", () => {
              addNafCode(r.code, r.libelle, { checkBoxId });
              input.value = "";
              box.innerHTML = "";
            });
            box.appendChild(btn);
          });
        } catch (err) {
          box.innerHTML = `<p class="help">Recherche indisponible.</p>`;
        }
      }, 250);
    });
  }

  wireNafSearch("naf-search", "naf-search-results", "naf-list");
  wireNafSearch("cfg-naf-search", "cfg-naf-search-results", "cfg-naf-list");

  document.getElementById("btn-cfg-add-naf").addEventListener("click", () => {
    const code = document.getElementById("cfg-naf-code").value.trim();
    const lib = document.getElementById("cfg-naf-lib").value.trim() || code;
    addNafCode(code, lib, { checkBoxId: "cfg-naf-list" });
    document.getElementById("cfg-naf-code").value = "";
    document.getElementById("cfg-naf-lib").value = "";
  });

  document.getElementById("btn-add-naf").addEventListener("click", () => {
    const code = document.getElementById("naf-code").value.trim();
    const lib = document.getElementById("naf-lib").value.trim() || code;
    addNafCode(code, lib, { checkBoxId: "naf-list" });
    document.getElementById("naf-code").value = "";
    document.getElementById("naf-lib").value = "";
  });

  async function runPrune({ silent = false } = {}) {
    const minEmployees = Number(document.getElementById("prune-min-emp").value || 3);
    const maxTravel = Number(document.getElementById("prune-max-travel").value || 45);
    const force = document.getElementById("prune-force").checked;
    const deleteUnknownEmployees = document.getElementById("prune-delete-unknown-emp")?.checked || false;
    const btn = document.getElementById("btn-prune");
    if (!silent) {
      btn.disabled = true;
      btn.innerHTML = `<span class="spinner"></span> Prune en cours…`;
      toast(
        `Prune — effectif ≥ ${minEmployees}${deleteUnknownEmployees ? ", NN supprimés" : ", NN conservés"}, trajet ≤ ${maxTravel} min…`
      );
    }
    try {
      const res = await startJob("/api/prune", {
        min_employees: minEmployees,
        max_travel_min: maxTravel,
        origin: document.getElementById("scan-point").value.trim() || currentTravelOrigin(),
        force_recompute: force,
        delete_unknown_employees: deleteUnknownEmployees,
      });
      toast(
        `Prune OK — ${res.initial} → ${res.final}` +
          ` (−${res.deleted_lt_min_employees} effectif, −${res.deleted_gt_max_travel} trajet` +
          (res.skipped_protected ? `, ${res.skipped_protected} candidature(s) protégée(s)` : "") +
          (res.travel_unavailable ? `, ${res.travel_unavailable} sans GPS` : "") +
          `)`
      );
      return res;
    } catch (err) {
      toast(String(err.message || err), "error");
      return null;
    } finally {
      if (!silent) {
        btn.disabled = false;
        btn.textContent = "Lancer le prune";
      }
    }
  }

  document.getElementById("btn-prune").addEventListener("click", async () => {
    const res = await runPrune();
    if (res) {
      await loadEntreprises();
      document.querySelector('.nav-btn[data-page="entreprises"]').click();
    }
  });

  document.getElementById("btn-hors-champs").addEventListener("click", async () => {
    const btn = document.getElementById("btn-hors-champs");
    if (!confirm("Marquer en « hors champs » toutes les entreprises à postuler dont le NAF ou le thème n'est pas informatique ?")) {
      return;
    }
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Analyse…`;
    try {
      const res = await fetch("/api/mark-hors-champs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).then((r) => r.json());
      if (res.error) {
        toast(res.error, "error");
        return;
      }
      toast(`Hors champs — ${res.marked} marquée(s), ${res.skipped} conservée(s) à postuler`);
      await loadEntreprises();
      document.querySelector('.nav-btn[data-page="entreprises"]').click();
    } finally {
      btn.disabled = false;
      btn.textContent = "Marquer hors champs (NAF / thème non IT)";
    }
  });

  document.getElementById("scan-national").addEventListener("change", (e) => {
    const disabled = e.target.checked;
    ["scan-point", "scan-rayon", "scan-depts"].forEach((id) => {
      document.getElementById(id).disabled = disabled;
    });
  });

  document.getElementById("btn-sirene").addEventListener("click", async () => {
    const btn = document.getElementById("btn-sirene");
    const selected = {};
    document.querySelectorAll("#naf-list input:checked").forEach((cb) => {
      selected[cb.value] = state.nafs[cb.value] || cb.value;
    });
    const includeMairies = document.getElementById("scan-mairies").checked;
    const includeAssociations = document.getElementById("scan-associations").checked;
    if (!Object.keys(selected).length && !includeMairies && !includeAssociations) {
      return toast("Sélectionne au moins un NAF, ou coche mairies / associations", "error");
    }

    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Scan Sirene…`;

    try {
      const national = document.getElementById("scan-national").checked;
      const res = await startJob("/api/scan/sirene", {
        national,
        point_ref: document.getElementById("scan-point").value.trim(),
        rayon_km: document.getElementById("scan-rayon").value,
        departements: document.getElementById("scan-depts").value,
        nafs: selected,
        include_mairies: includeMairies,
        include_associations: includeAssociations,
        INSEE_TOKEN: state.config.INSEE_TOKEN,
      });

      const clean = res.clean || {};
      const extras = [
        res.added_mairies ? `+${res.added_mairies} mairie(s)` : "",
        res.added_associations ? `+${res.added_associations} association(s)` : "",
      ]
        .filter(Boolean)
        .join(", ");
      toast(
        `Scan OK — +${res.added} brutes` +
          (extras ? ` (${extras})` : "") +
          ` → ${clean.after ?? "?"} après nettoyage` +
          (clean.excluded_effectif ? ` (−${clean.excluded_effectif} petites)` : "")
      );

      if (document.getElementById("scan-auto-prune").checked) {
        btn.innerHTML = `<span class="spinner"></span> Prune…`;
        await runPrune({ silent: true });
      }
      await loadEntreprises();
      document.querySelector('.nav-btn[data-page="entreprises"]').click();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Lancer le scan Sirene + nettoyage";
    }
  });

  document.getElementById("btn-clean").addEventListener("click", async () => {
    const btn = document.getElementById("btn-clean");
    btn.disabled = true;
    toast("Nettoyage en cours…");
    const res = await fetch("/api/clean", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apply_effectif_filter: true }),
    }).then((r) => r.json());
    btn.disabled = false;
    if (res.error) return toast(res.error, "error");
    toast(`Nettoyage : ${res.before} → ${res.after} (−${res.excluded_effectif} effectif, −${res.duplicates_removed} doublons)`);
    await loadEntreprises();
  });

  document.getElementById("btn-frenchtech").addEventListener("click", async () => {
    const btn = document.getElementById("btn-frenchtech");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Import French Tech…`;
    toast("Import French Tech Toulon en cours (peut prendre 1–2 min)…");
    try {
      const res = await startJob("/api/scan/frenchtech", {
        tech_only: document.getElementById("ft-tech-only").checked,
        resolve_siret: document.getElementById("ft-resolve-siret").checked,
        resolve_emails: document.getElementById("ft-resolve-emails").checked,
      });
      toast(
        `French Tech — ${res.scraped} lues, +${res.added} ajoutées` +
          (res.matched_existing ? `, ${res.matched_existing} déjà en base enrichies` : "") +
          (res.unresolved_siret ? `, ${res.unresolved_siret} sans SIRET` : "")
      );
      await loadEntreprises();
      document.querySelector('.nav-btn[data-page="entreprises"]').click();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Importer French Tech Toulon";
    }
  });

  async function scanOneEntreprise(siret, { reopen = false } = {}) {
    if (!siret) return;
    const ent = state.entreprises.find((x) => x.siret === siret);
    const name = ent?.denomination || siret;
    toast(`Scan SerpAPI — ${name}…`);

    const res = await fetch(`/api/scan/serpapi/${encodeURIComponent(siret)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        SERPAPI_KEY: state.config.SERPAPI_KEY,
        use_ollama: true,
      }),
    }).then((r) => r.json());

    if (res.error) {
      toast(res.error, "error");
      return;
    }

    toast(`Scan OK — ${name}${res.ollama ? " (Ollama)" : ""}`);
    await loadEntreprises();
    if (reopen || document.getElementById("drawer").classList.contains("active")) {
      openDrawer(siret);
    }
  }

  document.getElementById("btn-scan-one").addEventListener("click", async () => {
    const siret = document.getElementById("d-siret").value;
    const btn = document.getElementById("btn-scan-one");
    btn.disabled = true;
    btn.textContent = "Scan en cours…";
    await scanOneEntreprise(siret, { reopen: true });
    btn.disabled = false;
    btn.textContent = "Scanner cette entreprise (SerpAPI + Ollama)";
  });

  async function runSerpApi() {
    toast("SerpAPI + Ollama (1 req Google / entreprise)…");
    try {
      const res = await startJob("/api/scan/serpapi", {
        SERPAPI_KEY: state.config.SERPAPI_KEY,
        use_ollama: true,
      });
      toast(`SerpAPI terminé — ${res.scanned} entreprise(s)${res.ollama ? " (Ollama OK)" : " (sans Ollama)"}`);
      await loadEntreprises();
    } catch (err) {
      toast(String(err.message || err), "error");
    }
  }

  document.getElementById("btn-serpapi").addEventListener("click", runSerpApi);
  document.getElementById("btn-serpapi-2").addEventListener("click", runSerpApi);

  async function runDirigeants() {
    toast("Récupération des dirigeants (API Recherche d'Entreprises)…");
    try {
      const res = await startJob("/api/scan/dirigeants", {});
      toast(
        `Dirigeants — ${res.filled} trouvé(s), ${res.not_found} sans personne physique, ${res.errors} erreur(s)`
      );
      await loadEntreprises();
    } catch (err) {
      toast(String(err.message || err), "error");
    }
  }

  document.getElementById("btn-dirigeants").addEventListener("click", runDirigeants);
  document.getElementById("btn-dirigeants-2").addEventListener("click", runDirigeants);

  async function runContacts(limit) {
    toast(`Recherche contacts RH/tech (max ${limit || 30})…`);
    const body = {
      SERPAPI_KEY: state.config.SERPAPI_KEY,
      use_ollama: false,
      limit: limit || 30,
    };
    const buttons = [document.getElementById("btn-contacts"), document.getElementById("btn-contacts-2")].filter(Boolean);
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const res = await startJob("/api/scan/contacts", body);
      toast(
        `Contacts — ${res.filled} trouvé(s), ${res.not_found} sans profil, ${res.skipped} ignoré(s), ${res.errors} erreur(s) (${res.processed} traitées)`
      );
      await loadEntreprises();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  const btnContacts = document.getElementById("btn-contacts");
  const btnContacts2 = document.getElementById("btn-contacts-2");
  if (btnContacts) btnContacts.addEventListener("click", () => runContacts(15));
  if (btnContacts2) btnContacts2.addEventListener("click", () => runContacts(30));

  document.getElementById("btn-relances-dues")?.addEventListener("click", async () => {
    const res = await fetch("/api/relances/dues").then((r) => r.json());
    if (res.error) return toast(res.error, "error");
    if (!res.count) return toast("Aucune relance due pour le moment");
    const names = (res.relances || []).slice(0, 8).map((r) => r.denomination).join(" · ");
    toast(`${res.count} relance(s) due(s) : ${names}`);
    state.filter = "postule";
    state.search = "";
    document.getElementById("search").value = "";
    const first = res.relances[0];
    await loadEntreprises({ resetPage: true });
    if (first) await openDrawer(first.siret);
  });

  document.getElementById("btn-dirigeant-one").addEventListener("click", async () => {
    const siret = document.getElementById("d-siret").value;
    const btn = document.getElementById("btn-dirigeant-one");
    btn.disabled = true;
    btn.textContent = "Recherche en cours…";
    toast("Recherche du dirigeant…");
    const res = await fetch(`/api/scan/dirigeants/${encodeURIComponent(siret)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: true }),
    }).then((r) => r.json());
    btn.disabled = false;
    btn.textContent = "Récupérer le dirigeant (API gouv)";
    if (res.error) return toast(res.error, "error");
    if (res.found) {
      document.getElementById("d-prenom").value = res.contact_prenom || "";
      document.getElementById("d-nom").value = res.contact_nom || "";
      document.getElementById("d-poste").value = res.contact_poste || "";
      document.getElementById("d-contact-source").value = "dirigeant";
      toast(`${res.contact_prenom} ${res.contact_nom} — ${res.contact_poste || "dirigeant"}`);
    } else if (res.skipped) {
      toast(res.reason || "Déjà renseigné");
    } else {
      toast("Aucun dirigeant personne physique trouvé", "error");
    }
    await loadEntreprises();
  });

  document.getElementById("btn-contact-one")?.addEventListener("click", async () => {
    const siret = document.getElementById("d-siret").value;
    const btn = document.getElementById("btn-contact-one");
    btn.disabled = true;
    btn.textContent = "Recherche RH/tech…";
    toast("Recherche contact RH/tech…");
    try {
      const res = await fetch(`/api/scan/contacts/${encodeURIComponent(siret)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true, SERPAPI_KEY: state.config.SERPAPI_KEY, use_ollama: false }),
      }).then((r) => r.json());
      if (res.error) return toast(res.error, "error");
      if (res.found) {
        document.getElementById("d-prenom").value = res.contact_prenom || "";
        document.getElementById("d-nom").value = res.contact_nom || "";
        document.getElementById("d-poste").value = res.contact_poste || "";
        document.getElementById("d-li-contact").value = res.contact_linkedin || "";
        document.getElementById("d-contact-source").value = "linkedin_search";
        toast(`${res.contact_prenom} ${res.contact_nom} — ${res.contact_poste || "contact"}`);
      } else if (res.skipped) {
        toast(res.reason || "Ignoré");
      } else {
        toast(res.message || "Aucun profil RH/tech trouvé", "error");
      }
      await loadEntreprises({ skipMap: true });
      openDrawer(siret);
    } catch (err) {
      toast(String(err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Chercher contact RH / tech (SerpAPI)";
    }
  });

  document.getElementById("btn-refresh").addEventListener("click", async () => {
    await loadEntreprises();
    await loadNafCodesUsed();
  });

  let searchDebounce = null;
  document.getElementById("search").addEventListener("input", (e) => {
    state.search = e.target.value;
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      loadEntreprises({ resetPage: true });
      maybeRefreshTravelFilter();
    }, 300);
  });

  document.getElementById("btn-reset-db").addEventListener("click", async () => {
    if (!confirm("Vider toutes les entreprises ? (config conservée)")) return;
    await fetch("/api/db/reset", { method: "POST" });
    toast("Base entreprises vidée");
    await loadEntreprises({ resetPage: true });
    await loadNafCodesUsed();
  });

  (async () => {
    await loadConfig();
    await loadNafCodesUsed();
    await loadEntreprises();
  })();
