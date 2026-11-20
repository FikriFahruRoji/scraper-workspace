"""Configurable Indonesian NLP preprocessing, as plain functions.

Steps always run in this order; each one can be switched off:

    tidy → lowercase → URL/email/mention → hashtag → emoji → elongation →
    slang → numbers → punctuation → tokens → stopwords → stemming → min length

Slang runs before numbers and punctuation are removed so that keys such as
"b4", "7an", "w/" and "@" in the dictionary can still match.
"""
from collections import Counter
import csv
from dataclasses import dataclass
from functools import lru_cache
import html
import io
import json
from pathlib import Path
import re
import unicodedata

RESOURCES = Path(__file__).with_name("resources")
NEGATIONS = frozenset({"tidak", "bukan", "jangan", "belum", "kurang", "tak", "enggak"})
PLACEHOLDERS = ("<url>", "<email>", "<user>")

LIGHT = {
    "lowercase": True, "urls": "token", "hashtags": "strip", "emoji": "words", "numbers": "keep",
    "elongation": True, "slang": True, "punctuation": "keep", "stopwords": False, "stemming": False,
    "min_length": 0, "drop_empty": True, "drop_duplicates": True,
}
FULL = {
    "lowercase": True, "urls": "remove", "hashtags": "strip", "emoji": "remove", "numbers": "remove",
    "elongation": True, "slang": True, "punctuation": "remove", "stopwords": True, "stemming": True,
    "min_length": 2, "drop_empty": True, "drop_duplicates": True,
}
PRESETS = {"Ringan (model transformer)": LIGHT, "Lengkap (analisis kata)": FULL}
DEFAULT_LISTS = {"stopword_sources": ["project"], "keep_negation": True}

# Default text columns per scraping source; only string values are joined.
TEXT_COLUMNS = {
    "google_scholar": ["Judul", "Ringkasan"],
    "news": ["title", "content"], "gmaps": ["ulasan"], "instagram": ["caption", "komentar"],
    "playstore": ["ulasan"], "threads": ["text"], "x": ["text"], "youtube": ["komentar"],
}

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MENTION_RE = re.compile(r"(?<![\w@])@\w+")
HASHTAG_RE = re.compile(r"#(\w+)")
TAG_RE = re.compile(r"<[^<>]+>")
ELONGATION_RE = re.compile(r"([^\W\d_])\1{2,}")
NUMBER_RE = re.compile(r"(?<![\w<])\d+(?:[.,]\d+)*(?![\w>])")
PUNCT_RE = re.compile(r"(?P<keep><(?:url|email|user)>|(?<=\w)-(?=\w))|[^\w\s]|_")
EDGE_RE = re.compile(r"(\W*)(.*?)(\W*)", re.DOTALL)


# --------------------------------------------------------------------------- #
# Dictionaries
# --------------------------------------------------------------------------- #

def parse_slang(data, name="kamus"):
    """Read a JSON object or a two-column CSV into ordered (slang, formal) pairs."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    stripped = text.lstrip()
    if stripped.startswith("{"):
        pairs = json.loads(text, object_pairs_hook=list)
    else:
        rows = [row for row in csv.reader(io.StringIO(text)) if len(row) >= 2]
        if rows and [cell.strip().lower() for cell in rows[0][:2]] == ["slang", "formal"]:
            rows = rows[1:]
        pairs = [(row[0], row[1]) for row in rows]
    pairs = [(str(key).strip().lower(), str(value).strip()) for key, value in pairs if str(key).strip()]
    if not pairs:
        raise ValueError(f"{name}: tidak ada pasangan kata gaul yang terbaca.")
    return pairs


def parse_words(data):
    """One word per line (TXT) or the first column of a CSV."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    words = []
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].strip():
            words.append(row[0].strip().lower())
    return words


def parse_manual_slang(text):
    """Lines like `gpp=tidak apa-apa`."""
    pairs = []
    for line in (text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip():
                pairs.append((key.strip().lower(), value.strip()))
    return pairs


def check_slang(pairs):
    """Problems worth showing the user; nothing is fixed automatically."""
    counts = Counter(key for key, _ in pairs)
    mapping = dict(pairs)
    return {
        "entri": len(mapping),
        "kunci_ganda": sorted(key for key, count in counts.items() if count > 1),
        "ke_diri_sendiri": sorted(key for key, value in mapping.items() if key == value.lower()),
        "saling_tukar": sorted({tuple(sorted((key, value.lower()))) for key, value in mapping.items()
                                if key != value.lower() and mapping.get(value.lower(), "").lower() == key}),
        "frasa": sorted(key for key in mapping if " " in key),
    }


def check_words(words):
    counts = Counter(words)
    return {
        "entri": len(counts),
        "ganda": sorted(word for word, count in counts.items() if count > 1),
        "negasi": sorted(NEGATIONS & set(counts)),
    }


@lru_cache
def project_slang():
    return tuple(parse_slang((RESOURCES / "slang.txt").read_bytes(), "slang.txt"))


@lru_cache
def project_stopwords():
    return tuple(parse_words((RESOURCES / "stopwords.txt").read_bytes()))


@lru_cache
def sastrawi_stopwords():
    from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory
    return tuple(StopWordRemoverFactory().get_stop_words())


@lru_cache
def english_stopwords():
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    return tuple(sorted(ENGLISH_STOP_WORDS))


@lru_cache
def _stemmer():
    from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
    stemmer = StemmerFactory().create_stemmer()
    # Sastrawi 1.0.1 keeps its ~30k root words in a list and scans it on every
    # lookup (~40 ms per word). A set gives the same answers in constant time.
    dictionary = getattr(getattr(stemmer, "delegatedStemmer", stemmer), "dictionary", None)
    if dictionary is not None and isinstance(dictionary.words, list):
        dictionary.words = set(dictionary.words)
    return stemmer


@lru_cache(maxsize=200_000)
def stem(word):
    return _stemmer().stem(word) or word


# --------------------------------------------------------------------------- #
# Resources for one run
# --------------------------------------------------------------------------- #

@dataclass
class Resources:
    slang: dict
    phrase_re: re.Pattern | None
    stopwords: frozenset
    keep: frozenset


def build_resources(config, *, slang_upload=(), stopword_upload=()):
    """Merge dictionaries: bundled < uploaded < typed in the form."""
    slang = dict(project_slang())
    slang.update(dict(slang_upload))
    slang.update(dict(parse_manual_slang(config.get("extra_slang", ""))))
    phrases = sorted((key for key in slang if " " in key), key=len, reverse=True)
    phrase_re = (re.compile(r"(?<!\w)(" + "|".join(map(re.escape, phrases)) + r")(?!\w)", re.IGNORECASE)
                 if phrases else None)

    stop = set()
    sources = config.get("stopword_sources", ())
    if "project" in sources:
        stop.update(project_stopwords())
    if "sastrawi" in sources:
        stop.update(sastrawi_stopwords())
    if "english" in sources:
        stop.update(english_stopwords())
    if "upload" in sources:
        stop.update(stopword_upload)
    stop.update(parse_words(config.get("extra_stopwords", "")))
    keep = set(parse_words(config.get("keep_words", "")))
    if config.get("keep_negation", True):
        keep |= NEGATIONS
    return Resources(slang, phrase_re, frozenset(stop - keep), frozenset(keep))


# --------------------------------------------------------------------------- #
# One text
# --------------------------------------------------------------------------- #

def _demojize(text, stats):
    import emoji
    stats["emoji"] += emoji.emoji_count(text)
    words = emoji.demojize(text, language="id", delimiters=("\x00", "\x01"))
    return re.sub(r"\x00([^\x01]*)\x01", lambda m: " " + m.group(1).replace("_", " ") + " ", words)


def _remove_emoji(text, stats):
    import emoji
    stats["emoji"] += emoji.emoji_count(text)
    return emoji.replace_emoji(text, " ")


def _swap_token(token, slang, stats):
    if token.lower() in slang:
        stats["kata_gaul"] += 1
        return slang[token.lower()]
    lead, core, tail = EDGE_RE.fullmatch(token).groups()
    if core and core.lower() in slang:
        stats["kata_gaul"] += 1
        return lead + slang[core.lower()] + tail
    return token


def _normalize_slang(text, resources, stats):
    """Single pass: a replacement is never replaced again."""
    def tokens(segment):
        return " ".join(_swap_token(token, resources.slang, stats) for token in segment.split())

    if not resources.phrase_re:
        return tokens(text)
    parts = resources.phrase_re.split(text)
    out = []
    for index, part in enumerate(parts):
        if index % 2:  # captured phrase
            stats["kata_gaul"] += 1
            out.append(resources.slang[part.lower()])
        elif part.strip():
            out.append(tokens(part))
    return " ".join(out)


def _sub_count(pattern, replacement, text, stats, key):
    text, count = pattern.subn(replacement, text)
    stats[key] += count
    return text


def clean_text(text, config, resources, stats):
    """Return the cleaned text for one row; counters are added to `stats`."""
    # 1. Tidy: tags, entities, Unicode compatibility forms, whitespace.
    text = html.unescape(TAG_RE.sub(" ", str(text or "")))
    text = unicodedata.normalize("NFKC", text)
    if config["lowercase"]:
        text = text.lower()
    if config["urls"] != "keep":
        token = config["urls"] == "token"
        text = _sub_count(URL_RE, " <url> " if token else " ", text, stats, "url")
        text = _sub_count(EMAIL_RE, " <email> " if token else " ", text, stats, "email")
        text = _sub_count(MENTION_RE, " <user> " if token else " ", text, stats, "mention")
    if config["hashtags"] == "strip":
        text = HASHTAG_RE.sub(r"\1", text)
    elif config["hashtags"] == "remove":
        text = _sub_count(HASHTAG_RE, " ", text, stats, "hashtag")
    if config["emoji"] == "words":
        text = _demojize(text, stats)
    elif config["emoji"] == "remove":
        text = _remove_emoji(text, stats)
    if config["elongation"]:
        text = ELONGATION_RE.sub(r"\1", text)
    if config["slang"]:
        text = _normalize_slang(text, resources, stats)
        if config["lowercase"]:
            text = text.lower()
    if config["numbers"] == "remove":
        text = _sub_count(NUMBER_RE, " ", text, stats, "angka")
    if config["punctuation"] == "remove":
        text = PUNCT_RE.sub(lambda m: m.group("keep") or " ", text)

    tokens = text.split()
    if config["stopwords"]:
        before = len(tokens)
        tokens = [token for token in tokens if _core(token).lower() not in resources.stopwords]
        stats["stopword"] += before - len(tokens)
    if config["stemming"]:
        stemmed = []
        for token in tokens:
            lead, core, tail = EDGE_RE.fullmatch(token).groups()
            if core and token not in PLACEHOLDERS and core.lower() not in resources.keep:
                new = stem(core.lower())
                stats["stemming"] += new != core.lower()
                token = lead + new + tail
            stemmed.append(token)
        tokens = stemmed
    if config["min_length"]:
        before = len(tokens)
        tokens = [token for token in tokens
                  if token in PLACEHOLDERS or len(_core(token)) >= config["min_length"]]
        stats["token_pendek"] += before - len(tokens)
    return " ".join(tokens)


def _core(token):
    return EDGE_RE.fullmatch(token).group(2)


# --------------------------------------------------------------------------- #
# Many rows
# --------------------------------------------------------------------------- #

def join_columns(row, columns):
    return " ".join(str(row[column]).strip() for column in columns
                    if isinstance(row.get(column), str) and row[column].strip())


def new_stats():
    return Counter({key: 0 for key in ("url", "email", "mention", "hashtag", "emoji", "kata_gaul",
                                       "angka", "stopword", "stemming", "token_pendek")})


def run(rows, columns, config, resources, *, progress=None, stop=None, batch=2000):
    """Process rows; returns (output rows, step report).

    `progress(done, total, rows_so_far)` is called after every batch, and a set
    `stop` event ends the run early with the rows finished so far.
    """
    stats = new_stats()
    output, seen = [], set()
    empty = duplicates = 0
    total = len(rows)
    used_columns = {column for row in rows for column in row}
    original_columns = {}
    for column in ("teks_bersih", "jumlah_token"):
        if column in used_columns:
            alias = f"{column}_asli"
            suffix = 2
            while alias in used_columns:
                alias = f"{column}_asli_{suffix}"
                suffix += 1
            original_columns[column] = alias
            used_columns.add(alias)
    for start in range(0, total, batch):
        if stop is not None and stop.is_set():
            break
        for row in rows[start:start + batch]:
            cleaned = clean_text(join_columns(row, columns), config, resources, stats)
            if config["drop_empty"] and not cleaned:
                empty += 1
                continue
            if config["drop_duplicates"]:
                if cleaned in seen:
                    duplicates += 1
                    continue
                seen.add(cleaned)
            original = {original_columns.get(key, key): value for key, value in row.items()}
            output.append({**original, "teks_bersih": cleaned, "jumlah_token": len(cleaned.split())})
        if progress:
            progress(min(start + batch, total), total, output)
    report = {
        "baris_masuk": total, "baris_keluar": len(output), "baris_kosong_dibuang": empty,
        "duplikat_dibuang": duplicates, "total_token": sum(row["jumlah_token"] for row in output),
        **dict(stats),
    }
    return output, report
