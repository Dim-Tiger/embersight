/**
 * Single source of truth for critical-infrastructure map layers.
 *
 * Each entry describes (a) where to fetch the data from, (b) how to
 * normalize it into a flat point/line GeoJSON FeatureCollection, and
 * (c) how to draw + group it in the Legend.
 *
 * The web/app/api/poi/route.ts proxy reads INFRA_SOURCES; the map +
 * Legend read INFRA_LAYERS. Adding a new layer = one entry here.
 */

export type InfraGroup = "lifeSafety" | "response" | "infrastructure";

export type InfraGeometryKind = "point" | "line";

/** Server-side fetch + normalize definition (used by /api/poi). */
export type InfraSource = {
  id: string;
  /** "arcgis" → ArcGIS FeatureServer query; "overpass" → OSM Overpass POST. */
  kind: "arcgis" | "overpass";
  /** ArcGIS query URL (without bbox params) or Overpass query template. */
  endpoint: string;
  /** ArcGIS outFields. Ignored for Overpass. */
  outFields?: string;
  /** Overpass QL filters keyed by OSM tag → values. Ignored for ArcGIS. */
  overpassFilters?: Array<{
    key: string;
    values: string[];
    elementTypes?: Array<"node" | "way" | "relation">;
  }>;
  /** Server-side TTL in seconds. */
  revalidate: number;
  geometry: InfraGeometryKind;
};

/** Client-side visualization + Legend config. */
export type InfraLayer = {
  id: string;
  label: string;
  group: InfraGroup;
  geometry: InfraGeometryKind;
  /** Hex color used for fill / line. Picked for contrast on both dark and satellite. */
  color: string;
  /** Default toggle state. Heavy layers (transmission, water) default off. */
  defaultOn: boolean;
  /** Short tag shown on hover popups + Legend. */
  short: string;
  /** Emoji rendered as the on-map icon and in the legend chip. Pick glyphs
   *  whose meaning is immediately obvious to an IC under stress — fire truck
   *  for fire stations, hospital cross for hospitals, etc. */
  icon: string;
};

// --------------------------------------------------------------------------- //
// Source definitions — endpoints proven to work in agent/.../tools/infra.py
// and overpass.py. Same ArcGIS envelope/f=geojson recipe.
// --------------------------------------------------------------------------- //

export const INFRA_SOURCES: Record<string, InfraSource> = {
  hospitals: {
    id: "hospitals",
    kind: "arcgis",
    endpoint:
      "https://services1.arcgis.com/0MSEUqKaxRlEPj5g/arcgis/rest/services/Hospitals2/FeatureServer/0/query",
    outFields: "NAME,BEDS,TYPE,STATUS,TRAUMA",
    revalidate: 3600,
    geometry: "point",
  },
  schools: {
    id: "schools",
    kind: "arcgis",
    endpoint:
      "https://services.arcgis.com/XG15cJAlne2vxtgt/ArcGIS/rest/services/Public_Schools/FeatureServer/3/query",
    outFields: "NAME,ENROLLMENT,LEVEL_,STATUS",
    revalidate: 3600,
    geometry: "point",
  },
  fireStations: {
    id: "fireStations",
    kind: "arcgis",
    endpoint:
      "https://carto.nationalmap.gov/arcgis/rest/services/structures/MapServer/51/query",
    outFields: "NAME,ADDRESS,CITY",
    revalidate: 3600,
    geometry: "point",
  },
  eocs: {
    id: "eocs",
    kind: "arcgis",
    endpoint:
      "https://gis.fema.gov/arcgis/rest/services/FEMA/STATE_EOC/FeatureServer/0/query",
    outFields: "NAME,CITY,STATE",
    revalidate: 86400,
    geometry: "point",
  },
  commTowers: {
    id: "commTowers",
    kind: "arcgis",
    endpoint:
      "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services/Cellular_Towers_in_the_United_States/FeatureServer/0/query",
    outFields: "StrucType,Licensee,LocCity",
    revalidate: 86400,
    geometry: "point",
  },
  transmission: {
    id: "transmission",
    kind: "arcgis",
    endpoint:
      "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/ArcGIS/rest/services/Electric_Power_Transmission_Lines/FeatureServer/0/query",
    outFields: "VOLTAGE,OWNER,TYPE,VOLT_CLASS",
    revalidate: 86400,
    geometry: "line",
  },
  water: {
    id: "water",
    kind: "overpass",
    endpoint: "https://overpass-api.de/api/interpreter",
    overpassFilters: [
      { key: "natural", values: ["water"], elementTypes: ["node", "way"] },
    ],
    revalidate: 86400,
    geometry: "point",
  },
  airports: {
    id: "airports",
    kind: "overpass",
    endpoint: "https://overpass-api.de/api/interpreter",
    overpassFilters: [
      {
        key: "aeroway",
        values: ["aerodrome", "heliport", "helipad"],
        elementTypes: ["node", "way"],
      },
    ],
    revalidate: 86400,
    geometry: "point",
  },
};

// --------------------------------------------------------------------------- //
// Visualization config. Colors are picked to read on both Carto dark and
// Esri satellite — high saturation, dark stroke for contrast.
// --------------------------------------------------------------------------- //

export const INFRA_LAYERS: InfraLayer[] = [
  // Life Safety — auto-on
  {
    id: "hospitals",
    label: "Hospitals",
    group: "lifeSafety",
    geometry: "point",
    color: "#ef4444", // red-500
    defaultOn: true,
    short: "H",
    icon: "🏥",
  },
  {
    id: "schools",
    label: "Schools",
    group: "lifeSafety",
    geometry: "point",
    color: "#a78bfa", // violet-400
    defaultOn: false,
    short: "S",
    icon: "🏫",
  },
  {
    id: "eocs",
    label: "Emergency Ops Centers",
    group: "lifeSafety",
    geometry: "point",
    color: "#f472b6", // pink-400
    defaultOn: false,
    short: "E",
    icon: "🛡️",
  },
  // Response Assets — auto-on for fire stations
  {
    id: "fireStations",
    label: "Fire stations",
    group: "response",
    geometry: "point",
    color: "#fb923c", // orange-400
    defaultOn: true,
    short: "F",
    icon: "🚒",
  },
  {
    id: "airports",
    label: "Airports / heliports",
    group: "response",
    geometry: "point",
    color: "#facc15", // yellow-400
    defaultOn: false,
    short: "A",
    icon: "✈️",
  },
  // Infrastructure — heavy, lazy on
  {
    id: "transmission",
    label: "Transmission lines",
    group: "infrastructure",
    geometry: "line",
    color: "#fde047", // yellow-300
    defaultOn: false,
    short: "T",
    icon: "⚡",
  },
  {
    id: "commTowers",
    label: "Cell towers",
    group: "infrastructure",
    geometry: "point",
    color: "#94a3b8", // slate-400
    defaultOn: false,
    short: "C",
    icon: "📡",
  },
  {
    id: "water",
    label: "Water bodies (OSM)",
    group: "infrastructure",
    geometry: "point",
    color: "#38bdf8", // sky-400
    defaultOn: false,
    short: "W",
    icon: "💧",
  },
];

export const INFRA_GROUPS: Array<{ id: InfraGroup; label: string }> = [
  { id: "lifeSafety", label: "Life Safety" },
  { id: "response", label: "Response Assets" },
  { id: "infrastructure", label: "Infrastructure" },
];

export function infraLayer(id: string): InfraLayer | undefined {
  return INFRA_LAYERS.find((l) => l.id === id);
}
