"""Build scrapers/bencana/wilayah_idn.json: simplified kabupaten/kota outlines with their province.

Inputs (download them yourself, then pass their paths):
  1. geoBoundaries IDN ADM2, simplified GeoJSON (BPS/OCHA 2020, CC BY 3.0 IGO)
     https://www.geoboundaries.org/api/current/gbOpen/IDN/ADM2/  -> simplifiedGeometryGeoJSON
  2. OCHA HDX COD-AB Indonesia table, for each kabupaten's province
     https://data.humdata.org/dataset/cod-ab-idn  -> idn_admin_boundaries.xlsx

Both sources still list 34 provinces, so the kabupaten of the provinces formed in 2022 (UU 14, 15, 16 and
29 Tahun 2022) are moved here. Outlines are thinned with Douglas-Peucker and rounded to 4 decimals (~11 m).

    .venv/bin/python tools/build_wilayah.py adm2.geojson idn_admin_boundaries.xlsx
"""
import json
from pathlib import Path
import sys

import openpyxl

OUT = Path(__file__).resolve().parents[1] / "scrapers" / "bencana" / "wilayah_idn.json"
WATER = {"Danau", "Waduk"}
TOLERANCE = 0.003  # degrees, about 330 m
PROVINCE_NAMES = {"Dki Jakarta": "DKI Jakarta", "Daerah Istimewa Yogyakarta": "DI Yogyakarta"}
NEW_PROVINCES = {
    "Papua Selatan": ["Merauke", "Boven Digoel", "Mappi", "Asmat"],
    "Papua Tengah": ["Nabire", "Puncak Jaya", "Paniai", "Mimika", "Puncak", "Dogiyai", "Intan Jaya", "Deiyai"],
    "Papua Pegunungan": ["Jayawijaya", "Pegunungan Bintang", "Yahukimo", "Tolikara", "Mamberamo Tengah",
                         "Yalimo", "Lanny Jaya", "Nduga"],
    "Papua Barat Daya": ["Kota Sorong", "Sorong", "Sorong Selatan", "Raja Ampat", "Tambrauw", "Maybrat"],
}


def _simplify(points):
    """Douglas-Peucker on a closed ring, iteratively."""
    if len(points) < 5:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        length = (dx * dx + dy * dy) or 1e-18
        worst, index = 0.0, None
        for i in range(first + 1, last):
            px, py = points[i]
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
            distance = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if distance > worst:
                worst, index = distance, i
        if index is not None and worst > TOLERANCE ** 2:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, k in zip(points, keep) if k]


def _rings(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for polygon in polygons:
        for ring in polygon:
            thin = [(round(x, 4), round(y, 4)) for x, y in _simplify([tuple(p[:2]) for p in ring])]
            if len(thin) >= 4:
                yield thin


def _provinces(xlsx):
    sheet = openpyxl.load_workbook(xlsx, read_only=True)["idn_admin2"]
    rows = sheet.iter_rows(values_only=True)
    head = next(rows)
    table = {}
    for row in rows:
        record = dict(zip(head, row))
        table.setdefault(record["adm2_name"], set()).add(PROVINCE_NAMES.get(record["adm1_name"], record["adm1_name"]))
    return table


def main(geojson, xlsx):
    provinces = _provinces(xlsx)
    moved = {name: province for province, names in NEW_PROVINCES.items() for name in names}
    areas = []
    for feature in json.loads(Path(geojson).read_text(encoding="utf-8"))["features"]:
        name = feature["properties"]["shapeName"]
        if name.split()[0] in WATER:
            continue  # lakes and reservoirs are drawn as their own areas; points there take the nearest kabupaten
        found = provinces.get(name)
        if not found or len(found) != 1:
            raise SystemExit(f"Provinsi untuk {name!r} tidak tunggal: {found}")
        province = moved.pop(name, None) or found.pop()
        rings = list(_rings(feature["geometry"]))
        xs = [x for ring in rings for x, _ in ring]
        ys = [y for ring in rings for _, y in ring]
        label = name if name.startswith("Kota ") else f"Kabupaten {name}"
        areas.append({"k": label, "p": province, "b": [min(xs), min(ys), max(xs), max(ys)],
                      "r": [[c for point in ring for c in point] for ring in rings]})
    if moved:
        raise SystemExit(f"Kabupaten pemekaran Papua tidak ditemukan: {sorted(moved)}")
    data = {"sumber": "geoBoundaries IDN ADM2 (BPS/OCHA 2020, CC BY 3.0 IGO) dan OCHA COD-AB Indonesia; "
                      "provinsi Papua mengikuti pemekaran 2022",
            "areas": areas}
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    points = sum(len(r) // 2 for a in areas for r in a["r"])
    print(f"{len(areas)} wilayah, {len({a['p'] for a in areas})} provinsi, {points} titik, "
          f"{OUT.stat().st_size / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
