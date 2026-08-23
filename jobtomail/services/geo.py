"""Géolocalisation des communes (API Géo)."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import requests

from jobtomail.constants import BAN_API_URL, GEO_API_URL

logger = logging.getLogger(__name__)

CommuneInfo = dict[str, str | float]
IGN_ROUTE_URL = "https://data.geopf.fr/navigation/itineraire"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def get_commune_centre(nom: str) -> tuple[float, float] | None:
    res = requests.get(
        GEO_API_URL,
        params={"nom": nom, "fields": "centre", "boost": "population"},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    if not data:
        return None
    centre = data[0].get("centre", {}).get("coordinates")
    if not centre:
        return None
    lon, lat = centre
    return lat, lon


def geocode_adresse(adresse: str | None, commune: str | None) -> tuple[float, float] | None:
    """Géocode une adresse via la BAN, avec repli sur le centre de la commune."""
    queries: list[str] = []
    if adresse and adresse.strip():
        queries.append(adresse.strip())
        if commune and commune.strip():
            queries.append(f"{adresse.strip()}, {commune.strip()}")
    elif commune and commune.strip():
        queries.append(commune.strip())

    for q in queries:
        for attempt in range(3):
            try:
                res = requests.get(BAN_API_URL, params={"q": q, "limit": 1}, timeout=12)
                if res.status_code == 429 or res.status_code >= 500:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                res.raise_for_status()
                features = res.json().get("features") or []
                if features:
                    lon, lat = features[0]["geometry"]["coordinates"]
                    return lat, lon
                break
            except Exception:
                if attempt == 2:
                    logger.exception("Échec géocodage BAN pour %r", q)
                else:
                    time.sleep(0.4 * (attempt + 1))

    if commune and commune.strip():
        return get_commune_centre(commune.strip())
    return None


def normalize_location_label(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def get_route_info(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    *,
    avoid_tolls: bool = True,
) -> dict[str, Any] | None:
    """Calcule distance et durée routières via l'API IGN."""
    params = {
        "resource": "bdtopo-pgr",
        "profile": "car",
        "optimization": "fastest",
        "start": f"{start_lon},{start_lat}",
        "end": f"{end_lon},{end_lat}",
        "geometryFormat": "geojson",
        "timeUnit": "minute",
        "distanceUnit": "kilometer",
    }
    if avoid_tolls:
        params["exclusions"] = "Toll"

    for attempt in range(3):
        try:
            res = requests.get(IGN_ROUTE_URL, params=params, timeout=25)
            if res.status_code == 429 or res.status_code >= 500:
                time.sleep(0.5 * (attempt + 1))
                continue
            res.raise_for_status()
            data = res.json()
            duration = data.get("duration")
            distance = data.get("distance")
            if duration is None or distance is None:
                return None
            return {
                "duration_min": float(duration),
                "distance_km": float(distance),
                "raw": data,
            }
        except Exception:
            if attempt == 2:
                logger.exception(
                    "Échec calcul itinéraire IGN lat/lon %s,%s -> %s,%s",
                    start_lat,
                    start_lon,
                    end_lat,
                    end_lon,
                )
            else:
                time.sleep(0.5 * (attempt + 1))
    return None


def get_communes_dans_rayon(
    point_ref_nom: str,
    rayon_km: float,
    departements: list[str],
) -> dict[str, CommuneInfo]:
    logger.info(
        "Recherche communes autour de '%s' (rayon=%skm, depts=%s)",
        point_ref_nom,
        rayon_km,
        departements,
    )
    res = requests.get(
        GEO_API_URL,
        params={"nom": point_ref_nom, "fields": "centre,nom,code", "boost": "population"},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    if not data:
        raise ValueError(f"Commune '{point_ref_nom}' introuvable")

    centre = data[0]["centre"]["coordinates"]
    lat_ref, lon_ref = centre[1], centre[0]
    logger.debug("Point ref GPS : lat=%s lon=%s", lat_ref, lon_ref)

    communes: dict[str, CommuneInfo] = {}
    for dept in departements:
        r = requests.get(
            GEO_API_URL,
            params={"codeDepartement": dept, "fields": "nom,code,centre", "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        for commune in r.json():
            c_centre = commune.get("centre")
            if not c_centre:
                continue
            lon, lat = c_centre["coordinates"]
            if haversine_km(lat_ref, lon_ref, lat, lon) <= rayon_km:
                communes[commune["code"]] = {
                    "nom": commune["nom"],
                    "lat": lat,
                    "lon": lon,
                }

    logger.info("%d commune(s) dans le rayon", len(communes))
    return communes
