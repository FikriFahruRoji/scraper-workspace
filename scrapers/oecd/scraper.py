"""OECD data explorer datasets through the public SDMX API (sdmx.oecd.org): no key, no login.

Every OECD dataflow has its own list of dimensions (country, measure, unit, age, sex, ...). The scraper
reads that list, builds the SDMX key from a country choice and optional per-dimension filters, makes a
single data request, and returns one row per observation. Dimensions that never vary in the result are
reported once in the job metadata instead of repeating in every row. OECD limits the number of data
requests per hour, so a job makes exactly one.
"""
import csv
import io
import json
import re

from workspace_app.open_data import ApiError, get

BASE = "https://sdmx.oecd.org/public/rest"
STRUCTURE = "application/vnd.sdmx.structure+json;version=1.0;charset=utf-8"
DATA = "application/vnd.sdmx.data+csv; charset=utf-8"
COUNTRY = "REF_AREA"
TIME = "TIME_PERIOD"
CODELIST = re.compile(r"Codelist=([^:]+):([^(]+)\(([^)]+)\)")
CONCEPT = re.compile(r"Concept=([^:]+):([^(]+)\(([^)]+)\)\.(.+)$")
# Datasets confirmed to have Indonesian data, shown first and marked with a star in the form.
FEATURED = {
    "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL": "inflasi (CPI)",
    "OECD.SDD.NAD,DSD_NAAG@DF_NAAG": "PDB dan neraca nasional tahunan",
    "OECD.CTP.TPS,DSD_REV_COMP_ASAP@DF_RSASAP": "penerimaan pajak Asia-Pasifik",
    "OECD.ELS.HD,DSD_SHA@DF_SHA": "belanja kesehatan",
    "OECD.ELS.HD,DSD_HEALTH_STAT@DF_LE": "harapan hidup",
}
_FLOW = re.compile(r"^[A-Za-z0-9_.]+,[A-Za-z0-9_@.\-]+(?:,[0-9.]*)?$")
_CODE = re.compile(r"^[A-Za-z0-9_.\-]+$")
STANDARD = ("dataflow", "negara_kode", "negara", "periode", "tahun", "nilai", "status", "pengali")


def normalize_flow(value):
    """'OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL[,1.0]' -> 'agency,flow,version' (version may be empty = latest)."""
    text = re.sub(r"\s+", "", value or "")
    if not _FLOW.match(text):
        raise ValueError("Tulis dataflow sebagai AGENSI,ID, misalnya OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL.")
    agency, flow, *version = text.split(",")
    return ",".join((agency, flow, version[0] if version else ""))


def _fail(exc):
    text = str(exc)
    if "429" in text:
        return ApiError("OECD membatasi jumlah permintaan per jam. Tunggu sekitar satu jam lalu ulangi.")
    if "404" in text:
        return ApiError("OECD tidak punya data untuk kombinasi negara dan filter ini (atau dataflow tidak ada).")
    if "400" in text:
        return ApiError("OECD menolak permintaan. Periksa kode negara, nama dimensi, dan kode pada filter.")
    return exc


def _structure_json(url, stop, log, retries=2, timeout=60):
    try:
        return json.loads(get(url, accept=STRUCTURE, timeout=timeout, retries=retries, pause=3, stop=stop, log=log))["data"]
    except (ValueError, KeyError) as exc:
        raise ApiError("Jawaban struktur OECD tidak bisa dibaca.") from exc
    except ApiError as exc:
        raise _fail(exc) from exc


def catalogue(stop=None, log=None):
    """Every dataflow OECD publishes as [{'flow': 'agency,id,version', 'name': ...}], sorted by name."""
    data = _structure_json(f"{BASE}/dataflow/all", stop, log, retries=2, timeout=90)
    found = [{"flow": f"{item['agencyID']},{item['id']},{item.get('version', '')}", "name": item["name"].strip()}
             for item in data.get("dataflows", []) if item.get("id") and item.get("agencyID")]
    return sorted(found, key=lambda item: item["name"].lower())


def structure(flow, stop=None, log=None, with_availability=True):
    """Dimensions of a dataflow with their names and the codes that actually have data.

    Returns {'dims': [{'id', 'name', 'codes': [(code, label), ...]}], 'observations': int or None}. When the
    availability lookup fails (it is slow on big datasets), every code of the codelist is offered instead."""
    agency, flow_id, version = flow.split(",")
    version = version or "latest"
    data = _structure_json(f"{BASE}/dataflow/{agency}/{flow_id}/{version}?references=all", stop, log)
    try:
        listing = sorted(data["dataStructures"][0]["dataStructureComponents"]["dimensionList"]["dimensions"],
                         key=lambda item: item.get("position", 0))
    except (KeyError, IndexError) as exc:
        raise ApiError("Struktur dataflow OECD tidak bisa dibaca.") from exc
    lists = {(c["agencyID"], c["id"], c["version"]): c for c in data.get("codelists", [])}
    schemes = {(c["agencyID"], c["id"], c["version"]): c for c in data.get("conceptSchemes", [])}
    available, observations = {}, None
    if with_availability:
        try:
            constraint = _structure_json(
                f"{BASE}/availableconstraint/{agency},{flow_id},{version if version != 'latest' else ''}"
                "/all/all?mode=available&references=none", stop, log, retries=1, timeout=120)
            region = constraint["contentConstraints"][0]
            available = {item["id"]: set(item["values"]) for item in region["cubeRegions"][0]["keyValues"]
                         if item.get("values")}  # TIME_PERIOD carries a range instead of values
            observations = next((int(note["title"]) for note in region.get("annotations", [])
                                 if note.get("id") == "obs_count" and str(note.get("title", "")).isdigit()), None)
        except (ApiError, KeyError, IndexError):
            if log:
                log("  daftar kode yang tersedia tidak bisa dimuat; memakai semua kode dataset.")
    dims = []
    for item in listing:
        name = item["id"]
        match = CONCEPT.search(item.get("conceptIdentity", ""))
        if match:
            scheme = schemes.get((match.group(1), match.group(2), match.group(3)))
            concept = next((c for c in (scheme or {}).get("concepts", []) if c["id"] == match.group(4)), None)
            name = (concept or {}).get("name", name)
        codes = []
        match = CODELIST.search((item.get("localRepresentation") or {}).get("enumeration", ""))
        if match and (match.group(1), match.group(2), match.group(3)) in lists:
            wanted = available.get(item["id"])
            codes = [(code["id"], code.get("name", code["id"])) for code in
                     lists[(match.group(1), match.group(2), match.group(3))]["codes"]
                     if wanted is None or code["id"] in wanted]
        elif item["id"] in available:
            codes = [(code, code) for code in sorted(available[item["id"]])]
        dims.append({"id": item["id"], "name": name, "codes": codes})
    return {"dims": dims, "observations": observations}


def dimensions(flow, stop=None, log=None):
    """The ordered dimension ids of a dataflow."""
    return [item["id"] for item in structure(flow, stop, log, with_availability=False)["dims"]]


def build_key(dims, countries, filters):
    """SDMX key: one slot per dimension, codes joined with '+', empty slot = every value."""
    unknown = [name for name in filters if name not in dims]
    if unknown:
        raise ApiError(f"Dimensi {', '.join(unknown)} tidak ada di dataflow ini. Dimensi yang tersedia: "
                       f"{', '.join(dims)}.")
    slots = []
    for dim in dims:
        codes = filters.get(dim, [])
        if dim == COUNTRY and countries:
            codes = list(dict.fromkeys([*countries, *codes])) if COUNTRY in filters else countries
        slots.append("+".join(codes))
    return ".".join(slots)


def parse_csv(body, flow, dims):
    """Rows from a csvfilewithlabels body, plus the dimension labels that never vary.

    `dims` are the dataflow's dimension ids; each code column is followed by its label column."""
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig", "replace")))
    header = next(reader, None)
    if not header:
        return [], {}
    position = {name: index for index, name in enumerate(header)}
    label_of = {name: index + 1 for name, index in position.items() if index + 1 < len(header)}
    dims = [name for name in dims if name in position and name != COUNTRY]

    def cell(line, name, label=False):
        index = position.get(name)
        if index is None:
            return ""
        index = label_of[name] if label and name in label_of else index
        return line[index].strip() if index < len(line) else ""

    flow_id = flow.split(",")[1]
    raw, distinct = [], {name: set() for name in dims}
    for line in reader:
        if not line:
            continue
        try:
            value = float(cell(line, "OBS_VALUE"))
        except ValueError:
            continue
        period = cell(line, TIME)
        row = {"dataflow": flow_id, "negara_kode": cell(line, COUNTRY), "negara": cell(line, COUNTRY, True),
               "periode": period, "tahun": int(period[:4]) if period[:4].isdigit() else None, "nilai": value,
               "status": cell(line, "OBS_STATUS", True), "pengali": cell(line, "UNIT_MULT", True)}
        for name in dims:
            code = cell(line, name)
            distinct[name].add(code)
            row[f"{name.lower()}_kode"] = code
            row[name.lower()] = cell(line, name, True)
        raw.append(row)
    fixed = {}
    for name in dims:
        if len(distinct[name]) <= 1:
            sample = raw[0][name.lower()] if raw else ""
            fixed[name] = sample
            for row in raw:
                row.pop(name.lower(), None)
                row.pop(f"{name.lower()}_kode", None)
    for key in ("status", "pengali"):
        if raw and not any(row[key] for row in raw):
            for row in raw:
                row.pop(key)
    return raw, fixed


def collect_data(flow, countries, start_year, end_year, filters, count=0, max_minutes=10,
                 stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Fetch one dataflow ('agency,id,version') for the chosen countries and period."""
    log = log_cb or (lambda message: None)
    flow = normalize_flow(flow)
    log(f"Dataflow {flow.rstrip(',')}")
    try:
        dims = dimensions(flow, stop_event, log)
        log(f"  dimensi: {', '.join(dims)}")
        if countries and COUNTRY not in dims:
            log(f"  dataflow ini tidak punya dimensi {COUNTRY}; pilihan negara diabaikan, pakai filter dimensi.")
            countries = []
        key = build_key(dims, countries, filters)
        agency, flow_id, version = flow.split(",")
        url = (f"{BASE}/data/{agency},{flow_id},{version}/{key}?startPeriod={start_year}&endPeriod={end_year}"
               "&format=csvfilewithlabels")
        log(f"  permintaan data: {key or '(semua)'}")
        body = get(url, accept=DATA, timeout=180, retries=2, pause=5, stop=stop_event, log=log)
    except ApiError as exc:
        return {"rows": [], "error": str(_fail(exc)), "stop_reason": None, "sumber": "OECD (sdmx.oecd.org)"}
    rows, fixed = parse_csv(body, flow, dims)
    rows.sort(key=lambda row: (row["negara_kode"], *(str(v) for k, v in row.items()
                                                      if k not in STANDARD and not k.endswith("_kode")), row["periode"]))
    if count:
        del rows[count:]
    log(f"  {len(rows)} observasi")
    if checkpoint_cb:
        checkpoint_cb(rows)
    result = {"rows": rows, "error": None, "stop_reason": None, "sumber": "OECD (sdmx.oecd.org)",
              "dataflow": flow.rstrip(","), "dimensi": dims, "tetap": fixed}
    if not rows:
        result["message"] = "OECD tidak mengembalikan observasi. Perluas periode atau longgarkan filter."
    return result
