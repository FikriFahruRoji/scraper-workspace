# Scraper Workspace

Satu aplikasi Streamlit untuk mengumpulkan dan mengekspor data
dari **20 sumber**. Workspace memakai **Scrapling stealth** untuk jalur
browser dan menyimpan riwayat pekerjaan di SQLite.

| Sumber | Data yang tersedia |
| --- | --- |
| Google Scholar | Profil publik, metrik dan riwayat sitasi, atau publikasi berdasarkan kata kunci |
| Berita | Artikel dari 10 portal Indonesia |
| SINTA | Penulis per afiliasi, profil dan metrik penulis (skor SINTA, Scopus, Google Scholar, WoS), serta contoh dokumen |
| Google Maps Review | Ulasan tempat berdasarkan URL atau nama lokasi |
| Google Maps Lokasi | Daftar tempat relevan berdasarkan kata kunci: alamat, rating, koordinat, dan detail opsional |
| Play Store | Ulasan aplikasi berdasarkan package ID |
| App Store | Ulasan aplikasi iOS berdasarkan ID atau link App Store, per toko negara (hingga 500 ulasan terbaru atau paling membantu per aplikasi) |
| Instagram, Threads, dan X | Post, profil, pencarian, atau balasan sesuai sumber |
| YouTube | Komentar video dan balasan |
| Saham | Harga historis OHLCV, dividen, dan split; juga kripto dan kurs mata uang (pasar **Kurs**, misalnya `USD/IDR`) |
| World Bank | Indikator pembangunan (PDB, inflasi, penduduk, dan lainnya) per negara dan tahun |
| IMF | Data ekonomi makro World Economic Outlook per negara dan tahun, aktual dan proyeksi |
| Harga Pangan (BI) | Harga harian 21 komoditas pangan strategis (beras, cabai, bawang, daging, telur, minyak goreng, gula) per provinsi atau nasional dan jenis pasar, dari PIHPS Bank Indonesia |
| OECD | Seluruh katalog dataset OECD (sekitar 1.500) lewat API SDMX, dengan pilihan wilayah dan rincian bernama: inflasi, PDB, pajak, kesehatan, dan lainnya |
| Gempa (USGS) | Riwayat gempa Indonesia, seluruh dunia, atau kotak koordinat sendiri dari katalog USGS sejak 1900: waktu WIB dan UTC, magnitudo, kedalaman, koordinat, potensi tsunami, laporan dirasakan |
| Titik Api (NASA FIRMS) | Titik api (hotspot) satelit VIIRS atau MODIS per negara dan tahun sejak 2000: lokasi, waktu WIB dan UTC, daya radiasi api (FRP), keyakinan, siang atau malam, dan jenis |
| Bencana (GDACS & EONET) | Kejadian bencana selain gempa di Indonesia atau seluruh dunia sejak 2000, dengan provinsi dan kabupaten/kota: banjir, siklon tropis, gunung api, kekeringan, kebakaran hutan (GDACS, dengan level peringatan hijau/oranye/merah), serta tanah longsor, suhu ekstrem, dan kabut asap (NASA EONET): waktu WIB dan UTC, koordinat, keparahan, status |
| Cuaca (Open-Meteo) | Cuaca harian atau per jam (suhu, hujan, angin, kelembapan, dan lainnya) untuk lokasi mana pun, riwayat sejak 1940 atau prakiraan 16 hari |

Katalog USGS memuat gempa seluruh dunia sejak 1900. Satu permintaan dibatasi 20.000 kejadian, jadi
rentang tanggal dipecah otomatis. Untuk Indonesia, M 4,5+ berarti sekitar 1.000 gempa per tahun; turunkan
magnitudo hanya untuk rentang pendek. Kotak Indonesia (11,5°LS–6,5°LU, 94°–142°BT) mencakup sedikit wilayah
tetangga, jadi cek kolom `tempat`. Data USGS adalah domain publik.

OECD memakai API SDMX publik (sdmx.oecd.org). Formulir menampilkan seluruh katalog OECD (sekitar 1.500 dataset,
bisa dicari dengan mengetik; lima dataset yang terbukti memuat Indonesia diberi tanda bintang dan ada di atas). Setelah
dataset dipilih, formulir memuat dimensinya (wilayah, ukuran, satuan, usia, dan lainnya) dan hanya menawarkan kode yang
benar-benar punya data, lengkap dengan namanya. Dimensi yang cuma punya satu nilai ditampilkan sebagai keterangan tetap.
Daftar dataset disimpan di komputer selama seminggu; pemuatan pertama bisa sampai satu menit. Di hasil, dimensi yang
berubah menjadi kolom dan yang tetap dicatat sekali di metadata pekerjaan. OECD membatasi permintaan data per jam
(sekitar 20), jadi satu pengambilan hanya memakai satu permintaan data. Tidak semua wilayah ada di semua dataset:
Indonesia ada di CPI, neraca nasional, pajak Asia-Pasifik, belanja kesehatan, dan harapan hidup, tetapi tidak di
sebagian dataset ketenagakerjaan dan kependudukan.

Titik api NASA FIRMS diambil dari arsip standar per negara dan tahun (tanpa kunci). Arsip baru lengkap
setelah tahun kalender berakhir, jadi data terbaru adalah tahun lalu; data mendekati waktu nyata dan
area bebas memerlukan MAP_KEY pribadi dan belum didukung. VIIRS (375 m) mulai 2012, MODIS (1 km) mulai 2000.
Satu tahun VIIRS untuk Indonesia bisa ratusan ribu titik, jadi ada batas jumlah titik bawaan 200.000.

Bencana diambil per bulan dari GDACS (gdacs.org) dan NASA EONET, keduanya tanpa kunci. Wilayah Indonesia di GDACS
berarti kejadian yang mengenai Indonesia, jadi siklon yang tidak menyentuh daratan hanya muncul di Seluruh dunia; di EONET
berarti kotak koordinat Indonesia. Banyak kejadian EONET disalin dari GDACS; bila keduanya dipilih, salinan itu dibuang.
Setiap kejadian diberi kolom `provinsi` dan `kabupaten_kota` dari lintang dan bujurnya,
dihitung di komputer sendiri dengan batas wilayah BPS 2020 yang disederhanakan (geoBoundaries/OCHA, CC BY 3.0 IGO;
38 provinsi termasuk pemekaran Papua 2022; dibangun ulang dengan `tools/build_wilayah.py`). Titik di laut sampai 20 km
dari pantai diberi kabupaten terdekat dan jaraknya di `jarak_wilayah_km`; titik lebih jauh atau di luar Indonesia dibiarkan
kosong. Untuk kejadian berbentuk area, titiknya adalah titik tengah area itu.
Gempa tidak termasuk (pakai sumber Gempa USGS). Untuk data korban dan kerusakan per kabupaten, sumber resminya DIBI
BNPB, yang belum bisa diambil tanpa login.

Open-Meteo mencocokkan nama lokasi ke hasil pertama pencarian
lokasinya, jadi periksa kolom `lokasi` (atau tulis koordinat `lintang,bujur`). Data cuaca berlisensi
CC BY 4.0 untuk penggunaan non-komersial.

World Bank, IMF, OECD, USGS, NASA FIRMS, GDACS, NASA EONET, Open-Meteo, dan Harga Pangan (PIHPS BI) memakai API publik tanpa kunci, login, atau browser. Nilai IMF dalam satuan
penuh (jiwa, dolar AS), bukan juta atau miliar, dan setiap baris diberi tanda **aktual** atau
**proyeksi**. Hasilnya berupa tabel panjang (satu baris per negara, indikator, dan tahun) dengan
grafik per indikator di tab **Ringkasan**.

SINTA memakai situs publik sinta.kemdiktisaintek.go.id tanpa login, lewat sesi Scrapling yang meniru Chrome
(SINTA menolak klien non-browser dengan 403). Paket `sinta-scraper` 1.2.0 tidak dipakai karena masih menunjuk
ke domain lama dan markup yang sudah berubah. Daftar penulis per afiliasi dan profil penulis stabil dan lengkap.
**Daftar dokumen per penulis tidak lengkap**: SINTA hanya menampilkan 10 dokumen acak per daftar, tanpa halaman
berikutnya, dan isinya berganti tiap diminta. Karena itu mode **Contoh dokumen** menandai setiap baris sebagai
contoh. Untuk jumlah dokumen dan sitasi yang utuh, pakai mode **Profil penulis**.

## Jalankan di laptop

Pasang **Python 3.11**, lalu clone repositori atau ekstrak ZIP sumber. Pemasangan
awal memerlukan internet untuk mengunduh dependensi dan Chromium.

### macOS

Buka **`Jalankan Scraper Workspace.command`**. Jika menjalankan dari Terminal,
masuk ke folder proyek dan jalankan:

```bash
bash "Jalankan Scraper Workspace.command"
```

### Windows

Aktifkan Python Launcher saat memasang Python agar perintah `py -3.11` tersedia.
Buka PowerShell di folder proyek. Jalankan tiga perintah berikut saat pemasangan
awal atau setelah dependensi diperbarui.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m workspace_app.setup_runtime
```

Jalankan aplikasi dengan perintah berikut setiap kali membukanya.

```powershell
.\.venv\Scripts\python.exe -m streamlit run workspace_app.py --server.address 127.0.0.1 --server.port 8510
```

Setelah aplikasi berjalan, buka [Scraper Workspace di localhost](http://127.0.0.1:8510).

## Cara pakai

- Menu sidebar dibagi empat: **Data** (Dataset, Lakukan Scraping), **Preprocessing**
  (Gabungkan Data, Preprocessing Data, Proses Berjalan, Dataset Preprocessing), **Analisis** (Rekomendasi Analisis,
  Sentimen, Statistik Data, Frekuensi Kata, Tren Waktu, Topik Teks, Jaringan Kata), dan **Info** (peta analisis per
  jenis data). Tampilan aplikasi selalu mode terang. [Panduan analisis](docs/ANALISIS.md)
- **Lakukan Scraping**: pilih sumber dari katalog (bisa dicari dan difilter
  per kategori), klik **Kumpulkan Data**, isi parameter, periksa panel ringkasan, lalu klik
  **Mulai Ambil Data**. Satu pekerjaan atau login platform bisa aktif pada satu waktu dan
  tetap berjalan ketika berpindah halaman.
- Status dan tombol **Hentikan** tampil di atas halaman. Untuk scraping, proses berhenti
  setelah permintaan yang sedang berjalan selesai.
- **Dataset** mencantumkan semua pekerjaan (scraping, gabungan, dan preprocessing) dengan
  pencarian, filter sumber dan status, serta chip proyek. **Buka** menampilkan cara data
  diambil, tombol **Jalankan ulang dengan parameter ini**, tabel hasil, unduhan **Excel
  (.xlsx)**, **CSV**, atau **JSON**, dan teks metode siap salin untuk laporan. Untuk teks di
  atas batas Excel 32.767 karakter per sel, gunakan CSV atau JSON. Setelah penutupan
  tiba-tiba, pekerjaan menjadi **Terputus** saat aplikasi dibuka kembali; data sampai
  penyimpanan terakhir tetap tersedia.
- **Gabungkan Data** menyatukan dua dataset atau lebih (boleh dari sumber berbeda) menjadi satu
  dataset baru. Kolom `sumber_data` dan `pekerjaan_asal` bisa ditambahkan agar setiap baris
  tetap bisa ditelusuri, dan baris yang persis sama bisa dibuang. Hasilnya muncul di
  **Dataset** dan bisa langsung dibersihkan di Preprocessing atau dianalisis.
- **Proses Berjalan** menampilkan pekerjaan yang sedang jalan: status, jumlah baris tersimpan,
  lama berjalan, progres, log langsung (diperbarui tiap detik), dan tombol **Hentikan**. Setelah
  **Jalankan preprocessing** diklik, kamu otomatis dibawa ke halaman ini. Bila proses sudah
  selesai, halaman ini menampilkan ringkasan dan log terakhir. Tanda ● di menu muncul saat ada
  proses berjalan. **Dataset Preprocessing** mencantumkan semua hasil preprocessing; **Buka**
  menampilkan laporan langkah, konfigurasi, tabel, dan unduhan.
- Setiap scraping dan penggabungan wajib diberi **Nama proyek**. Ketik nama baru, atau pilih
  proyek yang sudah ada. Nama berkas unduhan memakai nama proyek.
- **Analisis** berjalan lokal pada dataset yang sudah selesai: **Statistik Data** (kelengkapan
  kolom, ringkasan angka, sebaran nilai), **Frekuensi Kata** (kata dan pasangan kata teratas),
  **Tren Waktu** (jumlah data per hari, minggu, atau bulan, dengan pembanding; zona waktu
  dihitung dalam WIB), **Topik Teks** (TF-IDF dan NMF), dan **Jaringan Kata** (kata yang
  sering muncul bersama). Halaman **Sentimen** mendukung analisis ulasan dengan
  label bintang atau model RoBERTa Indonesia. Metode RoBERTa memerlukan dependensi
  opsional di `requirements-analysis.txt` dan unduhan model. Analisis kata dan topik
  memakai kolom `teks_bersih` bila dataset sudah
  dipreprocessing, dan membersihkan teks sendiri bila belum (maksimal 20.000 teks pertama).
  Setiap tabel hasil bisa diunduh sebagai CSV.
- Klik **Login platform** jika dibutuhkan oleh sumber (tidak tersedia untuk Google Maps Review dan Google Maps Lokasi).
  Masuk secara manual lewat browser. Workspace memakai profil browser terpisah dari scraper mandiri.

Data Workspace disimpan di `~/Library/Application Support/ScraperWorkspace`
pada macOS atau `%LOCALAPPDATA%\ScraperWorkspace` pada Windows. Penghapusan
riwayat terpisah dari penyimpanan profil login. Berkas riwayat, cookie, profil
browser, dan hasil ekspor tidak diperlukan untuk menjalankan kode dari GitHub.

## Google Scholar

Di **Lakukan Scraping → Akademik → Google Scholar**, pilih **Profil Scholar**
untuk memasukkan URL atau ID peneliti, atau **Cari Kata Kunci** untuk mencari
publikasi berdasarkan topik. Isi nama proyek, lalu klik **Mulai Ambil Data**.
Profil memakai HTTP secara bawaan dan pencarian memakai browser. Metode,
bahasa halaman, dan batas waktu bisa diubah di pengaturan pengambilan.

Buka hasil dari **Dataset** untuk memfilter artikel dan melihat grafik.
Angka pada grafik sitasi tahunan berasal dari profil peneliti. Peringkat
artikel pada pencarian dihitung dari hasil yang lolos filter tampilan.
Unduhan mencakup seluruh dataset. Excel profil memiliki sheet **Artikel**,
**Profil**, dan **Sitasi per Tahun**. Excel pencarian memiliki sheet **Hasil**
dan **Pencarian**. CSV memuat tabel artikel dan JSON menyertakan metadata.

## Google Maps Lokasi

Di **Lakukan Scraping → Maps & Review → Google Maps Lokasi**, masukkan kata kunci
tempat, misalnya `kopi bandung`, lalu atur jumlah hasil maksimum (10–120).
Area opsional bisa ditambahkan untuk mempersempit pencarian. Hasil daftar memuat
nama, kategori, alamat, rating, jumlah ulasan, status buka, koordinat, dan link
tempat.

Centang **Ambil detail tiap tempat** untuk membuka halaman masing-masing tempat
dan melengkapi telepon, website, plus code, serta jam lengkap. Opsi ini jauh
lebih lambat karena setiap tempat dibuka satu per satu.

Hasil bisa dibuka dari **Dataset** dalam bentuk tabel, peta, dan grafik kategori,
lalu diunduh sebagai Excel, CSV, atau JSON. Google Maps biasanya berhenti memuat
sekitar 120 hasil per pencarian. Jika daftar kosong, coba kata kunci yang lebih
umum atau tambahkan area.

## Preprocessing NLP

Halaman **Preprocessing Data** membersihkan teks dari Riwayat scraping atau dari berkas
CSV/Excel. Pilih kolom teks (beberapa kolom digabung), pilih preset, lalu atur
langkahnya. Pratinjau 20 baris pertama berubah langsung saat konfigurasi diubah.

| # | Langkah | Ringan (transformer) | Lengkap (analisis kata) |
| --- | --- | --- | --- |
| 1 | Rapikan tag HTML, entitas, Unicode NFKC, spasi | selalu | selalu |
| 2 | Huruf kecil | ya | ya |
| 3 | URL, email, @mention | ganti token | hapus |
| 4 | Hashtag | buang tanda # | buang tanda # |
| 5 | Emoji | ubah ke kata | hapus |
| 6 | Huruf berulang (bagusss → bagus) | ya | ya |
| 7 | Normalisasi kata gaul | ya | ya |
| 8 | Angka | biarkan | hapus |
| 9 | Tanda baca (tanda hubung dalam kata tetap) | biarkan | hapus |
| 10 | Stopword, kata negasi dipertahankan | – | ya |
| 11 | Stemming Sastrawi | – | ya |
| 12 | Panjang token minimum | – | 2 |
| 13 | Buang baris kosong dan duplikat | ya | ya |

Kata gaul dijalankan sebelum angka dan tanda baca dihapus agar kunci seperti
`b4`, `7an`, dan `w/` tetap cocok. Penggantian hanya satu lapis.

Kamus bawaan ada di `workspace_app/resources/`: `slang.txt` (objek JSON
`{"slang": "formal"}`) dan `stopwords.txt` (satu kata per baris). Halaman
menampilkan pemeriksaan kamus (kunci ganda, kata yang dipetakan ke dirinya
sendiri, pasangan yang saling bertukar) tanpa memperbaikinya otomatis. Kamus
unggahan per proses ditambahkan di atas kamus bawaan dan tidak disimpan. Isian
manual `slang=formal` punya prioritas tertinggi.

Stopword bisa diambil dari kamus bawaan proyek, Sastrawi, daftar Inggris
scikit-learn, dan berkas unggahan. Kamus kata dasar Sastrawi berlisensi
CC-BY-NC-SA 3.0, jadi stemming hanya untuk penggunaan non-komersial.

Dengan **Simpan ke Riwayat Preprocessing**, proses berjalan di latar belakang
(satu antrean dengan scraping) dan hasilnya tersimpan dengan nama proyek di
**Dataset** (sumber **Preprocessing**). Tanpa menyimpan, proses berjalan di halaman dan hasil
bisa diunduh sampai halaman dimuat ulang. Keluaran berisi semua kolom asli,
`pekerjaan_asal` (untuk data dari Riwayat), `teks_bersih`, dan `jumlah_token`,
bersama laporan jumlah per langkah.
Jika masukan sudah berisi `teks_bersih` atau `jumlah_token`, kolom lama diberi
akhiran `_asli` yang unik agar nilainya tetap tersedia.

## Deploy ke Streamlit

Entrypoint aplikasi adalah **`workspace_app.py`** dan dependensi Python ada di
**`requirements.txt`** di root repositori. Lihat [Panduan deployment](docs/DEPLOYMENT.md)
untuk pemasangan di server Linux dan persiapan Streamlit Community Cloud.

Versi saat ini memiliki **satu antrean, satu riwayat, dan satu set profil browser
per instalasi**. Di server, seluruh pengunjung aplikasi akan berbagi state
tersebut. Gunakan akses khusus operator selama isolasi per pengguna belum ada.

## Struktur proyek

```text
workspace_app.py        Titik masuk Streamlit (menu)
workspace_app/          Aplikasi: halaman (studio, views, analysis_view, ...), pekerjaan dan
                        riwayat (jobs), preprocessing, analisis, tema (ui), Google Scholar
scrapers/               Satu folder per sumber (news, gmaps, gmaps_places, instagram, playstore, appstore,
                        saham, threads, x, youtube, worldbank, imf, oecd, usgs, firms, bencana, openmeteo, sinta, pangan), masing-masing berisi scraper.py
scrapers/_shared/       Modul yang dipakai beberapa scraper (stealth_transport.py)
tests/                  Uji otomatis
tools/                  Pemeriksaan manual: transport, sumber publik, kinerja berita
docs/                   Panduan deployment, mode stealth, dan analisis
.streamlit/             Konfigurasi Streamlit dan tema
```

Sumber baru ditambahkan dengan membuat `scrapers/<kunci>/scraper.py`, lalu mendaftarkan
kuncinya di `workspace_app/sources.py` (`LABELS`, `FUNCTIONS`) dan `workspace_app/studio.py`
(katalog).

## Kredit ikon

Logo sumber data (Instagram, YouTube, X, Threads, Google Maps, Google Play, Google Scholar) berasal dari
[Simple Icons](https://simpleicons.org) (berkas SVG berlisensi CC0), dan ikon simbol lainnya dari
[Material Symbols](https://fonts.google.com/icons) (Apache 2.0). Semuanya tersimpan lokal di
`workspace_app/resources/icons/`. Merek dagang dan logo milik pemiliknya masing-masing; di sini dipakai
hanya untuk menandai sumber data.

## Uji pengembang

Setelah memasang dependensi, jalankan perintah berikut menggunakan Python dari
lingkungan virtual proyek.

```bash
python -m pip install pytest
python -m pytest -q tests
```

Untuk pemeriksaan manual, jalankan:

```bash
python tools/check_news_performance.py
python tools/check_transport.py
```

Uji Workspace mencakup pipeline preprocessing, form seluruh sumber terdaftar, pekerjaan tunggal, penghentian, pemulihan
hasil parsial, nama proyek, dan ekspor. Uji integrasi RoBERTa bersifat opsional
dan memerlukan model serta variabel lingkungan `RUN_SLOW=1`.
