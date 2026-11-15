# OpenAlex Scraper

Buka `Jalankan OpenAlex.command` di macOS. Untuk menjalankan secara manual,
pasang `requirements.txt`, lalu jalankan `python -m streamlit run app.py`
dari folder ini. Jalankan `scrapling install` di environment yang sama jika
browser Scrapling belum tersedia.

## Ambil metrik dan publikasi

- **Profil penulis:** Masukkan tautan atau ID OpenAlex. Klik **Ambil metrik
  dari halaman profil** untuk membaca metrik dari HTML dengan
  Scrapling StealthySession. Buka profil dan klik angka **Works**
  untuk melihat daftar publikasi.
- **Kata kunci:** Masukkan kueri, lalu buka pencarian melalui tautan
  di aplikasi. Batas hasil diterapkan pada data yang diimpor.
- **Impor publikasi:** Unggah halaman HTML yang hasilnya sudah tampil
  atau CSV yang diunduh lewat **Export results** jika tersedia. Hasil
  akan langsung muncul dan dapat diunduh sebagai CSV. Mode profil
  mengimpor semua publikasi yang terbaca di berkas.

Aplikasi mengurai HTML lokal lewat Scrapling Selector. Isi kolom mengikuti
data yang tersedia di berkas. Daftar berfilter dan pencarian berada di
`/works?…`, yang [robots.txt OpenAlex](https://openalex.org/robots.txt)
kecualikan dari perayapan umum. Daftar tersebut dibuka oleh pengguna
di browser dan diimpor dari berkas.
