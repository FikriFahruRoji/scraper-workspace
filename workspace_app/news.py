"""Analyses for news (family C in docs/ANALISIS.md): plain functions, no Streamlit.

Distinctive words between portals (weighted log-odds with an informative Dirichlet prior), sentiment per
portal, and topics over time with the light tier (NMF). BERTopic, the model tier, lives in `bertopic_run`.
Both topic tiers report a coherence score computed here.
"""
from collections import Counter
from itertools import combinations
import math

import numpy as np
import pandas as pd

from workspace_app import analysis as an
from workspace_app import preprocess as pp
from workspace_app import sentiment as sn

GROUP_COLUMNS = ("portal", "sumber_data")
SEED = 42
MIN_WORD_COUNT = 5  # a word seen fewer times in the whole corpus is too thin for a z-score
Z_SIGNIFICANT = 1.96  # two-sided 95 %
MIN_GROUP_DOCS = 5
COHERENCE_WORDS = 10


def group_column(frame):
    """The column naming the portal (news rows) or the source (merged datasets), if it has 2+ groups."""
    lowered = {str(column).lower(): column for column in frame}
    for name in GROUP_COLUMNS:
        column = lowered.get(name)
        if column is not None and frame[column].astype(str).str.strip().replace("", np.nan).dropna().nunique() >= 2:
            return column
    return None


def default_text_columns(text_columns, origins):
    """Columns a source is usually read from (news: judul and isi), else the first text column."""
    found = [column for source in origins for column in pp.TEXT_COLUMNS.get(source, []) if column in text_columns]
    return list(dict.fromkeys(found)) or [column for column in an.default_text_columns(text_columns)
                                          if column != an.CLEAN_COLUMN][:1] or list(text_columns[:1])


def cleaned_rows(frame, columns, limit=an.MAX_DOCUMENTS):
    """Cleaned text of each row (same index as `frame`; '' where nothing is left), at most `limit` rows."""
    frame = frame.head(limit)
    rows = frame.to_dict("records")
    if columns == [an.CLEAN_COLUMN]:
        texts = [pp.join_columns(row, columns) for row in rows]
    else:
        config = an.words_config()
        resources = pp.build_resources(config)
        stats = pp.new_stats()
        texts = [pp.clean_text(pp.join_columns(row, columns), config, resources, stats) for row in rows]
    return pd.Series([text or "" for text in texts], index=frame.index)


# --------------------------------------------------------------------------- #
# Kata pembeda antarportal
# --------------------------------------------------------------------------- #

def distinctive_words(documents, groups, top=25, min_docs=MIN_GROUP_DOCS):
    """Words that set each group apart from all the others: weighted log-odds ratio with an informative
    Dirichlet prior (Monroe, Colaresi & Quinn, 2008, eq. 22), each group against the rest.

    The prior is the word counts of the whole corpus (so common words are shrunk towards zero). The score
    is the z-score of the log-odds difference; |z| ≥ 1.96 is significant at 95 %. Returns
    ({group: table of the `top` words most typical of it}, summary). Groups with fewer than `min_docs`
    documents are skipped."""
    documents, groups = list(documents), list(groups)
    sizes = Counter(group for text, group in zip(documents, groups) if text)
    kept = sorted(group for group, count in sizes.items() if count >= min_docs)
    counts = {group: Counter() for group in kept}
    for text, group in zip(documents, groups):
        if group in counts and text:
            counts[group].update(text.split())
    total = Counter()
    for counter in counts.values():
        total.update(counter)
    vocabulary = [word for word, count in total.items() if count >= MIN_WORD_COUNT]
    summary = {"kelompok": kept, "dokumen": {group: sizes[group] for group in kept}, "kata": len(vocabulary),
               "token": int(sum(total.values())), "dilewati": sorted(set(sizes) - set(kept))}
    if len(kept) < 2 or not vocabulary:
        return {}, summary
    prior = np.array([total[word] for word in vocabulary], dtype=float)
    alpha0 = prior.sum()
    result = {}
    for group in kept:
        inside = np.array([counts[group][word] for word in vocabulary], dtype=float)
        outside = prior - inside
        n_in, n_out = inside.sum(), outside.sum()
        odds_in = np.log((inside + prior) / (n_in + alpha0 - inside - prior))
        odds_out = np.log((outside + prior) / (n_out + alpha0 - outside - prior))
        z = (odds_in - odds_out) / np.sqrt(1 / (inside + prior) + 1 / (outside + prior))
        order = np.argsort(-z)[:top]
        result[group] = pd.DataFrame({
            "Kata": [vocabulary[i] for i in order], "Skor z": z[order].round(2),
            "Di kelompok ini": inside[order].astype(int),
            "Per 10 ribu kata (kelompok)": (inside[order] / n_in * 10_000).round(1),
            "Per 10 ribu kata (lainnya)": (outside[order] / n_out * 10_000).round(1),
            "Signifikan": z[order] >= Z_SIGNIFICANT,
        })
    return result, summary


def distinctive_method_text(summary, origin, group_name="portal"):
    sizes = ", ".join(f"{group} ({count})" for group, count in summary["dokumen"].items())
    skipped = (f" Kelompok dengan kurang dari {MIN_GROUP_DOCS} dokumen ({', '.join(summary['dilewati'])}) tidak "
               f"dianalisis." if summary["dilewati"] else "")
    return (
        f"Kata pembeda antar{group_name} pada {origin} dihitung dengan weighted log-odds ratio dengan prior "
        f"Dirichlet informatif (Monroe, Colaresi, & Quinn, 2008): setiap {group_name} dibandingkan dengan seluruh "
        f"{group_name} lainnya, prior berupa frekuensi kata pada seluruh korpus, dan skornya adalah z-score selisih "
        f"log-odds (|z| ≥ 1,96 signifikan pada taraf 95%). Teks dibersihkan tanpa stemming (huruf kecil, kata "
        f"gaul dibakukan, stopword dibuang) dan kata yang muncul kurang dari {MIN_WORD_COUNT} kali di seluruh "
        f"korpus diabaikan. Korpus terdiri atas {summary['token']} token dan {summary['kata']} kata berbeda dari "
        f"{len(summary['kelompok'])} {group_name}: {sizes}.{skipped} Skor tinggi menunjukkan kata yang khas bagi "
        f"satu {group_name}, bukan sekadar yang paling sering muncul; kekhasan kata mencerminkan pilihan topik dan "
        f"gaya redaksi sekaligus, sehingga tidak dengan sendirinya menunjukkan bias."
    )


# --------------------------------------------------------------------------- #
# Sentimen per portal
# --------------------------------------------------------------------------- #

def sentiment_by_group(frame, group, column="sentimen", top=15):
    """Share of each sentiment per group (the `top` largest) and a chi-square test of independence with
    Cramér's V. Returns (table, test or None)."""
    from scipy.stats import chi2_contingency

    rated = frame[frame[column].isin(sn.SENTIMENTS)] if column in frame else frame.iloc[0:0]
    rated = rated[rated[group].astype(str).str.strip().ne("")]
    if rated.empty:
        return pd.DataFrame(), None
    counts = pd.crosstab(rated[group], rated[column]).reindex(columns=list(sn.SENTIMENTS), fill_value=0)
    counts = counts.loc[counts.sum(axis=1).sort_values(ascending=False).index[:top]]
    table = (counts.div(counts.sum(axis=1), axis=0) * 100).round(1)
    table.insert(0, "Teks", counts.sum(axis=1))
    table["Skor bersih"] = (table["positif"] - table["negatif"]).round(1)
    table = table.reset_index().rename(columns={group: "Kelompok", **{s: s for s in sn.SENTIMENTS}})
    usable = counts.loc[:, counts.sum() > 0]
    test = None
    if len(usable) >= 2 and usable.shape[1] >= 2:
        chi2, p_value, dof, _ = chi2_contingency(usable.to_numpy())
        n = usable.to_numpy().sum()
        v = math.sqrt(chi2 / (n * (min(usable.shape) - 1))) if n else 0.0
        test = {"chi2": float(chi2), "p": float(p_value), "df": int(dof), "n": int(n), "cramers_v": float(v)}
    return table, test


def group_sentiment_method_text(test, origin, group_name="portal"):
    text = (f"Nada pemberitaan per {group_name} pada {origin} diringkas dari label sentimen model: proporsi "
            f"positif, netral, dan negatif per {group_name}, dan skor bersih (persen positif dikurangi persen negatif).")
    if test:
        from workspace_app.social import decimal, p_text
        text += (f" Perbedaan sebaran sentimen antar{group_name} diuji dengan uji chi-kuadrat independensi: "
                 f"χ²({test['df']}) = {decimal(test['chi2'], 2)}, p {p_text(test['p'])}, n = {test['n']}, ukuran "
                 f"efek V Cramér = {decimal(test['cramers_v'], 3)}. Ukuran efek yang kecil berarti perbedaan nadanya "
                 f"tipis meskipun signifikan, karena sampel besar membuat selisih kecil mudah signifikan.")
    return text


# --------------------------------------------------------------------------- #
# Topik: koherensi dan NMF dari waktu ke waktu
# --------------------------------------------------------------------------- #

def npmi_coherence(topic_words, documents, words=COHERENCE_WORDS):
    """Mean NPMI coherence of each topic from document co-occurrence (Bouma, 2009; Röder et al., 2015).

    `topic_words` is a list of word lists (best first); only the first `words` of each count. Returns
    (per-topic scores, mean). A pair never seen together scores -1, a pair that always appears together +1."""
    from scipy.sparse import csr_matrix

    topic_words = [list(dict.fromkeys(topic[:words])) for topic in topic_words]
    vocabulary = {word: number for number, word in enumerate(dict.fromkeys(w for t in topic_words for w in t))}
    rows, cols = [], []
    for number, text in enumerate(documents):
        for word in set(text.split()) & vocabulary.keys():
            rows.append(number)
            cols.append(vocabulary[word])
    n = len(documents)
    if not n or not vocabulary:
        return [None] * len(topic_words), None
    matrix = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, len(vocabulary)))
    document_frequency = np.asarray(matrix.sum(axis=0)).ravel()
    together = (matrix.T @ matrix).toarray()
    scores = []
    for topic in topic_words:
        pairs = []
        for a, b in combinations(topic, 2):
            i, j = vocabulary[a], vocabulary[b]
            joint = together[i, j] / n
            if joint <= 0:
                pairs.append(-1.0)
            elif joint >= 1:
                pairs.append(1.0)
            else:
                pmi = math.log(joint / ((document_frequency[i] / n) * (document_frequency[j] / n)))
                pairs.append(pmi / -math.log(joint))
        scores.append(float(np.mean(pairs)) if pairs else None)
    known = [score for score in scores if score is not None]
    return scores, (float(np.mean(known)) if known else None)


def topics_over_time(frame, columns, date_column, count=5, period="ME"):
    """NMF topics of the rows and the share of each topic per period.

    Returns (topic table, share table in percent per period, coherence dict, rows used). Raises ValueError
    (from `an.topics`) when there are too few documents."""
    cleaned = cleaned_rows(frame, columns)
    cleaned = cleaned[cleaned.ne("")]
    table, assignment = an.topics(list(cleaned), count)
    assigned = pd.Series(assignment, index=cleaned.index)
    names = {}
    for number, row in enumerate(table.itertuples(index=False)):
        if row.Topik != "Tanpa topik":
            words = [word.strip() for word in row[2].split(",")][:3]
            names[number] = f"{row.Topik}: {', '.join(words)}"
    names[count] = "Tanpa topik"
    dates = an.parse_dates(frame.loc[cleaned.index, date_column])
    data = pd.DataFrame({"periode": dates, "topik": assigned.map(names)}).dropna()
    share = pd.DataFrame()
    if not data.empty:
        share = (pd.crosstab(data["periode"].dt.to_period(period.rstrip("E")).dt.to_timestamp(), data["topik"],
                             normalize="index") * 100).round(1)
    words = [[w.strip() for w in str(text).split(",") if w.strip()] for text in table["Kata utama"]]
    keep = [number for number, row in enumerate(table["Topik"]) if row != "Tanpa topik"]
    scores, mean = npmi_coherence([words[number] for number in keep], list(cleaned))
    table = table.copy()
    table["Koherensi NPMI"] = None
    for number, score in zip(keep, scores):
        table.loc[table.index[number], "Koherensi NPMI"] = round(score, 3) if score is not None else None
    return table, share, {"rata_rata": mean, "dokumen": len(cleaned)}, len(cleaned)


def nmf_method_text(coherence, count, columns, origin, date_column=None):
    from workspace_app.social import decimal

    mean = f" Koherensi topik rata-rata (NPMI) {decimal(coherence['rata_rata'], 3)}." if coherence.get(
        "rata_rata") is not None else ""
    over = (f" Proporsi tiap topik dihitung per periode dari tanggal pada kolom {date_column}." if date_column else "")
    return (
        f"Topik pada {origin} (kolom {', '.join(columns)}) ditemukan dengan faktorisasi matriks non-negatif (NMF; "
        f"scikit-learn) pada matriks TF-IDF teks yang dibersihkan tanpa stemming (maksimum 3.000 kata, "
        f"min_df = 2, max_df = 0,7), {count} topik, inisialisasi nndsvda, random_state 0. Setiap dokumen "
        f"ditetapkan ke topik berbobot terbesar ({coherence['dokumen']} dokumen).{over}{mean} Koherensi NPMI diukur "
        f"dari kemunculan bersama 10 kata teratas tiap topik dalam dokumen yang sama (Bouma, 2009); nilainya "
        f"antara -1 dan 1, makin tinggi makin koheren."
    )
