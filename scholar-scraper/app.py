from datetime import datetime
from hashlib import sha256
import logging

import pandas as pd
import streamlit as st

from scholar import (BASE, FIELDS, csv_bytes, fetch_profile, parse, parse_profile_csv,
                     profile_url, search_url, unique)

st.set_page_config(page_title="Google Scholar Scraper", layout="wide")
st.title("Google Scholar Scraper")
mode = st.radio("Mode", ["Kata kunci", "Profil penulis"], horizontal=True)
profile = mode == "Profil penulis"
value = st.text_input("Tautan profil publik" if profile else "Kata kunci")
limit = None if profile else st.number_input("Batas publikasi", min_value=1, value=100)
url = ""
if value.strip():
    try:
        url = profile_url(value) if profile else search_url(value.strip())
    except ValueError as error:
        st.error(str(error))

if url:
    st.link_button("Buka di Google Scholar", url)

if profile:
    st.markdown(
        "**Profil sendiri, daftar publikasi saja**\n\n"
        "1. Login ke akun pemilik profil di Google Scholar.\n"
        "2. Centang kotak di sebelah **Title**, lalu pilih **Export → CSV**.\n"
        "3. Unggah CSV di bawah. Hasil langsung tampil.\n\n"
        "**Profil publik atau perlu jumlah sitasi dan metrik**\n\n"
        "1. Buka profil di Google Scholar. Tekan **Show more** berulang sampai tombol tidak aktif.\n"
        "2. Simpan halaman sebagai HTML dan unggah di bawah. Jika hasil masih parsial, "
        "gunakan **Salin tabel langsung dari browser**."
    )
else:
    st.caption("Simpan halaman hasil Scholar sebagai HTML. Unggah halaman sesuai urutan pencarian.")

uploads = st.file_uploader("Unggah CSV atau HTML profil" if profile else "HTML hasil pencarian",
                           type=["csv", "html", "htm"] if profile else ["html", "htm"],
                           accept_multiple_files=not profile, key=mode)
files = [uploads] if profile and uploads else uploads or []
pages = [(file.name, file.getvalue()) for file in files]
pasted = ""
if profile:
    with st.expander("Salin tabel langsung dari browser"):
        st.markdown(
            "1. Kembali ke tab Google Scholar dan pastikan semua publikasi sudah tampil.\n"
            "2. Di Chrome atau Edge, buka **Developer Tools → Console** "
            "(Mac **⌘⌥J**, Windows **Ctrl+Shift+J**).\n"
            "3. Salin perintah di bawah, tempel ke Console, lalu tekan **Enter**. "
            "Perintah menyalin tabel publikasi dan metrik ke clipboard.\n"
            "4. Kembali ke aplikasi dan tempel hasilnya pada kolom di bawah. "
            "Klik di luar kolom untuk memproses."
        )
        st.code(
            "copy('<html><body>' + "
            "['gsc_a_t', 'gsc_rsb_st', 'gsc_bpf_more'].map(id => "
            "document.getElementById(id)?.outerHTML ?? '').join('') + '</body></html>')",
            language="javascript",
        )
        pasted = st.text_area("Tempel HTML dari clipboard", height=100)
        if pasted.strip():
            pages = [("profil.html", pasted.encode("utf-8"))]
            st.caption("Tabel yang ditempel dipakai sebagai sumber hasil.")

source_key = tuple((name, sha256(data).digest()) for name, data in pages)
result_key = (mode, url, limit, source_key)
fetch_clicked = False
if profile and not pages:
    with st.expander("Pratinjau halaman awal"):
        st.caption("Hanya membaca publikasi yang langsung tampil di profil.")
        fetch_clicked = st.button("Ambil halaman awal profil", disabled=not url)
previous = st.session_state.get("result")
import_ready = pages and (url or (profile and not value.strip()))
if fetch_clicked or (import_ready and (not previous or previous[0] != result_key)):
    with st.spinner("Membaca publikasi…"):
        rows, metrics, issues = [], [], []
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            documents = [("profil.html", fetch_profile(url))] if fetch_clicked else pages
            for name, data in documents:
                try:
                    if name.lower().endswith(".csv"):
                        rows.extend(parse_profile_csv(data))
                        continue
                    items, indicators, more = parse(data, mode, url or BASE)
                    rows.extend(items)
                    metrics.extend(indicators)
                    if more:
                        issues.append("Daftar pada sumber ini masih bisa diperluas. Buka profil, klik Show more sampai habis, lalu gunakan Salin tabel langsung dari browser di atas.")
                except ValueError as error:
                    issues.append(str(error))
        except Exception:
            logging.getLogger(__name__).exception("Gagal mengambil profil Scholar")
            issues.append("Pengambilan profil gagal. Buka profil di browser dan unggah HTML.")
        st.session_state["result"] = (result_key, list(unique(rows))[:limit],
                                      metrics, issues, checked_at, fetch_clicked)

result = st.session_state.get("result")
if result and result[0] == result_key:
    _, rows, metrics, issues, checked_at, fetched = result
    if issues:
        st.warning(("Hasil parsial" if rows else "Tidak ada data yang dapat diekspor")
                   + ". " + " ".join(issues))
    st.caption(f"{len(rows)} publikasi · Diproses {checked_at}")
    if pages and pages[0][0].lower().endswith(".csv"):
        st.caption("Kolom mengikuti isi ekspor CSV. Unggah HTML profil untuk membaca metrik.")
    elif not fetched:
        st.caption("Angka sitasi mengikuti isi HTML yang diimpor.")
    if rows:
        st.dataframe(pd.DataFrame(rows, columns=FIELDS), hide_index=True)
        st.download_button("Unduh CSV publikasi", csv_bytes(rows, FIELDS),
                           "publikasi_scholar.csv", "text/csv")
    if metrics:
        metrics = [dict(item, waktu_proses=checked_at) for item in metrics]
        st.subheader("Metrik profil")
        st.dataframe(pd.DataFrame(metrics), hide_index=True)
        st.download_button("Unduh CSV metrik", csv_bytes(metrics, list(metrics[0])),
                           "metrik_scholar.csv", "text/csv")
