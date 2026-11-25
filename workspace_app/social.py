"""Analyses for social media text (family B in docs/ANALISIS.md): plain functions, no Streamlit.

The scrapers name the same thing differently, so the columns are found by name:
Instagram and YouTube link a reply to its parent comment by id (`parent_id`, `id_induk`), X and
Threads name the account replied to (`IN_REPLY_TO`). Both become account → account edges.
"""
import re

import numpy as np
import pandas as pd

from workspace_app import analysis as an

AUTHOR_COLUMNS = ("username", "akun", "penulis", "author", "nama")
ID_COLUMNS = ("id_komentar", "post_id", "id")
PARENT_COLUMNS = ("parent_id", "id_induk")
REPLY_TO_COLUMNS = ("in_reply_to",)
LIKE_COLUMNS = ("suka", "favorite_count", "like_count", "likes", "jumlah_suka")
TEXT_COLUMNS = ("komentar", "text", "caption", "teks", "ulasan", "isi")
SEED = 42
DUPLICATE_THRESHOLD = 0.9
DUPLICATE_MIN_TEXTS = 50
EXACT_BETWEENNESS_NODES = 2_000  # above this, betweenness is estimated from a seeded sample of nodes
BETWEENNESS_SAMPLE = 500
URL_RE = re.compile(r"https?://\S+|www\.\S+")
MENTION_RE = re.compile(r"@\w+")
SPACE_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #

def find_column(frame, names):
    lowered = {str(column).lower(): column for column in frame}
    return next((lowered[name] for name in names if name in lowered), None)


def _filled(series):
    return series.fillna("").astype(str).str.strip()


def text_columns(frame):
    """Text columns, known post/comment columns first; timestamps such as "2026-11-20 05:53:05 +0700" are
    several words long but are not text."""
    dates = set(an.date_columns(frame))
    found = [column for column in an.text_columns(frame) if column not in dates]
    known = [column for name in TEXT_COLUMNS for column in found if str(column).lower() == name]
    return list(dict.fromkeys([*known, *found]))


def author_column(frame):
    column = find_column(frame, AUTHOR_COLUMNS)
    return column if column is not None and _filled(frame[column]).ne("").any() else None


def like_column(frame):
    column = find_column(frame, LIKE_COLUMNS)
    if column is None:
        return None
    return column if pd.to_numeric(frame[column], errors="coerce").notna().any() else None


def reply_columns(frame):
    """(kind, columns) of the reply link a dataset has, or (None, None).

    kind "induk": (id column, parent id column); kind "akun": the column naming the account replied to."""
    parent, ident = find_column(frame, PARENT_COLUMNS), find_column(frame, ID_COLUMNS)
    if parent is not None and ident is not None and _filled(frame[parent]).ne("").any():
        return "induk", (ident, parent)
    target = find_column(frame, REPLY_TO_COLUMNS)
    if target is not None and _filled(frame[target]).ne("").any():
        return "akun", (target,)
    return None, None


# --------------------------------------------------------------------------- #
# Jaringan balasan
# --------------------------------------------------------------------------- #

def reply_edges(frame, author=None):
    """Account → account reply edges, weighted by how often, and a count of what could not be linked.

    Returns (edges with columns dari, ke, balasan; info dict)."""
    author = author or author_column(frame)
    kind, columns = reply_columns(frame)
    empty = pd.DataFrame(columns=["dari", "ke", "balasan"])
    info = {"jenis": kind, "balasan": 0, "induk_hilang": 0, "ke_diri_sendiri": 0}
    if author is None or kind is None:
        return empty, info
    accounts = _filled(frame[author])
    if kind == "induk":
        ident, parent = columns
        owner = dict(zip(_filled(frame[ident]), accounts))
        owner.pop("", None)
        parents = _filled(frame[parent])
        replies = parents.ne("")
        targets = parents[replies].map(owner)
        info["induk_hilang"] = int(targets.isna().sum())
        pairs = pd.DataFrame({"dari": accounts[replies], "ke": targets}).dropna()
    else:
        targets = _filled(frame[columns[0]]).str.lstrip("@")
        replies = targets.ne("")
        pairs = pd.DataFrame({"dari": accounts[replies].str.lstrip("@"), "ke": targets[replies]})
    info["balasan"] = int(replies.sum())
    pairs = pairs[pairs["dari"].ne("") & pairs["ke"].ne("")]
    self_reply = pairs["dari"].str.lower() == pairs["ke"].str.lower()
    info["ke_diri_sendiri"] = int(self_reply.sum())
    pairs = pairs[~self_reply]
    if pairs.empty:
        return empty, info
    edges = pairs.groupby(["dari", "ke"]).size().reset_index(name="balasan")
    return edges.sort_values("balasan", ascending=False, ignore_index=True), info


def reply_network(edges):
    """Account table and whole-graph metrics for reply edges.

    Centrality: replies received and sent (weighted degree), PageRank (damping 0.85), betweenness
    (exact up to EXACT_BETWEENNESS_NODES accounts, otherwise estimated from BETWEENNESS_SAMPLE nodes,
    seed 42). Communities: Louvain on the undirected graph (seed 42)."""
    import networkx as nx

    graph = nx.DiGraph()
    for row in edges.itertuples(index=False):
        graph.add_edge(row.dari, row.ke, weight=int(row.balasan))
    if graph.number_of_nodes() == 0:
        return pd.DataFrame(), {}
    nodes = graph.number_of_nodes()
    pagerank = nx.pagerank(graph, alpha=0.85, weight="weight")
    sample = None if nodes <= EXACT_BETWEENNESS_NODES else BETWEENNESS_SAMPLE
    betweenness = nx.betweenness_centrality(graph, k=sample, seed=SEED, normalized=True)
    undirected = graph.to_undirected()
    communities = nx.community.louvain_communities(undirected, weight="weight", seed=SEED)
    community = {node: number for number, members in
                 enumerate(sorted(communities, key=len, reverse=True), 1) for node in members}
    table = pd.DataFrame({
        "Akun": list(graph.nodes),
        "Balasan diterima": [graph.in_degree(node, weight="weight") for node in graph.nodes],
        "Balasan dikirim": [graph.out_degree(node, weight="weight") for node in graph.nodes],
        "PageRank": [pagerank[node] for node in graph.nodes],
        "Betweenness": [betweenness[node] for node in graph.nodes],
        "Komunitas": [community[node] for node in graph.nodes],
    }).sort_values(["PageRank", "Balasan diterima"], ascending=False, ignore_index=True)
    components = list(nx.weakly_connected_components(graph))
    metrics = {
        "akun": nodes,
        "relasi": graph.number_of_edges(),
        "balasan": int(edges["balasan"].sum()),
        "kepadatan": nx.density(graph),
        "resiprositas": nx.reciprocity(graph) if graph.number_of_edges() else 0.0,
        "komponen": len(components),
        "komponen_terbesar": max(len(part) for part in components) / nodes,
        "komunitas": len(communities),
        "modularitas": nx.community.modularity(undirected, communities, weight="weight"),
        "betweenness_sampel": sample,
        "seed": SEED,
    }
    return table, metrics


def layout(edges, accounts, seed=SEED):
    """Spring layout positions for the given accounts (networkx, seeded)."""
    import networkx as nx

    keep = set(accounts)
    graph = nx.Graph()
    graph.add_nodes_from(accounts)
    for row in edges.itertuples(index=False):
        if row.dari in keep and row.ke in keep:
            weight = graph.get_edge_data(row.dari, row.ke, {"weight": 0})["weight"] + int(row.balasan)
            graph.add_edge(row.dari, row.ke, weight=weight)
    return nx.spring_layout(graph, weight="weight", seed=seed, k=1.5 / np.sqrt(max(len(accounts), 1)))


def network_method_text(metrics, info, origin):
    sample = (f"diestimasi dari sampel {metrics['betweenness_sampel']} simpul (seed {metrics['seed']})"
              if metrics.get("betweenness_sampel") else "dihitung eksak")
    link = ("kolom id induk komentar" if info["jenis"] == "induk" else "kolom akun yang dibalas")
    return (
        f"Jaringan balasan {origin} dibangun sebagai graf berarah berbobot dengan pustaka NetworkX: simpul adalah "
        f"akun dan busur menunjuk dari akun pembalas ke akun yang dibalas, diturunkan dari {link}; bobot busur "
        f"adalah jumlah balasan. Balasan ke diri sendiri ({info['ke_diri_sendiri']}) dan balasan yang induknya "
        f"tidak ada di dataset ({info['induk_hilang']}) tidak dimasukkan. Graf berisi {metrics['akun']} akun dan "
        f"{metrics['relasi']} relasi ({metrics['balasan']} balasan; kepadatan {decimal(metrics['kepadatan'], 4)}, "
        f"resiprositas {decimal(metrics['resiprositas'], 3)}). Sentralitas diukur dengan derajat masuk dan keluar "
        f"berbobot, PageRank (faktor redaman 0,85), dan betweenness yang {sample}. Komunitas ditemukan dengan "
        f"algoritma Louvain pada graf tak berarah (seed {metrics['seed']}); diperoleh {metrics['komunitas']} "
        f"komunitas dengan modularitas {decimal(metrics['modularitas'], 3)}."
    )


def decimal(value, places=2):
    return f"{value:.{places}f}".replace(".", ",")


# --------------------------------------------------------------------------- #
# Interaksi dan akun
# --------------------------------------------------------------------------- #

def likes(frame, column):
    return pd.to_numeric(frame[column], errors="coerce").fillna(0).clip(lower=0)


def top_texts(frame, text, like, count=20, author=None):
    """The most-liked rows: text, account, likes."""
    table = pd.DataFrame({"Suka": likes(frame, like).astype(int), "Teks": _filled(frame[text])})
    if author is not None:
        table.insert(0, "Akun", _filled(frame[author]))
    table = table[table["Teks"].ne("")]
    return table.sort_values("Suka", ascending=False, kind="stable").head(count).reset_index(drop=True)


def gini(values):
    """Gini coefficient of non-negative values (0 = evenly spread, 1 = all in one)."""
    values = np.sort(np.asarray(values, dtype=float))
    if not len(values) or values.sum() == 0:
        return 0.0
    ranks = np.arange(1, len(values) + 1)
    return float((2 * (ranks * values).sum()) / (len(values) * values.sum()) - (len(values) + 1) / len(values))


def active_accounts(frame, author, like=None):
    """Rows and likes per account, most active first, and concentration figures."""
    accounts = _filled(frame[author])
    data = pd.DataFrame({"Akun": accounts})
    if like is not None:
        data["Suka"] = likes(frame, like)
    data = data[data["Akun"].ne("")]
    grouped = data.groupby("Akun")
    table = grouped.size().rename("Teks").to_frame()
    if like is not None:
        table["Total suka"] = grouped["Suka"].sum().astype(int)
        table["Median suka"] = grouped["Suka"].median()
    table["Porsi teks (%)"] = table["Teks"] / table["Teks"].sum() * 100
    table = table.sort_values("Teks", ascending=False).reset_index()
    top_tenth = max(1, int(np.ceil(len(table) * 0.1)))
    summary = {
        "akun": len(table),
        "teks": int(table["Teks"].sum()),
        "porsi_10_persen_teratas": float(table["Teks"].head(top_tenth).sum() / table["Teks"].sum() * 100)
        if len(table) else 0.0,
        "gini": gini(table["Teks"]),
        "satu_kali": int((table["Teks"] == 1).sum()),
    }
    return table, summary


def sentiment_by_likes(frame, like, column="sentimen"):
    """Likes per sentiment label, with a Kruskal–Wallis test across the labels.

    Returns (table, test dict or None). The test needs at least two labels with five rows each."""
    from scipy.stats import kruskal

    labels = _filled(frame[column])
    data = pd.DataFrame({"Sentimen": labels, "Suka": likes(frame, like)})
    data = data[data["Sentimen"].ne("")]
    if data.empty:
        return pd.DataFrame(), None
    grouped = data.groupby("Sentimen")["Suka"]
    table = pd.DataFrame({"Teks": grouped.size(), "Median suka": grouped.median(), "Rata-rata suka": grouped.mean(),
                          "Total suka": grouped.sum().astype(int)})
    table["Porsi suka (%)"] = table["Total suka"] / max(table["Total suka"].sum(), 1) * 100
    table = table.reset_index()
    groups = [values.to_numpy() for _, values in grouped if len(values) >= 5]
    test = None
    if len(groups) >= 2 and np.ptp(np.concatenate(groups)) > 0:
        statistic, p_value = kruskal(*groups)
        n = sum(len(values) for values in groups)
        # Epsilon-squared effect size for Kruskal–Wallis (Tomczak & Tomczak, 2014).
        test = {"h": float(statistic), "p": float(p_value), "df": len(groups) - 1, "n": n,
                "epsilon2": float(statistic / ((n ** 2 - 1) / (n + 1)))}
    return table, test


def interaction_method_text(summary, test, origin, like):
    parts = [
        f"Keaktifan akun pada {origin} diukur dari jumlah teks per akun ({summary['akun']} akun, "
        f"{summary['teks']} teks). Ketimpangannya diringkas dengan koefisien Gini jumlah teks per akun "
        f"({decimal(summary['gini'], 3)}) dan porsi teks dari 10% akun teraktif "
        f"({decimal(summary['porsi_10_persen_teratas'], 1)}%)."
    ]
    if test:
        parts.append(
            f"Perbedaan jumlah suka (kolom {like}) antarkelompok sentimen diuji dengan uji Kruskal–Wallis karena "
            f"sebaran suka sangat menceng: H({test['df']}) = {decimal(test['h'], 2)}, p {p_text(test['p'])}, "
            f"n = {test['n']}, ukuran efek ε² = {decimal(test['epsilon2'], 3)}.")
    return " ".join(parts)


def p_text(value):
    """'< 0,001' or '= 0,042', to follow a p."""
    return "< 0,001" if value < 0.001 else f"= {decimal(value, 3)}"


# --------------------------------------------------------------------------- #
# Teks berulang
# --------------------------------------------------------------------------- #

def normalise(text):
    """Lowercase, links and mentions replaced, spaces collapsed: copies that differ only there still match."""
    text = URL_RE.sub(" <url> ", str(text).lower())
    text = MENTION_RE.sub(" @akun ", text)
    return SPACE_RE.sub(" ", text).strip()


def near_duplicates(frame, text, threshold=DUPLICATE_THRESHOLD, author=None, limit=an.MAX_DOCUMENTS,
                    chunk=1_000):
    """Groups of texts whose TF-IDF cosine similarity is at least `threshold`.

    Pairs are linked into groups transitively (union–find). Returns (groups table, row → group Series,
    summary dict). Only the first `limit` non-empty texts are compared."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts = _filled(frame[text]).map(normalise)
    texts = texts[texts.str.split().str.len() >= 3].head(limit)
    summary = {"teks": len(texts), "ambang": threshold, "grup": 0, "teks_dalam_grup": 0, "persen": 0.0}
    empty = pd.DataFrame(columns=["Grup", "Teks", "Akun berbeda", "Kemiripan minimum", "Contoh"])
    if len(texts) < 2:
        return empty, pd.Series(dtype="Int64"), summary
    matrix = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"(?u)\b\w+\b").fit_transform(texts)
    parent = np.arange(len(texts))

    def root(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    lowest = {}
    for start in range(0, len(texts), chunk):
        block = (matrix[start:start + chunk] @ matrix.T).tocoo()
        keep = (block.data >= threshold - 1e-9) & (block.col > block.row + start)
        for row, col, value in zip(block.row[keep] + start, block.col[keep], block.data[keep]):
            a, b = root(row), root(col)
            if a != b:
                parent[max(a, b)] = min(a, b)
            lowest[(row, col)] = value
    roots = np.array([root(node) for node in range(len(texts))])
    sizes = pd.Series(roots).value_counts()
    grouped = sizes[sizes >= 2]
    if grouped.empty:
        return empty, pd.Series(dtype="Int64"), summary
    number = {group: index for index, group in enumerate(grouped.index, 1)}
    membership = pd.Series([number.get(group) for group in roots], index=texts.index, dtype="Int64").dropna()
    minimum = {}
    for (row, col), value in lowest.items():
        group = number.get(roots[row])
        if group is not None:
            minimum[group] = min(minimum.get(group, 1.0), value)
    accounts = _filled(frame[author]) if author is not None else None
    rows = []
    for group, members in membership.groupby(membership):
        first = _filled(frame[text]).loc[members.index[0]]
        rows.append({"Grup": int(group), "Teks": len(members),
                     "Akun berbeda": accounts.loc[members.index].replace("", np.nan).nunique()
                     if accounts is not None else None,
                     "Kemiripan minimum": round(min(minimum.get(group, 1.0), 1.0), 3),
                     "Contoh": first[:200] + ("…" if len(first) > 200 else "")})
    table = pd.DataFrame(rows).sort_values("Teks", ascending=False, ignore_index=True)
    if accounts is None:
        table = table.drop(columns="Akun berbeda")
    summary.update(grup=len(table), teks_dalam_grup=int(len(membership)),
                   persen=len(membership) / len(texts) * 100)
    return table, membership, summary


def duplicate_method_text(summary, origin):
    return (
        f"Indikasi teks berulang pada {origin} dicari dengan membandingkan setiap pasangan teks memakai kemiripan "
        f"kosinus vektor TF-IDF (unigram dan bigram, tf sublinear; scikit-learn) setelah huruf dikecilkan dan "
        f"tautan serta sebutan akun diseragamkan. Teks kurang dari tiga kata tidak dibandingkan. Pasangan dengan "
        f"kemiripan ≥ {decimal(summary['ambang'], 2)} digabung menjadi grup secara transitif. Dari "
        f"{summary['teks']} teks, {summary['teks_dalam_grup']} ({decimal(summary['persen'], 1)}%) masuk ke "
        f"{summary['grup']} grup. Teks berulang dapat berasal dari templat, kutipan, salin-tempel pengguna biasa, "
        f"atau aktivitas terkoordinasi; analisis ini tidak membedakannya dan tidak menjadi bukti akun bot."
    )
