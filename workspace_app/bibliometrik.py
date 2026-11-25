"""Bibliometrik (Google Scholar & SINTA): co-author network, citations per year, title topics (NMF),
and SINTA author profile distributions (h-index, citations per prodi/affiliation, Scopus vs GS).
Plain functions, no Streamlit."""
from collections import Counter
from itertools import combinations
import re

import networkx as nx
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

AUTHOR_COLUMNS = ("authors", "penulis")
TITLE_COLUMNS = ("title", "judul")
YEAR_COLUMNS = ("year", "tahun")
CITATION_COLUMNS = ("citations", "sitasi", "cited_by")
URL_RE = re.compile(r"https?://\S+")
NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


class NotEnough(ValueError):
    """Raised when a bibliometric dataset lacks required columns or minimum rows."""


def _first(frame, candidates):
    lowered = {str(col).lower(): col for col in frame}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None


def _split_authors(raw):
    if pd.isna(raw):
        return []
    text = str(raw).strip()
    if not text or text.lower() in ("nan", "none"):
        return []
    parts = re.split(r"\s*[;,]\s*", text)
    cleaned = []
    for part in parts:
        name = part.strip(" .\t\n")
        if not name or name.lower() in ("...", "…", "et al", "et al.", "dkk", "dkk."):
            continue
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


# --------------------------------------------------------------------------- #
# 1. Co-author network (Google Scholar)
# --------------------------------------------------------------------------- #

def coauthor_network(frame, min_pubs=10, min_edge_weight=1, seed=42):
    """Build an undirected weighted co-author graph from the `authors` column."""
    if frame is None or frame.empty:
        raise NotEnough("Dataset kosong.")
    col = _first(frame, AUTHOR_COLUMNS)
    if col is None:
        raise NotEnough("Dataset ini tidak punya kolom penulis (`authors`). Coba data Google Scholar.")

    author_lists = [_split_authors(val) for val in frame[col]]
    non_empty = [lst for lst in author_lists if lst]
    if len(non_empty) < min_pubs:
        raise NotEnough(f"Butuh minimal {min_pubs} publikasi dengan kolom penulis, tersedia {len(non_empty)}.")

    pub_counts = Counter()
    edge_counts = Counter()
    for lst in non_empty:
        for author in lst:
            pub_counts[author] += 1
        for a, b in combinations(sorted(lst), 2):
            edge_counts[(a, b)] += 1

    graph = nx.Graph()
    for author, count in pub_counts.items():
        graph.add_node(author, publikasi=count)
    for (a, b), weight in edge_counts.items():
        if weight >= int(min_edge_weight):
            graph.add_edge(a, b, weight=weight)

    if graph.number_of_edges() == 0:
        raise NotEnough("Tidak ditemukan pasangan co-author yang berkolaborasi pada publikasi yang sama.")

    # Keep nodes that have at least one edge when filtering by min_edge_weight > 1, or all nodes otherwise
    betweenness = nx.betweenness_centrality(graph, weight=None)
    communities = nx.community.louvain_communities(graph, weight="weight", seed=int(seed))
    modularity = nx.community.modularity(graph, communities, weight="weight")
    comm_map = {}
    for cid, members in enumerate(sorted(communities, key=len, reverse=True), start=1):
        for m in members:
            comm_map[m] = cid

    nodes = []
    for node in graph.nodes():
        nodes.append({
            "penulis": node,
            "publikasi": pub_counts[node],
            "coauthor": graph.degree(node),
            "bobot_kolaborasi": int(graph.degree(node, weight="weight")),
            "betweenness": round(float(betweenness.get(node, 0.0)), 4),
            "komunitas": comm_map.get(node, 1),
        })
    nodes_df = pd.DataFrame(nodes).sort_values(
        ["bobot_kolaborasi", "publikasi", "penulis"], ascending=[False, False, True]
    ).reset_index(drop=True)

    edges_df = pd.DataFrame(
        [{"Source": u, "Target": v, "Weight": int(d["weight"])} for u, v, d in graph.edges(data=True)]
    ).sort_values("Weight", ascending=False).reset_index(drop=True)

    positions = nx.spring_layout(graph, seed=int(seed), weight="weight")
    stats_dict = {
        "publikasi": len(non_empty),
        "penulis": graph.number_of_nodes(),
        "kolaborasi": graph.number_of_edges(),
        "komponen": nx.number_connected_components(graph),
        "komunitas": len(communities),
        "modularitas": float(modularity),
    }
    return {
        "graph": graph,
        "nodes": nodes_df,
        "edges": edges_df,
        "positions": positions,
        "stats": stats_dict,
    }


def coauthor_method_text(origin, result):
    st_d = result["stats"]
    return (
        f"Jaringan co-author pada {origin} dibangun dengan NetworkX sebagai graf tak berarah berbobot dari "
        f"{st_d['publikasi']} publikasi (simpul = penulis, sisi = publikasi bersama, bobot = jumlah kolaborasi). "
        f"Graf memiliki {st_d['penulis']} penulis, {st_d['kolaborasi']} pasangan kolaborasi, "
        f"{st_d['komponen']} komponen terhubung, dan {st_d['komunitas']} komunitas Louvain "
        f"(modularitas = {st_d['modularitas']:.3f}, seed 42)."
    )


# --------------------------------------------------------------------------- #
# 2. Citations per year (Google Scholar)
# --------------------------------------------------------------------------- #

def citations_by_year(frame):
    """Aggregate publication counts and citations per year plus h-index summary."""
    if frame is None or frame.empty:
        raise NotEnough("Dataset kosong.")
    year_col = _first(frame, YEAR_COLUMNS)
    cit_col = _first(frame, CITATION_COLUMNS)
    if year_col is None:
        raise NotEnough("Dataset ini tidak punya kolom tahun (`year` atau `tahun`).")

    work = pd.DataFrame({"tahun": pd.to_numeric(frame[year_col], errors="coerce")})
    if cit_col is not None:
        work["sitasi"] = pd.to_numeric(frame[cit_col], errors="coerce").fillna(0).astype(int)
    else:
        work["sitasi"] = 0

    work = work.dropna(subset=["tahun"]).copy()
    if work.empty:
        raise NotEnough("Tidak ada baris dengan tahun publikasi yang valid.")
    work["tahun"] = work["tahun"].astype(int)

    yearly = (
        work.groupby("tahun", as_index=False)
        .agg(
            publikasi=("sitasi", "count"),
            total_sitasi=("sitasi", "sum"),
            rata_rata_sitasi=("sitasi", "mean"),
            median_sitasi=("sitasi", "median"),
            sitasi_maks=("sitasi", "max"),
        )
        .sort_values("tahun")
        .reset_index(drop=True)
    )

    sorted_cits = sorted(work["sitasi"].tolist(), reverse=True)
    h_idx = sum(1 for i, c in enumerate(sorted_cits, start=1) if c >= i)
    i10_idx = sum(1 for c in sorted_cits if c >= 10)
    summary = {
        "total_publikasi": int(len(work)),
        "total_sitasi": int(work["sitasi"].sum()),
        "h_index": int(h_idx),
        "i10_index": int(i10_idx),
        "tahun_awal": int(yearly["tahun"].min()),
        "tahun_akhir": int(yearly["tahun"].max()),
        "punya_sitasi": cit_col is not None,
    }
    return {"yearly": yearly, "summary": summary}


def citations_method_text(origin, result):
    s = result["summary"]
    return (
        f"Sebaran publikasi dan sitasi tahunan pada {origin} diagregasi dengan pandas pada rentang tahun "
        f"{s['tahun_awal']}–{s['tahun_akhir']} ({s['total_publikasi']} publikasi, total {s['total_sitasi']} sitasi, "
        f"h-index korpus = {s['h_index']}, i10-index = {s['i10_index']})."
    )


# --------------------------------------------------------------------------- #
# 3. Title topics (NMF)
# --------------------------------------------------------------------------- #

def title_topics(frame, n_topics=5, n_words=8, seed=42):
    """Extract publication title topics using TF-IDF + NMF."""
    if frame is None or frame.empty:
        raise NotEnough("Dataset kosong.")
    title_col = _first(frame, TITLE_COLUMNS)
    if title_col is None:
        raise NotEnough("Dataset ini tidak punya kolom judul (`title` atau `judul`).")

    titles = frame[title_col].dropna().astype(str).str.strip()
    valid_idx = titles[titles.ne("") & ~titles.isin(["nan", "None"])].index
    if len(valid_idx) < 10:
        raise NotEnough(f"Butuh minimal 10 judul publikasi untuk pemodelan topik, tersedia {len(valid_idx)}.")

    cleaned = (
        titles.loc[valid_idx]
        .str.lower()
        .str.replace(URL_RE, " ", regex=True)
        .str.replace(NON_WORD_RE, " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    k = max(2, min(int(n_topics), len(cleaned) // 2))
    stopwords = list(ENGLISH_STOP_WORDS | {"dan", "di", "ke", "dari", "yang", "untuk", "pada", "dengan", "dalam", "atau"})
    vec = TfidfVectorizer(max_features=2000, stop_words=stopwords, min_df=2, ngram_range=(1, 2))
    try:
        dtm = vec.fit_transform(cleaned)
    except ValueError as exc:
        raise NotEnough("Kosakata judul terlalu sedikit atau seragam untuk diekstrak topiknya.") from exc

    if dtm.shape[1] < k:
        k = max(2, dtm.shape[1])
    if dtm.shape[1] < 2:
        raise NotEnough("Kosakata judul terlalu sedikit untuk pemodelan topik NMF.")

    model = NMF(n_components=k, random_state=int(seed), init="nndsvda", max_iter=400)
    doc_topic = model.fit_transform(dtm)
    terms = vec.get_feature_names_out()
    dominant = doc_topic.argmax(axis=1)

    topic_rows = []
    for idx in range(k):
        top_indices = model.components_[idx].argsort()[::-1][: int(n_words)]
        keywords = ", ".join(terms[j] for j in top_indices)
        count = int((dominant == idx).sum())
        topic_rows.append({
            "topik": f"Topik {idx + 1}",
            "kata_kunci": keywords,
            "jumlah_publikasi": count,
            "porsi_persen": round(count / len(cleaned) * 100.0, 2),
        })
    topics_df = pd.DataFrame(topic_rows).sort_values("jumlah_publikasi", ascending=False).reset_index(drop=True)

    year_col = _first(frame, YEAR_COLUMNS)
    by_year = None
    if year_col is not None:
        yrs = pd.to_numeric(frame.loc[valid_idx, year_col], errors="coerce")
        trend_df = pd.DataFrame({"tahun": yrs, "topik": [f"Topik {i + 1}" for i in dominant]}).dropna()
        if not trend_df.empty:
            trend_df["tahun"] = trend_df["tahun"].astype(int)
            by_year = (
                trend_df.groupby(["tahun", "topik"], as_index=False)
                .size()
                .rename(columns={"size": "jumlah"})
                .sort_values(["tahun", "topik"])
                .reset_index(drop=True)
            )

    return {
        "topics": topics_df,
        "by_year": by_year,
        "n_docs": len(cleaned),
        "n_topics": k,
        "seed": int(seed),
    }


def topics_method_text(origin, result):
    return (
        f"Topik judul pada {origin} ({result['n_docs']} publikasi) diekstrak dengan TF-IDF (unigram dan bigram, "
        f"min_df = 2) dan Non-negative Matrix Factorization (NMF, {result['n_topics']} topik, init nndsvda, "
        f"seed {result['seed']}) menggunakan scikit-learn."
    )


# --------------------------------------------------------------------------- #
# 4. SINTA author profile analysis
# --------------------------------------------------------------------------- #

def _first_present(frame, candidates):
    lowered = {str(col).lower(): col for col in frame}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None


def sinta_profile_analysis(frame):
    """Analyse SINTA author profiles: h-index & citations by prodi/affiliation and Scopus vs GS."""
    if frame is None or frame.empty:
        raise NotEnough("Dataset kosong.")

    work = frame.copy()
    # SINTA document sample rows have a `daftar` column (Scopus/Google Scholar/Garuda); skip them per docs/ANALISIS.md
    if "daftar" in work.columns:
        work = work[work["daftar"].isna() | (work["daftar"].astype(str).str.strip() == "")].copy()
        if work.empty:
            raise NotEnough("Dataset ini berisi daftar dokumen sampel SINTA. Analisis tingkat dokumen SINTA tidak "
                            "ditawarkan; gunakan mode profil penulis SINTA.")

    scopus_h_col = _first_present(work, ("scopus_h_index", "h_index_scopus"))
    gs_h_col = _first_present(work, ("gscholar_h_index", "h_index_gs"))
    scopus_c_col = _first_present(work, ("scopus_citations",))
    gs_c_col = _first_present(work, ("gscholar_citations",))

    if scopus_h_col is None and gs_h_col is None:
        raise NotEnough("Dataset ini tidak punya kolom profil SINTA (h-index Scopus/Google Scholar).")

    profiles = pd.DataFrame({
        "nama": work[_first_present(work, ("nama", "name"))].astype(str) if _first_present(work, ("nama", "name")) else "",
        "afiliasi": work[_first_present(work, ("afiliasi", "affiliation"))].fillna("").astype(str) if _first_present(work, ("afiliasi", "affiliation")) else "",
        "program_studi": work[_first_present(work, ("program_studi", "prodi"))].fillna("").astype(str) if _first_present(work, ("program_studi", "prodi")) else "",
        "scopus_h_index": pd.to_numeric(work[scopus_h_col], errors="coerce") if scopus_h_col else np.nan,
        "gscholar_h_index": pd.to_numeric(work[gs_h_col], errors="coerce") if gs_h_col else np.nan,
        "scopus_citations": pd.to_numeric(work[scopus_c_col], errors="coerce") if scopus_c_col else np.nan,
        "gscholar_citations": pd.to_numeric(work[gs_c_col], errors="coerce") if gs_c_col else np.nan,
    })

    def _group_stats(group_col):
        sub = profiles[profiles[group_col].str.strip().ne("")].copy()
        if sub.empty:
            return pd.DataFrame()
        return (
            sub.groupby(group_col, as_index=False)
            .agg(
                penulis=("nama", "count"),
                median_h_scopus=("scopus_h_index", "median"),
                rata_h_scopus=("scopus_h_index", "mean"),
                median_h_gs=("gscholar_h_index", "median"),
                rata_h_gs=("gscholar_h_index", "mean"),
                total_sitasi_scopus=("scopus_citations", "sum"),
                total_sitasi_gs=("gscholar_citations", "sum"),
            )
            .sort_values(["penulis", "median_h_scopus"], ascending=[False, False], na_position="last")
            .reset_index(drop=True)
        )

    by_prodi = _group_stats("program_studi")
    by_afiliasi = _group_stats("afiliasi")

    paired_h = profiles[["scopus_h_index", "gscholar_h_index"]].dropna()
    corr_h = None
    if len(paired_h) >= 3 and paired_h["scopus_h_index"].nunique() > 1 and paired_h["gscholar_h_index"].nunique() > 1:
        r, p = stats.spearmanr(paired_h["scopus_h_index"], paired_h["gscholar_h_index"])
        corr_h = {"spearman_r": float(r), "p_value": float(p), "n": int(len(paired_h))}

    return {
        "profiles": profiles.reset_index(drop=True),
        "by_prodi": by_prodi,
        "by_afiliasi": by_afiliasi,
        "corr_h": corr_h,
        "n_profiles": int(len(profiles)),
    }


def sinta_method_text(origin, result):
    corr = result["corr_h"]
    corr_txt = (
        f" Korelasi peringkat Spearman antara h-index Scopus dan Google Scholar adalah "
        f"ρ = {corr['spearman_r']:.3f} (p = {corr['p_value']:.4f}, n = {corr['n']})."
        if corr
        else ""
    )
    return (
        f"Sebaran h-index dan sitasi profil penulis SINTA pada {origin} ({result['n_profiles']} profil) "
        f"diringkas per program studi dan afiliasi menggunakan pandas (median, rata-rata, dan jumlah total).{corr_txt}"
    )
