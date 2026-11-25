"""Which kind of data a dataset holds, and which analyses suit it. Plain functions, no Streamlit.

The families and analyses follow docs/ANALISIS.md. A dataset is recognised by its source; a
preprocessed, sentiment or merged dataset by the source it came from; anything else by its columns.
"""
from dataclasses import dataclass, field

import pandas as pd

from workspace_app import analysis as an
from workspace_app import news
from workspace_app import social as so
from workspace_app import sources

FAMILIES = {
    "A": ("Ulasan berbintang", "Teks opini dengan bintang 1–5 sebagai label gratis."),
    "B": ("Teks media sosial", "Teks tanpa label, banyak slang dan emoji, ada suka dan balasan."),
    "C": ("Berita", "Artikel panjang dari beberapa portal, bertanggal."),
    "D": ("Deret waktu", "Nilai angka yang berurutan menurut tanggal."),
    "E": ("Panel negara-tahun", "Indikator per negara per tahun; titik tahunannya sedikit."),
    "F": ("Kejadian geospasial", "Titik kejadian dengan lintang dan bujur."),
    "G": ("Bibliometrik", "Penulis, publikasi, dan sitasi."),
}

SOURCE_FAMILIES = {
    "playstore": ("A",), "appstore": ("A",), "gmaps": ("A",),
    "instagram": ("B",), "x": ("B",), "threads": ("B",), "youtube": ("B",),
    "news": ("C",),
    "saham": ("D",), "pangan": ("D",), "openmeteo": ("D",),
    "worldbank": ("E",), "imf": ("E",), "oecd": ("E",),
    "usgs": ("F", "D"), "firms": ("F", "D"), "bencana": ("F", "D"), "gmaps_places": ("F",),
    "sinta": ("G",), "google_scholar": ("G",),
}
LABEL_SOURCES = {name: key for key, name in sources.LABELS.items()}
RATING_COLUMNS = ("rating", "bintang", "score", "skor_ulasan")
LAT_COLUMNS = ("lintang", "latitude", "lat")
LON_COLUMNS = ("bujur", "longitude", "lon", "lng")
SENTIMENT_SOURCE = "sentimen"
EMOTION_SOURCE = "emosi"
TOPIC_SOURCE = "bertopic"
ENTITY_SOURCE = "ner"


@dataclass
class Profile:
    """What a dataset holds, as far as choosing analyses is concerned."""
    rows: int
    text_columns: list = field(default_factory=list)
    rating_column: str | None = None
    date_columns: list = field(default_factory=list)
    numeric_columns: list = field(default_factory=list)
    has_coordinates: bool = False
    has_panel: bool = False
    has_sentiment: bool = False
    has_version: bool = False
    has_replies: bool = False
    has_author: bool = False
    has_likes: bool = False
    has_groups: bool = False
    names: frozenset = frozenset()  # lower-case column names


def origin_sources(job):
    """The scraping sources a job's rows came from (a job is a dict from JobManager.get or list_jobs)."""
    source, params = job["source"], job.get("params") or {}
    if source in sources.LABELS:
        return [source]
    if source == "preprocessing":
        origin = (params.get("masukan") or {}).get("sumber")
        return [origin] if origin else []
    if source in (SENTIMENT_SOURCE, EMOTION_SOURCE, TOPIC_SOURCE, ENTITY_SOURCE):
        return list(params.get("sumber_asal") or [])
    return []


def merged_sources(frame):
    """Sources named in a merged dataset's `sumber_data` column."""
    if "sumber_data" not in frame:
        return []
    return [LABEL_SOURCES[name] for name in frame["sumber_data"].dropna().unique() if name in LABEL_SOURCES]


def _first(frame, names):
    lowered = {str(column).lower(): column for column in frame}
    return next((lowered[name] for name in names if name in lowered), None)


def numeric_columns(frame, threshold=0.9):
    """Columns whose filled values are (mostly) numbers, including numbers stored as text."""
    found = []
    for column in frame:
        values = frame[column].dropna()
        values = values[values.astype(str).str.strip() != ""]
        if len(values) and pd.to_numeric(values, errors="coerce").notna().mean() >= threshold:
            found.append(column)
    return found


def profile(frame):
    frame = frame.drop(columns=["pekerjaan_asal"], errors="ignore")
    rating = _first(frame, RATING_COLUMNS)
    if rating is not None:
        values = pd.to_numeric(frame[rating], errors="coerce").dropna()
        if values.empty or not values.between(1, 5).all():
            rating = None
    texts = an.text_columns(frame)
    numbers = numeric_columns(frame)
    lowered = {str(column).lower() for column in frame}
    return Profile(
        rows=len(frame),
        text_columns=texts,
        rating_column=rating,
        date_columns=an.date_columns(frame),
        numeric_columns=numbers,
        has_coordinates=bool(_first(frame, LAT_COLUMNS) and _first(frame, LON_COLUMNS)),
        has_panel={"negara", "tahun", "nilai"} <= lowered,
        has_sentiment="sentimen" in lowered,
        has_version="versi_aplikasi" in lowered and frame["versi_aplikasi"].astype(str).str.strip().ne("").any(),
        has_replies=so.reply_columns(frame)[0] is not None,
        has_author=so.author_column(frame) is not None,
        has_likes=so.like_column(frame) is not None,
        has_groups=news.group_column(frame) is not None,
        names=frozenset(lowered),
    )


def by_columns(info):
    """Families guessed from the columns alone."""
    found = []
    if info.text_columns:
        found.append("A" if info.rating_column else "B")
    if info.has_panel:
        found.append("E")
    if info.has_coordinates:
        found.append("F")
    if info.date_columns and set(info.numeric_columns) - {info.rating_column} and "E" not in found:
        found.append("D")
    return found


def detect(job, frame, info=None):
    """(families, how they were found) for a job and its rows."""
    info = info or profile(frame)
    origins = origin_sources(job) or merged_sources(frame)
    families = list(dict.fromkeys(family for source in origins for family in SOURCE_FAMILIES.get(source, ())))
    if families:
        names = ", ".join(sources.label(source) for source in origins)
        return families, f"dari sumber data ({names})"
    return by_columns(info), "dari pola kolom"


# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #

@dataclass
class Recommendation:
    key: str
    title: str
    why: str
    page: str | None  # analysis page key, or None while the analysis is not built yet
    stage: int  # build stage (1 = built in Tahap 1); docs/ANALISIS.md lists the order
    blocked: str = ""  # why it cannot run on this dataset, empty when it can

    @property
    def ready(self):
        return self.page is not None and not self.blocked


def _need_text(info, minimum=1):
    if not info.text_columns:
        return "Tidak ada kolom teks."
    if info.rows < minimum:
        return f"Butuh minimal {minimum} baris, dataset ini {info.rows}."
    return ""


def _need(condition, reason):
    return "" if condition else reason


CATALOGUE = {
    "A": [
        ("sentimen", "Sentimen model dan sentimen dari bintang",
         "Bintang adalah label gratis: model RoBERTa dibandingkan dengan classifier yang dilatih dari bintang "
         "dataset ini sendiri.", "sentimen", 1, lambda i: _need_text(i)),
        ("ketidakcocokan", "Ketidakcocokan bintang dan isi teks",
         "Ulasan bintang 5 bernada negatif (atau sebaliknya) sering menunjukkan masalah yang tersembunyi.",
         "sentimen", 1, lambda i: _need(i.rating_column, "Tidak ada kolom rating 1–5.")),
        ("aspek", "Sentimen per aspek (kamus aspek)",
         "Memisahkan keluhan soal login, iklan, harga, pelayanan, dan seterusnya.", "sentimen", 1,
         lambda i: _need_text(i)),
        ("tren-sentimen", "Tren sentimen per bulan dan per versi",
         "Melihat apakah rilis versi tertentu menurunkan kepuasan.", "sentimen", 1,
         lambda i: _need(i.date_columns or i.has_version, "Tidak ada kolom tanggal atau versi.")),
        ("topik", "Topik ulasan", "Kelompok keluhan dan pujian yang sering muncul.", "topik", 0,
         lambda i: _need_text(i, 50)),
        ("frekuensi", "Frekuensi kata", "Kata yang paling sering muncul.", "frekuensi", 0, lambda i: _need_text(i)),
    ],
    "B": [
        ("sentimen", "Sentimen komentar", "Teks tanpa label: sentimen dari model RoBERTa.", "sentimen", 1,
         lambda i: _need_text(i)),
        ("jaringan-balasan", "Jaringan balasan dan akun sentral",
         "Siapa membalas siapa: PageRank, betweenness, dan komunitas Louvain dari kolom induk atau akun yang "
         "dibalas.", "balasan", 2,
         lambda i: _need(i.has_replies and i.has_author, "Tidak ada kolom akun dan kolom balasan "
                                                         "(parent_id, id_induk, atau IN_REPLY_TO).")),
        ("interaksi", "Teks teratas, akun aktif, sentimen × suka",
         "Komentar paling disukai, seberapa timpang keaktifan akun, dan apakah nada tertentu lebih disukai.",
         "interaksi", 2, lambda i: _need(i.has_author or i.has_likes, "Tidak ada kolom akun maupun jumlah suka.")),
        ("teks-berulang", "Indikasi teks berulang",
         "Komentar yang sangat mirip (kosinus TF-IDF): templat, salin-tempel, atau kampanye. Bukan bukti bot.",
         "berulang", 2, lambda i: _need_text(i, 50)),
        ("emosi", "Emosi dan toksisitas",
         "Lima emosi (RoBERTa, EmoT) serta deteksi ujaran kebencian dan hinaan (IndoBERTweet, IndoToxic2024).",
         "emosi", 2, lambda i: _need_text(i)),
        ("topik", "Topik percakapan", "Kelompok tema yang dibicarakan.", "topik", 0, lambda i: _need_text(i, 50)),
        ("tren", "Volume dari waktu ke waktu", "Kapan percakapan memuncak.", "tren", 0,
         lambda i: _need(i.date_columns, "Tidak ada kolom tanggal.")),
    ],
    "C": [
        ("log-odds", "Kata pembeda antarportal",
         "Weighted log-odds (Monroe dkk., 2008): kata yang khas bagi satu portal dibanding portal lain.", "pembeda", 3,
         lambda i: _need_text(i) or _need(i.has_groups, "Tidak ada kolom portal atau sumber dengan minimal dua nilai.")),
        ("sentimen", "Sentimen per portal",
         "Nada pemberitaan tiap portal: sebaran sentimen, skor bersih, dan uji chi-kuadrat.", "sentimen", 1,
         lambda i: _need_text(i)),
        ("bertopic", "Topik dari waktu ke waktu (BERTopic)",
         "Topik dari makna kalimat (embedding multibahasa, UMAP, HDBSCAN) dengan koherensi NPMI; NMF sebagai "
         "tingkat ringan.", "topik-waktu", 3, lambda i: _need_text(i, 50)),
        ("entitas", "Entitas bernama",
         "Orang, organisasi, partai, dan tempat yang paling sering diberitakan, per portal, dari waktu ke waktu, "
         "dan yang sering disebut bersama (NusaBert-ner, NERGrit).", "entitas", 3, lambda i: _need_text(i)),
        ("topik", "Topik berita (NMF)", "Tingkat ringan untuk topik.", "topik", 0, lambda i: _need_text(i, 50)),
        ("tren", "Volume berita", "Kapan isu paling banyak diberitakan.", "tren", 0,
         lambda i: _need(i.date_columns, "Tidak ada kolom tanggal.")),
    ],
    "D": [
        ("forecast", "Forecast dengan uji mundur",
         "AutoETS, AutoARIMA, dan MSTL dibanding baseline naif pada data terakhir; MASE, MAPE, dan cakupan selang.",
         "deret-waktu", 4, lambda i: _need(i.date_columns, "Tidak ada kolom tanggal.")),
        ("dekomposisi", "Dekomposisi musiman dan uji stasioner",
         "STL dengan kekuatan tren dan musiman, uji ADF dan KPSS.", "deret-waktu", 4,
         lambda i: _need(i.date_columns, "Tidak ada kolom tanggal.")),
        ("ramadan", "Efek Ramadan dan Lebaran",
         "Simpangan harga di sekitar Idul Fitri dibanding hari lain, per tahun.", "deret-waktu", 4,
         lambda i: None if "harga" not in i.names else _need(i.date_columns, "Tidak ada kolom tanggal.")),
        ("saham", "Return, indikator, dan volatilitas GARCH",
         "Sebaran return, MA, RSI, MACD, dan GARCH(1,1); forecast selalu dibanding random walk.", "deret-waktu", 4,
         lambda i: None if not ({"close", "adj_close"} & i.names) else _need(i.date_columns,
                                                                                 "Tidak ada kolom tanggal.")),
        ("cuaca", "Anomali cuaca",
         "Selisih suhu atau hujan terhadap rata-rata hari dan bulan yang sama di data ini.", "deret-waktu", 4,
         lambda i: None if not any(name.startswith(("suhu", "hujan")) for name in i.names)
         else _need(i.date_columns, "Tidak ada kolom tanggal.")),
        ("statistik", "Statistik kolom angka", "Ringkasan, sebaran, dan kelengkapan.", "statistik", 0, lambda i: ""),
    ],
    "E": [
        ("tren-cagr", "Tren, CAGR, dan perbandingan antarnegara",
         "Laju pertumbuhan majemuk tahunan (CAGR) dan perbandingan lintas negara; tanpa forecast karena titik tahunan sedikit.",
         "panel-negara", 6, lambda i: ""),
        ("korelasi-indikator", "Korelasi antarindikator",
         "Korelasi Pearson atau Spearman dengan uji signifikansi antarindikator.",
         "panel-negara", 6, lambda i: ""),
        ("klaster-negara", "Klaster negara (k-means)",
         "Pengelompokan negara berdasarkan indikator yang terstandardisasi (z-score).",
         "panel-negara", 6, lambda i: ""),
        ("regresi-panel", "Regresi panel efek tetap",
         "Estimasi PanelOLS dengan efek tetap negara/tahun (linearmodels).",
         "panel-negara", 6, lambda i: ""),
        ("statistik", "Statistik indikator", "Ringkasan dan kelengkapan data.", "statistik", 0, lambda i: ""),
    ],
    "F": [
        ("peta", "Peta kepadatan heksagon", "Di mana kejadian menumpuk; bisa dibobot dengan magnitudo atau FRP.",
         "geospasial", 5, lambda i: _need(i.has_coordinates, "Tidak ada kolom lintang dan bujur.")),
        ("klaster", "Klaster titik (HDBSCAN)",
         "Kelompok kejadian yang rapat menurut jarak lingkaran besar, dengan pusat dan radius tiap klaster.",
         "geospasial", 5, lambda i: _need(i.has_coordinates and i.rows >= 30,
                                          "Butuh kolom lintang dan bujur dan minimal 30 titik.")),
        ("wilayah", "Hitungan per kabupaten/kota dan provinsi",
         "Titik ditempatkan pada batas kabupaten/kota (offline); khusus Indonesia.", "geospasial", 5,
         lambda i: _need(i.has_coordinates, "Tidak ada kolom lintang dan bujur.")),
        ("gempa", "Gempa: Gutenberg–Richter dan kedalaman",
         "Nilai b dengan Mc otomatis (Aki, Utsu, Shi–Bolt) dan sebaran kedalaman.", "geospasial", 5,
         lambda i: None if not ({"magnitudo", "magnitude", "mag"} & i.names)
         else _need(i.rows >= 50, f"Butuh minimal 50 gempa, dataset ini {i.rows}.")),
        ("tren", "Jumlah kejadian dari waktu ke waktu", "Musim dan lonjakan kejadian.", "tren", 0,
         lambda i: _need(i.date_columns, "Tidak ada kolom tanggal.")),
        ("statistik", "Statistik kejadian", "Sebaran magnitudo, kedalaman, kecerahan.", "statistik", 0, lambda i: ""),
    ],
    "G": [
        ("coauthor", "Jaringan co-author",
         "Graf kolaborasi penulis (NetworkX): komunitas Louvain dan betweenness centrality dari kolom authors.",
         "bibliometrik", 7, lambda i: _need("authors" in i.names or "penulis" in i.names,
                                            "Tidak ada kolom penulis (authors).")),
        ("sitasi-tahunan", "Sitasi per tahun",
         "Jumlah publikasi, total sitasi per tahun, dan h-index korpus.",
         "bibliometrik", 7, lambda i: _need("year" in i.names or "tahun" in i.names,
                                            "Tidak ada kolom tahun.")),
        ("topik-judul", "Topik judul publikasi (NMF)",
         "Tema riset dari judul publikasi menggunakan TF-IDF + NMF.",
         "bibliometrik", 7, lambda i: _need_text(i, 10)),
        ("profil-sinta", "Sebaran h-index dan sitasi profil SINTA",
         "Sebaran per program studi/afiliasi dan perbandingan h-index Scopus vs Google Scholar.",
         "bibliometrik", 7, lambda i: _need("scopus_h_index" in i.names or "gscholar_h_index" in i.names
                                            or "h_index_scopus" in i.names or "h_index_gs" in i.names,
                                            "Tidak ada kolom h-index profil SINTA.")),
    ],
}


def recommendations(families, info):
    """Analyses for the families, most suitable first: runnable, then blocked, then not built yet."""
    found, seen = [], set()
    for family in families:
        for key, title, why, page, stage, check in CATALOGUE.get(family, []):
            if key in seen:  # the same analysis suits several families: list it once, under the first
                continue
            seen.add(key)
            reason = check(info)
            if reason is None:  # does not apply to this dataset at all: not worth listing
                continue
            found.append((family, Recommendation(key, title, why, page, stage, reason)))
    order = {True: 0, False: 1}
    return sorted(found, key=lambda item: (order[item[1].ready], item[1].page is None, item[1].stage == 0))
