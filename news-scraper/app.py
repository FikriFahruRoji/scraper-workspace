"""Aplikasi scraping berita multi-portal berbasis Streamlit.

Menjalankan: streamlit run app.py
"""

from __future__ import annotations

from collections import deque
from datetime import date, timedelta
from time import monotonic

import pandas as pd
import streamlit as st

from scraper import PORTAL_LABELS, run_scrape

st.set_page_config(page_title="Scraper Berita Multi-Portal", page_icon="📰", layout="wide")

st.title("Scraper Berita")
st.caption(
    "Ambil **judul + isi lengkap** berita dari banyak media memakai **Scrapling** "
    "(browser stealth, hingga empat tab)."
)

# ---------------------------------------------------------------------------
# Pengaturan
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Pengaturan")
    portals = st.multiselect(
        "Portal", list(PORTAL_LABELS), default=list(PORTAL_LABELS),
        format_func=PORTAL_LABELS.get,
    )
    keyword = st.text_input(
        "Kata kunci",
        placeholder='ekonomi · "energi terbarukan" · PLTS OR PLTB',
        help='Dukung AND, OR, tanda kutip, dan kurung. Contoh: '
             '"pajak karbon" AND (PLTU OR "batu bara"). Kata berdampingan = AND.',
    )

    today = date.today()
    date_range = st.date_input(
        "Rentang waktu",
        value=(today - timedelta(days=30), today),
        min_value=date(2015, 1, 1),
        max_value=today,
        format="DD/MM/YYYY",
    )

    st.divider()
    headless = st.checkbox("Headless (tanpa jendela browser)", value=True)
    disable_resources = st.checkbox("Blokir gambar/font/iklan (lebih cepat)", value=True)

    run = st.button("Mulai Scraping", type="primary", width="stretch")
    st.caption("Mode stealth aktif (browser ringan). Tanpa batas hasil — rentang luas bisa lama; "
               "log menunjukkan kemajuan.")


def _parse_date_range(value) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and value[0] and value[1]:
            return value[0].isoformat(), value[1].isoformat()
        if len(value) == 1 and value[0]:
            return value[0].isoformat(), None
        return None, None
    return value.isoformat(), None


# ---------------------------------------------------------------------------
# Jalankan scraping
# ---------------------------------------------------------------------------
if run:
    if not keyword.strip():
        st.warning("Masukkan kata kunci terlebih dahulu.")
    elif not portals:
        st.warning("Pilih minimal satu portal.")
    else:
        start_date, end_date = _parse_date_range(date_range)
        st.subheader("Log proses")
        status = st.empty()
        log_box = st.empty()
        log_lines = deque(maxlen=80)
        last_update = 0.0

        def _log(message: str, count: int) -> None:
            global last_update
            log_lines.append(message)
            now = monotonic()
            if now - last_update < 0.25:
                return
            last_update = now
            status.markdown(f"⏳ **{count} artikel terkumpul** — proses berjalan…")
            log_box.code("\n".join(log_lines), language="text")

        try:
            result = run_scrape(
                portal_keys=portals,
                keyword=keyword.strip(),
                start_date=start_date,
                end_date=end_date,
                headless=headless,
                disable_resources=disable_resources,
                log_cb=_log,
            )
        except ValueError as exc:
            status.empty()
            st.error(f"Ekspresi kata kunci tidak valid: {exc}")
            result = None

        if result is not None:
            log_box.code("\n".join(log_lines), language="text")
            status.markdown(f"✅ **Selesai** — {len(result['articles'])} artikel terkumpul")
            st.session_state["result"] = result
            st.session_state["keyword"] = keyword.strip()

# ---------------------------------------------------------------------------
# Tampilkan hasil
# ---------------------------------------------------------------------------
result = st.session_state.get("result")
if result is not None:
    articles = result["articles"]
    stats = result["portal_stats"]
    st.caption(f"Ekspresi kata kunci: `{result.get('query', '')}`")

    c1, c2, c3 = st.columns(3)
    c1.metric("Artikel terkumpul", len(articles))
    c2.metric("Portal dicoba", len(stats))
    c3.metric("Portal berhasil", sum(1 for s in stats.values() if s["kept"] > 0))

    if result["errors"]:
        with st.expander(f"⚠️ {len(result['errors'])} peringatan/kesalahan", expanded=False):
            for err in result["errors"]:
                st.write("-", err)

    if not articles:
        st.info("Tidak ada artikel yang cocok. Coba kata kunci/rentang lain, atau periksa peringatan di atas.")
    else:
        ordered = sorted(articles, key=lambda article: article.date_iso, reverse=True)
        columns = ("portal", "title", "url", "date_iso", "category", "image", "snippet", "content")
        raw = pd.DataFrame([{field: getattr(a, field) for field in columns} for a in ordered])
        view = raw[["portal", "title", "date_iso", "category", "url"]].assign(
            isi=raw["content"].str.slice(0, 200)
        )

        st.subheader("Daftar Artikel")
        st.dataframe(
            view[["portal", "title", "date_iso", "category", "url", "isi"]],
            column_config={
                "portal": st.column_config.TextColumn("Portal", width="small"),
                "title": st.column_config.TextColumn("Judul", width="large"),
                "date_iso": st.column_config.TextColumn("Tanggal", width="small"),
                "category": st.column_config.TextColumn("Kanal", width="small"),
                "url": st.column_config.LinkColumn("Tautan", display_text="Buka artikel"),
                "isi": st.column_config.TextColumn("Cuplikan isi", width="large"),
            },
            hide_index=True,
            width="stretch",
        )

        csv = raw[
            ["portal", "title", "url", "date_iso", "category", "image", "snippet", "content"]
        ].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ Unduh hasil lengkap (CSV)",
            data=csv,
            file_name=f"berita_{st.session_state.get('keyword', 'hasil')}.csv",
            mime="text/csv",
        )

        with st.expander("📊 Ringkasan per portal", expanded=False):
            st.dataframe(
                pd.DataFrame([
                    {"portal": PORTAL_LABELS.get(k, k), "ditemukan": s["found"],
                     "disimpan": s["kept"], "error": s["error"] or ""}
                    for k, s in stats.items()
                ]),
                hide_index=True, width="stretch",
            )

        st.subheader("Isi Lengkap per Artikel")
        selected = st.selectbox(
            "Pilih artikel", range(len(ordered)), index=None,
            format_func=lambda i: f"{ordered[i].portal} · {ordered[i].title}",
        )
        if selected is not None:
            a = ordered[selected]
            st.link_button(a.title, a.url)
            st.caption(" · ".join(x for x in [a.portal, a.category, a.date_iso] if x))
            if a.image:
                st.image(a.image)
            st.write(a.content or a.snippet)
