# Scraper Berita Multi-Portal

Aplikasi web untuk **mengambil berita dari banyak media Indonesia** berdasarkan
**ekspresi kata kunci** dan **rentang waktu**, lengkap dengan **judul, tanggal,
dan isi berita penuh** (bukan sekadar ringkasan). Dibangun dengan **Scrapling**
(`AsyncStealthySession`) dan **Streamlit**.

## Fitur

- 🔎 **Ekspresi kata kunci**: `AND`, `OR`, tanda kutip `"..."`, dan kurung `( )`.
- 📰 **10 portal**: Kompas, Detik, CNN Indonesia, CNBC Indonesia, Republika,
  Suara.com, Tribunnews, Tempo.co, IDX Channel, JPNN.
- 📄 **Isi lengkap artikel** (semua paragraf), bukan hanya snippet.
- 📝 **Log proses real-time** selama scraping (URL ditemukan, artikel diambil,
  artikel dilewati beserta alasannya).
- 🗂️ **Portal diproses paralel** dengan maksimal empat tab dalam satu browser.
- 🕵️ **Mode stealth** memakai browser Chromium Scrapling untuk semua portal.
- 📊 Tabel hasil, ringkasan per portal, tampilan isi lengkap, dan ekspor CSV.

## Sintaks kata kunci

| Contoh | Arti |
|---|---|
| `ekonomi` | mengandung kata `ekonomi` |
| `"energi terbarukan"` | frasa persis |
| `PLTS OR PLTB` | salah satu |
| `"pajak karbon" AND PLTU` | keduanya |
| `"pajak karbon" AND (PLTU OR "batu bara")` | gabungan |
| `JETP transisi` | `JETP AND transisi` (AND implisit) |

Saat penelusuran, `OR` dipecah menjadi beberapa query; `AND` memakai operand
pertama sebagai query pencarian, lalu **semua artikel disaring ulang** dengan
ekspresi penuh pada judul + isi.

## Kebutuhan

- Python **3.10+** (disarankan 3.11/3.12).
- Koneksi internet.
- Browser Chromium untuk Scrapling (diunduh sekali).

## Instalasi

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# unduh browser stealth (sekali saja)
scrapling install --force
patchright install chromium
```

> **Catatan macOS**: gunakan Python dari Homebrew atau `uv` (bukan Python
> bertanda tangan *Developer ID/hardened runtime* seperti runtime DeepSeek
> Harness), karena wheel terkompilasi (`curl_cffi`, `numpy`) bisa gagal dimuat
> dengan error `different Team IDs`.

## Menjalankan

```bash
source .venv/bin/activate
streamlit run app.py
```

Atau klik dua kali **`Jalankan Scraper Berita.command`**.

Di panel kiri: pilih portal, isi ekspresi kata kunci, atur rentang tanggal, lalu
klik **Mulai Scraping**. **Log proses** tampil langsung di bawah tombol.

## Struktur

```
news-scraper/
├── app.py                  # antarmuka Streamlit + log proses
├── scraper/
│   ├── __init__.py
│   ├── core.py             # model Article/Portal + utilitas tanggal & teks
│   ├── fetchers.py         # Sesi dan pool tab AsyncStealthySession
│   ├── query.py            # parser ekspresi AND/OR/kutip/kurung
│   ├── portals.py          # konfigurasi portal: discover() + parse()
│   └── engine.py           # run_scrape(): orkestrasi + log
├── requirements.txt
└── README.md
```

## Cara Kerja

1. **Parse ekspresi** (`query.py`) → AST + daftar query penelusuran.
2. **Penemuan** (`discover`) — per portal per query:
   - Kompas & Detik: endpoint pencarian (kata kunci + tanggal).
   - CNN/CNBC/Republika/Suara/Tribunnews/JPNN/IDX Channel: halaman `/tag/<kata-kunci>`.
   - Tempo: RSS `rss.tempo.co`.
3. **Pengambilan isi** (`parse`) — buka artikel dengan browser stealth, ambil
   `og:title`, tanggal dari JSON-LD, dan **semua paragraf** badan berita.
4. **Penyaringan per halaman** — setiap halaman diperiksa: artikel di luar
   rentang tanggal dan yang tidak memenuhi ekspresi kata kunci dibuang.
   Pencarian dapat berlanjut setelah halaman tanpa hasil cocok, dan berhenti
   saat daftar habis, berulang, mencapai batas pengaman, atau portal menolak permintaan.
5. **Tampil/ekspor** — tabel + isi lengkap + CSV (UTF-8 BOM).

## Catatan & Batasan

- **Cakupan hasil** mengikuti daftar yang tersedia dari masing-masing portal.
  Halaman tag (CNN/CNBC/Suara) **berhenti otomatis** begitu melewati tanggal awal.
  Pengaman internal: 500 halaman (portal dengan tanggal di URL) / 150 halaman (lainnya).
- **Tempo.co** hanya menyajikan artikel berbahasa Inggris lewat feed utama;
  subdomain `bisnis.`/`nasional.` mengembalikan body kosong, jadi tidak dipakai.
- Portal dengan halaman yang tidak selesai `load` (CNN, sebagian Detik) memakai
  mode *light*: hanya dokumen HTML yang diunduh.
- Artikel dibaca dalam kelompok hingga dua per portal, dengan jeda antar
  kelompok. Respons HTTP 403, 429, atau 503 menghentikan portal terkait.
- Isi artikel dirender saat dipilih. CSV mencakup seluruh artikel terkumpul.
- Benchmark fixture lokal membandingkan satu dan empat tab pada 16 artikel
  yang sama. Jalankan dari folder induk dengan
  `.venv/bin/python tools/check_news_performance.py`.

## Etika

- Hanya ambil konten yang boleh diakses; patuhi `robots.txt` dan ToS tiap media.
- Gunakan untuk riset/penggunaan wajar; jangan untuk pelatihan model AI tanpa izin.
- Jeda antar-permintaan sudah diterapkan; jangan menaikkan beban secara agresif.

Dibangun dengan [Scrapling](https://github.com/D4Vinci/Scrapling).
