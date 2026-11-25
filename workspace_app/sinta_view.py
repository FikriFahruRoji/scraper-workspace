"""Form and result views for the SINTA source: authors of an affiliation, author profiles, and sample documents."""
import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

SOURCES = ("sinta",)
NAMES = {"sinta": "SINTA"}
MODES = {
    "affiliation": ("Penulis per afiliasi", "Semua penulis sebuah perguruan tinggi, lengkap dengan skor"),
    "profile": ("Profil penulis", "Skor, bidang, serta metrik Scopus, Google Scholar, dan WoS"),
    "documents": ("Contoh dokumen", "Hanya 10 dokumen acak per daftar, bukan daftar lengkap"),
}
TARGET_LABELS = {"affiliation": "ID atau URL afiliasi", "profile": "ID atau URL penulis",
                 "documents": "ID atau URL penulis"}
PLACEHOLDERS = {"affiliation": "417\nhttps://sinta.kemdiktisaintek.go.id/affiliations/profile/414",
                "profile": "5974504\nhttps://sinta.kemdiktisaintek.go.id/authors/profile/29555",
                "documents": "5974504"}
VIEWS = {"scopus": "Scopus", "googlescholar": "Google Scholar", "garuda": "Garuda", "researches": "Penelitian"}
MAX_TARGETS = {"affiliation": 10, "profile": 100, "documents": 30}
MAX_AUTHORS = 5000


def _lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    from workspace_app.views import _initial, _key, check as checkbox, choice, number, time_limit

    mode = choice(tr("Data yang diambil"), MODES, key=_key(source, "mode"))
    key = _key(source, f"targets-{mode}")
    targets = st.text_area(tr(TARGET_LABELS[mode]), key=key, height=110, placeholder=PLACEHOLDERS[mode],
                           help=tr('Satu per baris. Boleh ID angka atau salinan URL dari sinta.kemdiktisaintek.go.id. Maksimal {0} per pengambilan.', MAX_TARGETS[mode]))
    max_authors, views, full_profile = 0, list(VIEWS)[:2], True
    if mode == "affiliation":
        max_authors = number(source, "max_authors", tr("Maksimum penulis per afiliasi (0 untuk semua)"), 0, MAX_AUTHORS,
                             100, help=tr("SINTA menampilkan 10 penulis per halaman, dan satu halaman diambil kira-kira "
                                       "tiap 1 detik. Afiliasi besar punya ribuan penulis."))
        full_profile = checkbox(source, "full_profile", "Lengkapi dengan profil tiap penulis", True,
                             help=tr("Membuka profil setiap penulis (sekitar 1 detik per penulis) supaya tabelnya sama "
                                  "dengan mode Profil penulis: bidang serta metrik Scopus, Google Scholar, dan WoS. "
                                  "Matikan untuk daftar cepat yang hanya berisi skor dan h-index."))
        st.caption(tr("Ratusan penulis butuh beberapa menit. Naikkan Batas waktu di Pengaturan lanjutan bila perlu "
                   "(maksimum 30 menit); data yang sudah terkumpul tetap disimpan."))
    if mode == "documents":
        views = st.multiselect(tr("Daftar dokumen"), list(VIEWS), key=_key(source, "views"), format_func=lambda key: tr(VIEWS[key]),
                               **_initial(_key(source, "views"), views, "default"))
        st.warning(tr("SINTA hanya menampilkan 10 dokumen acak per daftar, tanpa halaman berikutnya, dan isinya "
                   "berganti tiap diminta. Jangan memakai hasil ini sebagai daftar publikasi lengkap. Untuk "
                   "jumlah dokumen dan sitasi, pakai mode Profil penulis."), icon=":material/warning:")
    st.caption(tr("Data publik dari sinta.kemdiktisaintek.go.id. Jeda sekitar 1 detik antar permintaan."))
    return {"mode": mode, "targets": _lines(targets), "max_authors": max_authors, "views": views,
            "full_profile": full_profile, "max_minutes": time_limit(source)}


def check(params, source="sinta"):
    """Raise ValueError with a plain message when the form cannot be run."""
    from workspace_app import sources

    mode = params["mode"]
    if not params["targets"]:
        raise ValueError(tr("Isi minimal satu ID atau URL SINTA."))
    if len(params["targets"]) > MAX_TARGETS[mode]:
        raise ValueError(tr('Maksimal {0} ID per pengambilan.', MAX_TARGETS[mode]))
    extract_id = sources.load("sinta").extract_id
    if bad := [target for target in params["targets"] if not extract_id(target)]:
        raise ValueError(tr('Bukan ID atau URL SINTA yang valid: {0}.', ', '.join(bad[:3])))
    if mode == "documents" and not params.get("views"):
        raise ValueError(tr("Pilih minimal satu daftar dokumen."))
    if not 0 <= params.get("max_authors", 0) <= MAX_AUTHORS:
        raise ValueError(tr('Maksimum penulis harus antara 0 dan {0}.', MAX_AUTHORS))


def _numbers(frame, *names):
    """The first of `names` present in `frame` as numbers; empty if none is. Datasets saved before the affiliation
    table shared the profile columns call the list h-indexes h_index_scopus and h_index_gs."""
    name = next((name for name in names if name in frame), None)
    if name is None:
        return pd.Series(float("nan"), index=frame.index)
    return pd.to_numeric(frame[name], errors="coerce")  # an all-empty column arrives as object dtype


def _top(series):
    """The largest value as text, or a dash when the column has no numbers."""
    top = series.max()
    return "–" if pd.isna(top) else f"{int(top):,}".replace(",", ".")


def _targets_text(targets):
    shown = ", ".join(targets[:3])
    return shown + (f" dan {len(targets) - 3} lainnya" if len(targets) > 3 else "")


def summary(source, params):
    mode = params["mode"]
    rows = [("Sumber", tr(NAMES[source])), ("Data", tr(MODES[mode][0])),
            ("Target", _targets_text(params["targets"]) or "belum diisi")]
    if mode == "affiliation":
        rows.append(("Penulis", f"maksimum {params['max_authors']} per afiliasi" if params["max_authors"] else tr("semua")))
        rows.append(("Profil", "dibuka tiap penulis" if params.get("full_profile", True) else tr("hanya daftar")))
    if mode == "documents":
        rows.append(("Daftar", ", ".join(VIEWS[view] for view in params["views"]) or tr("belum dipilih")))
    rows.append(("Batas waktu", tr("{0} menit", params['max_minutes'])))
    return rows


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params = job["params"]
    mode = params.get("mode", "affiliation")
    rows = [("Data", tr(MODES[mode][0])), ("Target", "; ".join(params.get("targets", [])))]
    if mode == "affiliation":
        rows += [("Maksimum penulis", params.get("max_authors") or tr("semua")),
                 ("Profil tiap penulis", "dibuka" if params.get("full_profile", True) else "tidak (hanya daftar)")]
    if mode == "documents":
        rows += [("Daftar", ", ".join(VIEWS.get(view, view) for view in params.get("views", []))),
                 ("Catatan", "Contoh acak 10 dokumen per daftar, bukan daftar lengkap")]
    return rows


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    from workspace_app.views import _key

    state, mode = st.session_state, params.get("mode", "affiliation")
    state[_key(source, "mode")] = mode
    state[_key(source, f"targets-{mode}")] = "\n".join(params.get("targets", []))
    if mode == "affiliation":
        state[_key(source, "max_authors")] = params.get("max_authors", 100)
        state[_key(source, "full_profile")] = params.get("full_profile", True)
    if mode == "documents":
        state[_key(source, "views")] = [view for view in params.get("views", []) if view in VIEWS]


def hints(job):
    """What to try after an empty or failed run."""
    return ["Periksa ID di URL SINTA: sinta.kemdiktisaintek.go.id/authors/profile/<ID> untuk penulis, "
            "atau /affiliations/profile/<ID> untuk afiliasi.",
            "Jika SINTA menolak permintaan (403), tunggu beberapa menit lalu coba lagi.",
            "Kurangi jumlah ID atau maksimum penulis bila batas waktu tercapai."]


def chart(frame, source="sinta"):
    """A short summary for each kind of result."""
    if "judul" in frame:
        counts = frame["daftar"].value_counts()
        st.bar_chart(counts)
        st.caption(tr("Contoh acak dari SINTA: jumlah ini hanya mencerminkan contoh, bukan jumlah dokumen penulis."))
        return
    if "skor_sinta" in frame:
        scores = _numbers(frame, "skor_sinta")
        hindex = _numbers(frame, "scopus_h_index", "h_index_scopus")
        cols = st.columns(3)
        cols[0].metric(tr("Penulis"), len(frame), border=True)
        cols[1].metric(tr("Skor SINTA tertinggi"), _top(scores), border=True)
        cols[2].metric(tr("H-index Scopus tertinggi"), _top(hindex), border=True)
        top = pd.Series(scores.to_numpy(), index=frame["nama"]).dropna().nlargest(15)
        if not top.empty:
            st.bar_chart(top, horizontal=True)
            st.caption(tr("15 penulis dengan skor SINTA tertinggi di hasil ini."))
        table = pd.DataFrame({"Scopus": hindex, "Google Scholar": _numbers(frame, "gscholar_h_index", "h_index_gs"),
                              "WoS": _numbers(frame, "wos_h_index")}).set_index(frame["nama"])
        if len(frame) <= 12 and table.notna().any().any():
            st.bar_chart(table, stack=False)  # side by side: summing three databases' h-indexes means nothing
            st.caption(tr("H-index per penulis menurut tiap basis data."))
        return
    st.metric(tr("Baris"), len(pd.DataFrame(frame)))
