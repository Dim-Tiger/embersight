"""Critical-infrastructure replacements for the decommissioned HIFLD portal
(see README §HIFLD CORRECTION).

  - Hospitals: HIFLD Open / CMS Provider of Services (NBI Hospitals layer)
  - Schools: NCES Common Core of Data (Public Schools layer)
  - Electric transmission: HIFLD archive on DataLumos (DOI 10.3886/E241367V1)
    + EIA US Electric Power Transmission Lines
  - Critical facilities: fire stations, EOCs, comm towers

All ArcGIS FeatureServer endpoints accept an envelope geometry filter
in EPSG:4326. We pass `(minx, miny, maxx, maxy)` tuples throughout and
return plain dicts/lists for JSON-safe handoff to the LLM prompt.
"""

from __future__ import annotations

from typing import Any

import httpx

HOSPITALS_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/"
    "Hospital/FeatureServer/0/query"
)

SCHOOLS_URL = (
    "https://services1.arcgis.com/Ua5sjt3LWTPigjyD/ArcGIS/rest/services/"
    "Public_School_Location_201819/FeatureServer/0/query"
)

TRANSMISSION_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/"
    "Electric_Power_Transmission_Lines/FeatureServer/0/query"
)

FIRE_STATIONS_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/"
    "Fire_Station/FeatureServer/0/query"
)

EOC_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/"
    "Emergency_Operations_Center_EOC/FeatureServer/0/query"
)

COMM_TOWER_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/"
    "Cellular_Towers/FeatureServer/0/query"
)


def _envelope(bbox: tuple[float, float, float, float]) -> str:
    minx, miny, maxx, maxy = bbox
    return (
        f'{{"xmin":{minx},"ymin":{miny},"xmax":{maxx},"ymax":{maxy},'
        '"spatialReference":{"wkid":4326}}'
    )


def _fetch_features(
    url: str, bbox: tuple[float, float, float, float], out_fields: str
) -> dict[str, Any]:
    params = {
        "where": "1=1",
        "geometry": _envelope(bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "1000",
        "f": "geojson",
    }
    with httpx.Client(timeout=20) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    # ArcGIS returns HTTP 200 with an `error` envelope when the layer is
    # missing or unauthorized. Treat that as a real failure.
    if isinstance(data, dict) and "error" in data and "features" not in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"arcgis:{msg}")
    return data


def _point_coords(geom: dict[str, Any] | None) -> tuple[float, float] | None:
    if not geom:
        return None
    coords = geom.get("coordinates")
    if not coords:
        return None
    try:
        if geom.get("type") == "Point":
            return float(coords[0]), float(coords[1])
        # Polygon / MultiPolygon -> first vertex as cheap proxy
        ring = coords
        while isinstance(ring, list) and ring and isinstance(ring[0], list):
            ring = ring[0]
        return float(ring[0]), float(ring[1])
    except Exception:  # noqa: BLE001
        return None


def query_hospitals(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """HIFLD/CMS hospitals in bbox. Returns list of dicts."""
    try:
        data = _fetch_features(
            HOSPITALS_URL, bbox, "NAME,BEDS,TYPE,STATUS,TRAUMA"
        )
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"http:{exc}"}]
    out: list[dict[str, Any]] = []
    for feat in data.get("features", []) or []:
        props = feat.get("properties", {}) or {}
        lonlat = _point_coords(feat.get("geometry"))
        if lonlat is None:
            continue
        lon, lat = lonlat
        beds = props.get("BEDS")
        try:
            beds_i = int(beds) if beds not in (None, -999, "-999") else 0
        except (TypeError, ValueError):
            beds_i = 0
        out.append(
            {
                "name": props.get("NAME") or "Unknown",
                "lat": lat,
                "lon": lon,
                "beds": beds_i,
                "type": props.get("TYPE") or "Unknown",
            }
        )
    return out


def query_schools(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """NCES Common Core of Data schools in bbox."""
    try:
        data = _fetch_features(
            SCHOOLS_URL, bbox, "NAME,ENROLLMENT,LEVEL_,STATUS"
        )
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"http:{exc}"}]
    out: list[dict[str, Any]] = []
    for feat in data.get("features", []) or []:
        props = feat.get("properties", {}) or {}
        lonlat = _point_coords(feat.get("geometry"))
        if lonlat is None:
            continue
        lon, lat = lonlat
        enr = props.get("ENROLLMENT")
        try:
            enr_i = int(enr) if enr not in (None, -999, "-999") else 0
        except (TypeError, ValueError):
            enr_i = 0
        out.append(
            {
                "name": props.get("NAME") or "Unknown",
                "lat": lat,
                "lon": lon,
                "enrollment": enr_i,
            }
        )
    return out


def query_transmission_lines(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """EIA US Electric Power Transmission Lines intersecting bbox."""
    try:
        data = _fetch_features(
            TRANSMISSION_URL, bbox, "VOLTAGE,OWNER,TYPE,STATUS,VOLT_CLASS"
        )
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"http:{exc}"}]
    out: list[dict[str, Any]] = []
    for feat in data.get("features", []) or []:
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry") or {}
        # Re-serialize LineString/MultiLineString to a minimal WKT-ish string.
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        geom_repr = f"{gtype}({len(coords)} parts)" if gtype else ""
        try:
            voltage = float(props.get("VOLTAGE") or 0)
        except (TypeError, ValueError):
            voltage = 0.0
        out.append(
            {
                "voltage_kv": voltage,
                "owner": props.get("OWNER") or "Unknown",
                "type": props.get("TYPE") or "Unknown",
                "geom_wkt": geom_repr,
            }
        )
    return out


def query_critical_facilities(
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Composite query: fire stations + EOCs + comm towers in bbox."""
    out: dict[str, Any] = {"fire_stations": [], "eocs": [], "comm_towers": []}

    for key, url, fields in (
        ("fire_stations", FIRE_STATIONS_URL, "NAME,ADDRESS,CITY"),
        ("eocs", EOC_URL, "NAME,COUNTY,STATE"),
        ("comm_towers", COMM_TOWER_URL, "STRUC_TYPE,OWNER,HEIGHT"),
    ):
        try:
            data = _fetch_features(url, bbox, fields)
        except Exception as exc:  # noqa: BLE001
            out[key] = [{"error": f"http:{exc}"}]
            continue
        rows: list[dict[str, Any]] = []
        for feat in data.get("features", []) or []:
            props = feat.get("properties", {}) or {}
            lonlat = _point_coords(feat.get("geometry"))
            if lonlat is None:
                continue
            lon, lat = lonlat
            rows.append(
                {
                    "name": props.get("NAME")
                    or props.get("STRUC_TYPE")
                    or "Unknown",
                    "lat": lat,
                    "lon": lon,
                    **{k: v for k, v in props.items() if k not in ("NAME",)},
                }
            )
        out[key] = rows
    out["total"] = sum(
        len(v) for v in (out["fire_stations"], out["eocs"], out["comm_towers"])
    )
    return out
