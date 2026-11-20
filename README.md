# Scraper Workspace

Satu aplikasi Streamlit untuk mengumpulkan dan mengekspor data
dari **9 sumber**. Workspace memakai **Scrapling stealth** untuk jalur
browser dan menyimpan riwayat pekerjaan di SQLite.

| Sumber | Data yang tersedia |
| --- | --- |
| Google Scholar | Profil publik, metrik dan riwayat sitasi, atau publikasi berdasarkan kata kunci |
| Berita | Artikel dari 10 portal Indonesia |
| Google Maps dan Play Store | Ulasan tempat dan aplikasi |
| Instagram, Threads, dan X | Post, profil, pencarian, atau balasan sesuai sumber |
| YouTube | Komentar video dan balasan |
| Saham | Harga historis OHLCV, dividen, dan split |

## Jalankan di laptop

1. Pasang **Python 3.11** dan siapkan koneksi internet untuk pemasangan awal.
   Di macOS pastikan `python3.11` tersedia. Di Windows aktifkan Python Launcher
   agar perintah `py -3.11` tersedia.
2. Clone repositori atau ekstrak ZIP sumber.
3. Jalankan **`Jalankan Scraper Workspace.command`** di macOS atau
   **`Jalankan Scraper Workspace.bat`** di Windows. Launcher menyiapkan
   lingkungan Python dan memasang Chromium untuk Scrapling.
4. Buka [Scraper Workspace di localhost](http://127.0.0.1:8510).
   Aplikasi lokal hanya mendengarkan di `127.0.0.1`.

Jika macOS meminta menjalankan launcher dari Terminal, gunakan
`bash "Jalankan Scraper Workspace.command"` dari folder proyek. Delapan
scraper juga punya launcher lokal masing-masing.

## Cara pakai

- Menu sidebar dibagi tiga: **Data** (Dataset, Lakukan Scraping), **Preprocessing**
  (Gabungkan Data, Preprocessing Data, Proses Berjalan, Dataset Preprocessing), dan **Analisis** (Statistik Data, Frekuensi
  Kata, Tren Waktu, Topik Teks, Jaringan Kata). Tema dipilih di sidebar, bagian **Pengaturan**: **Sistem**
  (mengikuti komputermu), **Terang**, atau **Gelap**. Pilihan disimpan di komputer ini.
- **Lakukan Scraping**: pilih sumber dari katalog (bisa dicari, diurutkan, dan difilter
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
  sering muncul bersama). Analisis teks memakai kolom `teks_bersih` bila dataset sudah
  dipreprocessing, dan membersihkan teks sendiri bila belum (maksimal 20.000 teks pertama).
  Setiap tabel hasil bisa diunduh sebagai CSV.
- Klik **Login platform** jika dibutuhkan oleh sumber (tidak tersedia untuk Google Maps). Masuk secara manual lewat
  browser. Workspace memakai profil browser terpisah dari scraper mandiri.

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
**`requirements.txt`** di root repositori. Lihat [Panduan deployment](DEPLOYMENT.md)
untuk pemasangan di server Linux dan persiapan Streamlit Community Cloud.

Versi saat ini memiliki **satu antrean, satu riwayat, dan satu set profil browser
per instalasi**. Di server, seluruh pengunjung aplikasi akan berbagi state
tersebut. Gunakan akses khusus operator selama isolasi per pengguna belum ada.

## Uji pengembang

Setelah memasang dependensi dan menyiapkan lingkungan, jalankan:

```bash
python -m pip install pytest
python -m pytest -q tests
python tools/check_news_performance.py
```

Uji Workspace mencakup pipeline preprocessing, form 9 sumber, pekerjaan tunggal, penghentian, pemulihan
hasil parsial, nama proyek, dan ekspor. Integrasi browser dan launcher
gabungan telah diuji di macOS. Launcher Windows belum diuji pada perangkat
Windows.
