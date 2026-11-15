from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from client import BASE, FIELDS, as_csv, author_id, fetch_profile, read_csv, read_works

st.set_page_config(page_title="OpenAlex Scraper", layout="wide")
st.title("OpenAlex Scraper")
mode = st.radio("Mode", ["Kata kunci", "Profil penulis"], horizontal=True)
profile = mode == "Profil penulis"
query = st.text_input("Tautan atau ID penulis OpenAlex" if profile else "Kata kunci").strip()
identifier = ""
if profile and query:
    try:
        identifier = author_id(query)
    except ValueError as error:
        st.error(str(error))

if profile:
    limit = None
    if identifier:
        st.link_button("Buka profil di OpenAlex", f"{BASE}/authors/{identifier}")
    if st.button("Ambil metrik dari halaman profil", disabled=not identifier):
        st.session_state.pop("metrik", None)
        try:
            with st.spinner("Membaca HTML profil dengan Scrapling…"):
                metrics = fetch_profile(identifier)
            metrics["waktu_pengambilan"] = datetime.now(ZoneInfo("Asia/Jakarta")).isoformat()
            st.session_state["metrik"] = (identifier, metrics)
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error("Browser gagal membuka profil. Jalankan scrapling install jika browser belum terpasang.")
    saved = st.session_state.get("metrik")
    if saved and saved[0] == identifier:
        st.dataframe(pd.DataFrame([saved[1]]), hide_index=True)
        st.download_button("Unduh CSV metrik", as_csv([saved[1]], saved[1]),
                           "openalex_metrik.csv", "text/csv")
    st.caption("Di profil OpenAlex, klik angka di bagian Works untuk membuka daftar publikasi.")
else:
    limit = int(st.number_input("Batas publikasi", min_value=1, value=100))
    if query:
        st.link_button("Buka pencarian di OpenAlex",
                       f"{BASE}/works?{urlencode({'search': query})}")

st.markdown(
    "1. Buka daftar publikasi di OpenAlex dan tunggu hasil tampil.\n"
    "2. Simpan setiap halaman yang diperlukan sebagai HTML, atau gunakan **Export results** "
    "jika tersedia dan unduh CSV.\n"
    "3. Unggah satu atau beberapa berkas di bawah. Hasil muncul otomatis."
)
files = st.file_uploader("HTML atau CSV daftar OpenAlex", type=["html", "htm", "csv"],
                         accept_multiple_files=True, key=mode)
rows, seen = [], set()
for file in files:
    try:
        parse = read_csv if file.name.lower().endswith(".csv") else read_works
        parsed = parse(file.getvalue())
        if not parsed:
            st.warning(f"{file.name} tidak berisi tautan publikasi yang dapat dibaca. "
                       "Pastikan hasil sudah tampil sebelum menyimpan, atau gunakan CSV.")
        for row in parsed:
            key = row["id_openalex"] or row["doi"] or (row["judul"], row["penulis"], row["tahun"])
            if row["judul"] and key not in seen:
                seen.add(key)
                rows.append(row)
    except (ValueError, TypeError):
        st.warning(f"{file.name} tidak dapat dibaca sebagai data OpenAlex.")

if rows:
    rows = rows[:limit]
    st.subheader(f"Publikasi dari berkas ({len(rows):,})")
    st.caption("Jumlah mengikuti berkas yang diunggah. Kolom kosong berarti data tidak ditemukan dalam berkas.")
    st.dataframe(pd.DataFrame(rows, columns=FIELDS), hide_index=True)
    st.download_button("Unduh CSV publikasi", as_csv(rows, FIELDS),
                       "openalex_publikasi.csv", "text/csv")
