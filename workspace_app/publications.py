"""Adapt the standalone publication parsers to Workspace jobs."""


def collect(source, parser, params, documents, stop, log, checkpoint):
    rows, metrics, issues = [], [], []
    seen = set()
    mode = params["mode"]
    limit = params["limit"]
    if params["action"] == "preview" and not stop.is_set():
        log("Membuka halaman awal profil lewat Scrapling stealth.")
        if source == "openalex":
            metrics.append(parser.fetch_profile(params["target"]))
        else:
            documents = [("profil.html", parser.fetch_profile(params["target"]))]

    for name, data in documents:
        if stop.is_set():
            break
        log(f"Membaca {name}")
        try:
            is_csv = name.lower().endswith(".csv")
            if source == "scholar":
                if is_csv:
                    parsed = parser.parse_profile_csv(data)
                else:
                    parsed, indicators, more = parser.parse(data, mode, params["url"] or parser.BASE)
                    metrics.extend(indicators)
                    if more:
                        issues.append("Profil masih menampilkan Show more. Impor halaman yang sudah diperluas untuk daftar selengkapnya.")
            else:
                parsed = parser.read_csv(data) if is_csv else parser.read_works(data)
            if not parsed:
                issues.append(f"{name}: tidak ada publikasi yang terbaca.")
            for row in parsed:
                if not row["judul"]:
                    continue
                identity = (row["judul"].casefold(), (row["penulis"] or "").casefold(), row["tahun"])
                key = (row.get("id_openalex") or row.get("doi") or identity
                       if source == "openalex" else identity)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
                    if limit and len(rows) >= limit:
                        break
            if rows:
                checkpoint(rows)
            log(f"{len(rows)} publikasi terkumpul.")
            if limit and len(rows) >= limit:
                break
        except (ValueError, TypeError) as exc:
            issues.append(f"{name}: {exc}")
    if params["action"] == "preview" and source == "openalex":
        return metrics, {"result_kind": "Metrik profil", "source_url": params["url"]}, None
    if not rows and not issues and not stop.is_set():
        issues.append("Tidak ada publikasi yang terbaca.")
    return rows, {"profile_metrics": metrics, "source_url": params["url"]}, " ".join(issues) or None
