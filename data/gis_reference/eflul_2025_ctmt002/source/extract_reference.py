"""Reproduce a source-preserving MV GIS excerpt from the official ANEEL BDGD.

Auxiliary reader only. Install pyogrio/shapely/pyproj under ./tools, not the
scientific environment. No electrical connection is inferred from coordinates.
"""
from __future__ import annotations

import collections
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import urllib.request
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cache", type=Path, default=Path.cwd() / ".audit_cache/ingestion_gis")
parser.add_argument("--output", type=Path, default=None)
args = parser.parse_args()
ROOT = args.cache.resolve()
ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tools"))

import networkx as nx
import numpy as np
import pyogrio
from pyogrio.raw import read
from pyproj import Transformer
from shapely import from_wkb
from shapely.geometry import mapping
from shapely.ops import transform

ITEM = "04a736e95f114c32a56206957a492cba"
NAME = "Eflul_86_2025-12-31_V11_20260823-1830.gdb.zip"
EXPECTED_SHA = "35a071a48728cd3bf5dbd9aa960026c9f6290f04c3d4abea25038eae476df168"
FEEDER = "002"
OUT = args.output.resolve() if args.output else ROOT / "eflul_2025_ctmt_002"
OUT.mkdir(parents=True, exist_ok=True)


def scalar(x):
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=scalar) + "\n", encoding="utf-8")


def csv_write(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


raw_zip = ROOT / NAME
if not raw_zip.exists():
    urllib.request.urlretrieve(f"https://www.arcgis.com/sharing/rest/content/items/{ITEM}/data", raw_zip)
actual_sha = hashlib.sha256(raw_zip.read_bytes()).hexdigest()
if actual_sha != EXPECTED_SHA:
    raise ValueError(f"The source ZIP changed: {actual_sha}")
gdb = ROOT / "raw" / NAME.removesuffix(".zip")
if not gdb.exists():
    with zipfile.ZipFile(raw_zip) as archive:
        archive.extractall(ROOT / "raw")


def read_layer(name):
    metadata, _fids, geometries, columns = read(gdb, layer=name)
    rows = [{str(k): scalar(v) for k, v in zip(metadata["fields"], values)} for values in zip(*columns)]
    shapes = [from_wkb(g) if g is not None else None for g in geometries] if geometries is not None else [None] * len(rows)
    return metadata, rows, shapes


to_wgs84 = Transformer.from_crs("EPSG:4674", "EPSG:4326", always_xy=True)
to_utm = Transformer.from_crs("EPSG:4674", "EPSG:31982", always_xy=True)
schema = []
for layer, geometry_type in pyogrio.list_layers(gdb):
    info = pyogrio.read_info(gdb, layer=str(layer))
    schema.append({"layer": str(layer), "count": info["features"], "geometry_type": geometry_type,
                   "crs": info["crs"], "fields": [str(x) for x in info["fields"]],
                   "dtypes": [str(x) for x in info["dtypes"]]})
dump(ROOT / "source_schema.json", schema)

layers = {}
for name in ["CTMT", "SSDMT", "UNSEMT", "UNREMT"]:
    metadata, rows, geometries = read_layer(name)
    selected = [(r, g) for r, g in zip(rows, geometries)
                if str(r["COD_ID"] if name == "CTMT" else r["CTMT"]) == FEEDER]
    selected.sort(key=lambda item: str(item[0]["COD_ID"]))
    layers[name] = selected
    csv_write(OUT / f"{name}.csv", [r for r, _ in selected])
    dump(OUT / f"{name}.geojson", {
        "type": "FeatureCollection",
        "name": f"ANEEL_EFLUL_2025_{name}_CTMT_002",
        "source_crs": metadata["crs"],
        "geometry_crs": "EPSG:4326 (transformed from SIRGAS 2000 EPSG:4674)",
        "features": [{"type": "Feature", "id": r["COD_ID"], "properties": r,
                      "geometry": mapping(transform(to_wgs84.transform, g)) if g is not None else None}
                     for r, g in selected],
    })

# Electrical edges retain all declared PACs, phases, IDs and normal switch states.
edges = []
for name in ["SSDMT", "UNSEMT", "UNREMT"]:
    for r, _g in layers[name]:
        edges.append({
            "source_layer": name, "source_id": r["COD_ID"], "edge_id": f"{name}:{r['COD_ID']}",
            "PAC_1": r["PAC_1"], "PAC_2": r["PAC_2"], "FAS_CON": r["FAS_CON"],
            "P_N_OPE": r.get("P_N_OPE", ""), "SIT_ATIV": r.get("SIT_ATIV", ""),
            "COMP_m": r.get("COMP", ""), "CTMT": r["CTMT"],
            "model_treatment": "pass_through_regulator_for_connectivity_only" if name == "UNREMT" else "declared_line" if name == "SSDMT" else "declared_switch",
        })
csv_write(OUT / "mv_connectivity.csv", edges)

# Locate PACs by declared PN_CON joins and device points, for display only.
# Conflicting cartography is preserved and quantified; it never modifies edges.
_m, ponnot_rows, ponnot_shapes = read_layer("PONNOT")
ponnot = {r["COD_ID"]: g for r, g in zip(ponnot_rows, ponnot_shapes)}
candidates = collections.defaultdict(list)
missing_pncon = []
for name in ["SSDMT", "UNSEMT", "UNREMT"]:
    for r, geom in layers[name]:
        for suffix in ["1", "2"]:
            pac = r[f"PAC_{suffix}"]
            if name == "SSDMT":
                pn = r[f"PN_CON_{suffix}"]
                point = ponnot.get(pn)
                provenance = f"SSDMT:{r['COD_ID']}:PN_CON_{suffix}={pn}"
                if point is None:
                    missing_pncon.append(provenance)
                    continue
            else:
                point = geom
                provenance = f"{name}:{r['COD_ID']}:declared_point_geometry"
            if point is not None:
                candidates[pac].append({"coordinate": list(point.coords)[0][:2], "provenance": provenance})

all_graph = nx.MultiGraph()
normal_graph = nx.MultiGraph()
for e in edges:
    all_graph.add_edge(e["PAC_1"], e["PAC_2"], key=e["edge_id"])
normal_graph.add_nodes_from(all_graph)
for e in edges:
    if e["source_layer"] != "UNSEMT" or e["P_N_OPE"] == "F":
        normal_graph.add_edge(e["PAC_1"], e["PAC_2"], key=e["edge_id"])
source = layers["CTMT"][0][0]["PAC_INI"]
reachable = nx.node_connected_component(normal_graph, source)

nodes = []
conflicts = []
for pac in sorted(all_graph):
    possible = sorted(candidates[pac], key=lambda x: x["provenance"])
    unique_coords = sorted(set(tuple(x["coordinate"]) for x in possible))
    # Coordinate is a representative of the SOURCE records, not a synthetic fix.
    coordinate = possible[0]["coordinate"] if possible else None
    projected = [to_utm.transform(*xy) for xy in unique_coords]
    max_disagreement = max((math.dist(a, b) for a in projected for b in projected), default=0.0)
    if len(unique_coords) > 1:
        conflicts.append({"PAC": pac, "unique_coordinate_count": len(unique_coords),
                          "max_disagreement_m": max_disagreement, "source_candidates": possible})
    x_m, y_m = to_utm.transform(*coordinate) if coordinate else (None, None)
    nodes.append({"PAC": pac, "is_source": pac == source, "energized_normal": pac in reachable,
                  "longitude_sirgas2000": coordinate[0] if coordinate else None,
                  "latitude_sirgas2000": coordinate[1] if coordinate else None,
                  "easting_m_epsg31982": x_m, "northing_m_epsg31982": y_m,
                  "coordinate_provenance": possible[0]["provenance"] if possible else None,
                  "coordinate_candidate_count": len(possible), "unique_coordinate_count": len(unique_coords),
                  "max_coordinate_disagreement_m": max_disagreement,
                  "coordinate_status": "display_representative_unverified"})
csv_write(OUT / "mv_nodes_display.csv", nodes)
dump(OUT / "mv_nodes_display.geojson", {
    "type": "FeatureCollection", "name": "PAC_display_representatives_not_operational_positions",
    "features": [{"type": "Feature", "id": n["PAC"], "properties": n,
                  "geometry": {"type": "Point", "coordinates": list(to_wgs84.transform(n["longitude_sirgas2000"], n["latitude_sirgas2000"]))}
                  if n["longitude_sirgas2000"] is not None else None} for n in nodes],
})

parallel_groups = collections.defaultdict(list)
for e in edges:
    parallel_groups[tuple(sorted([e["PAC_1"], e["PAC_2"]]))].append(e["edge_id"])
qc = {
    "feeder": FEEDER, "source_pac": source, "crs_native": "EPSG:4674", "crs_display_projection": "EPSG:31982",
    "node_count_all_declared_endpoints": len(nodes), "edge_count_all": len(edges),
    "normal_energized_pac_count": len(reachable),
    "normal_unenergized_pacs": sorted(set(all_graph) - reachable),
    "all_edges_component_sizes": sorted(map(len, nx.connected_components(all_graph)), reverse=True),
    "normal_component_sizes": sorted(map(len, nx.connected_components(normal_graph)), reverse=True),
    "normal_closed_edge_count": normal_graph.number_of_edges(),
    "layer_counts": {n: len(v) for n, v in layers.items()},
    "switch_states": dict(collections.Counter(r["P_N_OPE"] for r, _g in layers["UNSEMT"])),
    "phase_counts_by_layer": {n: dict(collections.Counter(r.get("FAS_CON", "") for r, _g in v)) for n, v in layers.items() if n != "CTMT"},
    "parallel_edge_groups": [{"PACs": list(k), "edge_ids": v} for k, v in sorted(parallel_groups.items()) if len(v) > 1],
    "missing_declared_PN_CON_references": missing_pncon,
    "missing_display_position_PACs": [n["PAC"] for n in nodes if n["longitude_sirgas2000"] is None],
    "PACs_with_multiple_source_coordinates_count": len(conflicts),
    "maximum_coordinate_disagreement_m": max((x["max_disagreement_m"] for x in conflicts), default=0.0),
    "coordinate_conflicts": conflicts,
    "limitations": [
        "Electrical connectivity exclusively follows declared PAC_1/PAC_2 identifiers; no snapping/proximity joins.",
        "The voltage regulator is represented as a pass-through edge solely for connectivity, not voltage regulation.",
        "Open switch boundary stubs remain declared but normally unenergized. No cross-feeder continuation was fabricated.",
        "Source geometry has inconsistent repeated PAC positions. Representative coordinates are display-only and not homologated for routing/travel.",
        "No customer, LV, jobs, scheduling resources or capacities have been included in this MV excerpt.",
        "Normal reachability is a topological data check, not operational feasibility, protection or power-flow validation.",
    ],
}
dump(OUT / "quality_report.json", qc)

source_metadata = {
    "dataset": "ANEEL BDGD / EFLUL", "reference_date": "2025-12-31",
    "source_item_title": NAME.removesuffix(".gdb.zip"), "source_item_id": ITEM, "source_owner": "aneel_aneel",
    "catalog_url": "https://dadosabertos.aneel.gov.br/dataset/base-de-dados-geografica-da-distribuidora-bdgd",
    "catalog_metadata_api": "https://dadosabertos.aneel.gov.br/api/3/action/package_show?id=base-de-dados-geografica-da-distribuidora-bdgd",
    "item_url": f"https://www.arcgis.com/home/item.html?id={ITEM}",
    "download_url": f"https://www.arcgis.com/sharing/rest/content/items/{ITEM}/data",
    "license": "Open Data Commons Open Database License (ODbL), as declared by ANEEL CKAN dataset metadata",
    "license_id": "odc-odbl", "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
    "arcgis_item_licenseInfo": None,
    "license_qualification": "The individual ArcGIS item has licenseInfo=null; ODbL is supplied by its official parent dataset, not invented as an item-level statement.",
    "attribution": "Contém informações da Base de Dados Geográfica da Distribuidora (BDGD), ANEEL/EFLUL, data-base 31/12/2025; recorte CTMT 002 (USA 06).",
    "zip_bytes": raw_zip.stat().st_size, "zip_sha256": actual_sha,
    "retrieved_utc_date": "2026-10-29", "feeder_selection": "CTMT COD_ID=002; smallest EFLUL feeder by SSDMT record count (314). All matching SSDMT, UNSEMT and UNREMT retained.",
    "raw_native_crs": "EPSG:4674 (SIRGAS 2000)", "geojson_crs": "EPSG:4326 (transformed with pyproj, always_xy=True)",
    "display_projected_crs": "EPSG:31982 (SIRGAS 2000 / UTM zone 22S), coordinates in metres",
    "units_verified": {
        "CTMT.TEN_NOM": "DDA TTEN code; 49 -> 13800 V (Manual BDGD Rev5 p182)",
        "UNSEMT.P_N_OPE": "TNOROPE: A=Aberta; F=Fechada (Manual BDGD Rev5 p202)",
        "SSDMT.COMP": "metres (Manual BDGD Rev5 SSDMT model)",
        "SEGCON.CNOM/CMAX": "amperes (Manual BDGD Rev5 SEGCON model; not imported into this topological excerpt)",
    },
    "manual_url": "https://www.arcgis.com/sharing/rest/content/items/5e98fead943e4b7e8eaa95f56967a6d1/data",
    "reader_versions": {"pyogrio": pyogrio.__version__, "GDAL": str(pyogrio.__gdal_version__)},
    "scientific_claim_boundary": "Public real GIS import and topological reachability demonstration only. Any planning run must label synthetic overlays and must not claim operational validation.",
}
dump(OUT / "source.json", source_metadata)
manifest = []
for path in sorted(OUT.iterdir()):
    if path.is_file() and path.name != "manifest_sha256.json":
        manifest.append({"path": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
dump(OUT / "manifest_sha256.json", manifest)
print(json.dumps({k: v for k, v in qc.items() if k not in ["coordinate_conflicts", "parallel_edge_groups"]}, ensure_ascii=True, indent=2))
