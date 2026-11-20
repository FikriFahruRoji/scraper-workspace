"""Scholar collection controls and saved dataset views."""
import pandas as pd
import streamlit as st

SORTS = {"profile": {"citations": "Sitasi terbanyak", "year": "Tahun terbaru", "title": "Judul"},
         "search": {"relevance": "Relevansi", "date": "Tanggal terbaru"}}


def form(mode):
    prefix = f"scholar-{mode}-"
    profile = mode == "profile"
    query = st.text_input("URL atau ID profil publik" if profile else "Kata kunci pencarian", key=prefix+"query")
    sort = st.selectbox("Urutan", SORTS[mode], format_func=SORTS[mode].get, key=prefix+"sort")
    count = st.number_input("Maksimum artikel" if profile else "Maksimum hasil (0 untuk semua halaman)",
                            min_value=1 if profile else 0, max_value=2000 if profile else 1000,
                            value=100 if profile else 20, key=prefix+"count")
    params = dict(mode=mode, query=query, count=count, sort=sort)
    with st.expander("Pengaturan pengambilan"):
        engines = {"http": "HTTP", "browser": "Browser"}
        params["engine"] = st.selectbox("Metode", list(engines), index=0 if profile else 1,
                                        format_func=engines.get, key=prefix+"engine")
        params["hl"] = st.selectbox("Bahasa halaman", ["en", "id"], key=prefix+"hl")
        params["timeout"] = st.number_input("Batas waktu per halaman (detik)", 5, 180,
                                            value=30 if profile else 60, key=prefix+"timeout")
    if not profile:
        for field, label in (("year_from", "Tahun awal"), ("year_to", "Tahun akhir")):
            params[field] = st.number_input(label, 1500, 2200, value=None, step=1,
                                            placeholder="Opsional", key=prefix+field)
    return params


def data_tabs(job):
    profile = job["params"]["mode"] == "profile"
    name = "Profil" if profile else "Pencarian"
    prefix = f"scholar-{job['id']}-"
    table, charts, detail = st.tabs(["Artikel" if profile else "Hasil", "Grafik", name])
    frame = pd.DataFrame(job["rows"])
    with table:
        if frame.empty:
            st.info("Tidak ada artikel tersimpan.")
        else:
            query = st.text_input("Cari judul, penulis, atau venue", key=prefix+"find").strip()
            if query:
                frame = frame[frame[["Judul", "Penulis", "Venue"]].apply(
                    lambda col: col.str.contains(query, case=False, regex=False, na=False)).any(axis=1)]
            left, middle, right = st.columns(3)
            start = left.number_input("Dari tahun", value=None, step=1, key=prefix+"start")
            end = middle.number_input("Sampai tahun", value=None, step=1, key=prefix+"end")
            minimum = right.number_input("Sitasi minimal", 0, value=0, key=prefix+"min")
            if start is not None:
                frame = frame[frame["Tahun"] >= start]
            if end is not None:
                frame = frame[frame["Tahun"] <= end]
            if minimum:
                frame = frame[frame["Sitasi"] >= minimum]
            if not profile and st.checkbox("Hanya dengan tautan PDF", key=prefix+"pdf"):
                frame = frame[frame["Tautan PDF"].fillna("").ne("")]
            st.caption(f"{len(frame)} dari {len(job['rows'])} artikel. Unduhan mencakup seluruh dataset.")
            st.dataframe(frame, hide_index=True, width="stretch")
    with charts:
        if not frame.empty:
            st.markdown("**Artikel per tahun terbit**")
            st.bar_chart(frame["Tahun"].dropna().astype(int).value_counts().sort_index().rename("Artikel"))
            if not profile:
                st.markdown("**Venue terbanyak**")
                st.bar_chart(frame["Venue"].replace("", None).dropna().value_counts().head(10).rename("Artikel"))
                st.markdown("**10 artikel dengan sitasi terbanyak dalam hasil terpilih**")
                ranked = frame.dropna(subset=["Sitasi"]).sort_values("Sitasi", ascending=False).head(10)
                if not ranked.empty:
                    st.bar_chart(ranked, x="Judul", y="Sitasi", horizontal=True, sort="-Sitasi")
        history = job["metadata"].get("Sitasi per Tahun", [])
        if profile and history:
            st.markdown("**Sitasi yang diterima profil per tahun**")
            st.bar_chart(pd.DataFrame(history), x="Tahun", y="Sitasi")
    with detail:
        attrs = pd.DataFrame(job["metadata"].get(name, []))
        if not attrs.empty:
            attrs["Nilai"] = attrs["Nilai"].astype("string")
            st.dataframe(attrs, hide_index=True, width="stretch")
        if profile and history:
            st.dataframe(history, hide_index=True)
