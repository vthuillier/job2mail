  export const STATUS_LABELS = {
    tous: "Tous",
    a_postuler: "À postuler",
    hors_champs: "Hors champs",
    postule: "Postulé",
    relance: "Relance",
    entretien: "Entretien",
    refus: "Refus",
    offre: "Offre",
  };

  export const STATUS_COLORS = {
    a_postuler: "#fbbf24",
    hors_champs: "#8b9aab",
    postule: "#38bdf8",
    relance: "#2dd4bf",
    entretien: "#34d399",
    refus: "#fb7185",
    offre: "#34d399",
  };

  export const TRANCHE_EFFECTIFS = {
    NN: "Non renseigné",
    "00": "0 salarié",
    "01": "1 ou 2 salariés",
    "02": "3 à 5 salariés",
    "03": "6 à 9 salariés",
    11: "10 à 19 salariés",
    12: "20 à 49 salariés",
    21: "50 à 99 salariés",
    22: "100 à 199 salariés",
    31: "200 à 249 salariés",
    32: "250 à 499 salariés",
    41: "500 à 999 salariés",
    42: "1 000 à 1 999 salariés",
    51: "2 000 à 4 999 salariés",
    52: "5 000 à 9 999 salariés",
    53: "10 000 salariés et plus",
  };

  export const state = {
    entreprises: [],
    filter: "tous",
    search: "",
    config: {},
    nafs: {},
    view: "list",
    map: null,
    mapLayer: null,
    mapReady: false,
    geocodePromise: null,
    advFiltersOpen: false,
    travelLoading: false,
    advFilters: {
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
    },
  };

  export function toast(msg, type = "ok") {
    const el = document.createElement("div");
    el.className = `toast${type === "error" ? " error" : ""}`;
    el.textContent = msg;
    document.getElementById("toasts").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  export function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  export function normalizeText(value) {
    return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
  }

  export function currentTravelOrigin() {
    return (state.config.point_ref || "La Crau").trim() || "La Crau";
  }

  export function formatTravelInfo(e) {
    const originOk = normalizeText(e.travel_origin) === normalizeText(currentTravelOrigin());
    const noTolls = e.travel_without_tolls === 1 || e.travel_without_tolls === true;
    if (!originOk || !noTolls || e.travel_duration_min == null || e.travel_distance_km == null) return "";
    const minutes = Math.round(Number(e.travel_duration_min));
    const distance = Number(e.travel_distance_km).toFixed(1);
    return `Trajet sans péage depuis ${currentTravelOrigin()} : ${minutes} min · ${distance} km`;
  }

  