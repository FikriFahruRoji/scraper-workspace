# Rekomendasi Analisis per Karakteristik Data

Dokumen ini adalah kontrak fitur Analisis di nOCturnal Scraper: dataset apa
cocok dengan analisis apa, memakai library gratis apa, dan dengan syarat data
apa. Semua berjalan di komputer pengguna, tanpa token dan tanpa LLM.

Status: **Tahap 1** (hub Rekomendasi Analisis + keluarga A Ulasan),
**Tahap 2** (keluarga B Media sosial), **Tahap 3** (keluarga C Berita, kecuali entitas bernama),
**Tahap 4** (keluarga D Deret waktu), **Tahap 5** (keluarga F Geospasial),
**Tahap 6** (keluarga E Panel negara-tahun), **Tahap 7** (keluarga G Bibliometrik), dan
**Tahap 8** (Lintas dataset) sudah dibangun.

## Prinsip

1. **Dua tingkat.** Tingkat ringan (scikit-learn, kamus, NMF) selalu ada.
   Tingkat model (transformer, BERTopic) opsional: paketnya ada di
   `requirements-analysis.txt`, dan bobot model diunduh ke folder data aplikasi
   hanya setelah pengguna setuju.
2. **Inferensi berat = pekerjaan.** Sentimen untuk puluhan ribu baris berjalan
   di antrean yang sama dengan scraping, bisa dihentikan, punya log, dan
   hasilnya disimpan sebagai **dataset turunan** yang bisa diekspor.
3. **Tingkat riset.** Setiap hasil membawa parameter, metrik evaluasi, seed,
   versi model, dan paragraf metode yang bisa ditempel ke skripsi.
4. **Batas jujur.** Analisis yang syarat datanya tidak terpenuhi tidak
   dijalankan; aplikasi menyebut alasannya.
5. **Teks yang tepat untuk metode yang tepat.** Transformer membaca teks asli
   yang dinormalisasi ringan; TF-IDF, kamus, dan topik membaca teks bersih.
6. **Lisensi.** Bawaan memakai lisensi permisif (MIT/Apache/BSD). Komponen
   non-komersial diberi label di antarmuka (contoh yang sudah ada: kamus
   stemming Sastrawi, CC-BY-NC-SA).

## Cara jenis data dikenali

Aplikasi membaca sumber pekerjaan. Dataset Preprocessing memakai sumber
masukannya, dataset Sentimen memakai sumber asalnya, dan dataset Gabungan
memakai nilai kolom `sumber_data`. Bila sumber tidak dikenal, pola kolom dipakai:

| Pola kolom | Keluarga |
|---|---|
| teks + `rating` | A Ulasan |
| teks tanpa rating | B Medsos / C Berita |
| `lintang` + `bujur` | F Geospasial |
| `negara` + `tahun` + `nilai` | E Panel negara |
| tanggal + kolom angka | D Deret waktu |

Satu dataset boleh masuk lebih dari satu keluarga (contoh: Bencana = F + D).

## Matriks

### A. Ulasan berbintang — Play Store, App Store, Google Maps Review

Karakteristik: teks pendek opini, **bintang 1–5 sebagai label gratis**,
tanggal, versi aplikasi (Play/App Store), balasan pemilik (Maps).

| Analisis | Metode / library | Syarat data | Status |
|---|---|---|---|
| Sentimen model | RoBERTa Indonesia `w11wo/indonesian-roberta-base-sentiment-classifier` (MIT; dilatih pada SmSA IndoNLU), transformers + torch | ≥ 1 teks | Tahap 1 |
| Sentimen dari bintang | TF-IDF + Logistic Regression, label bintang 1–2 negatif / 4–5 positif, uji 80/20 bertingkat, seed 42 (scikit-learn) | ≥ 200 ulasan berlabel, ≥ 30 per kelas | Tahap 1 |
| Ketidakcocokan bintang–teks | Bintang 4–5 tapi teks negatif, bintang 1–2 tapi teks positif; kesepakatan Cohen's kappa | kolom rating | Tahap 1 |
| Sentimen per aspek | Kamus aspek yang bisa diedit (preset Aplikasi dan Tempat), sentimen kalimat yang menyebut aspek | ≥ 1 aspek | Tahap 1 |
| Tren sentimen | Proporsi sentimen per bulan dan per versi aplikasi | kolom tanggal / versi | Tahap 1 |
| Kata dan topik per bintang | Frekuensi kata, NMF (sudah ada) | ≥ 50 teks | sudah ada |

### B. Teks media sosial — Instagram, X, Threads, YouTube

Karakteristik: tanpa label, banyak slang dan emoji, ada struktur balasan
(`parent_id` Instagram, `id_induk` YouTube) dan jumlah suka.

| Analisis | Metode / library | Syarat | Status |
|---|---|---|---|
| Sentimen | model yang sama dengan A | ≥ 1 teks | Tahap 1 |
| Emosi dan toksisitas (halaman Emosi & Toksisitas, opsional) | emosi: `StevenLimcorn/indonesian-roberta-base-emotion-classifier` (MIT; dilatih pada EmoT IndoNLU, Apache-2.0), lima kelas senang/cinta/sedih/marah/takut tanpa kelas netral, F1 makro 0,72; probabilitas < 0,6 dilaporkan "tidak jelas" (bisa diubah). Toksisitas: `Exqrch/IndoBERTweet-HateSpeech` dan `-Insult` (CC BY-SA 4.0; dilatih pada IndoToxic2024, Apache-2.0) dengan tokenizer `indolem/indobertweet-base-uncased` (Apache-2.0), biner, ditandai bila p ≥ 0,5. Dijalankan sebagai pekerjaan antrean, hasilnya dataset turunan `emosi` | ≥ 1 teks | Tahap 2 |
| Jaringan balasan (halaman Jaringan Balasan) | NetworkX (BSD): graf berarah akun pembalas → akun yang dibalas, dari `parent_id`/`id_induk` (via `id_komentar`) atau `IN_REPLY_TO`; derajat masuk/keluar berbobot, PageRank (α 0,85), betweenness (eksak ≤ 2.000 akun, selebihnya sampel 500 simpul), komunitas Louvain dan modularitas, seed 42; daftar relasi bisa dibuka di Gephi | kolom akun + kolom balasan | Tahap 2 |
| Komentar teratas, akun paling aktif, sentimen × suka (halaman Interaksi & Akun) | pandas; ketimpangan keaktifan dengan koefisien Gini dan porsi 10 % akun teraktif; suka antarsentimen dengan uji Kruskal–Wallis dan ε² (SciPy) | kolom suka / akun; uji butuh ≥ 2 sentimen × ≥ 5 teks | Tahap 2 |
| Indikasi teks berulang (halaman Teks Berulang) | kosinus TF-IDF unigram+bigram ≥ 0,9 (bisa diubah 0,70–1), tautan dan sebutan akun diseragamkan, teks < 3 kata dilewati, grup digabung transitif; bukan tuduhan bot | ≥ 50 teks | Tahap 2 |

Kolom akun dikenali dari `username` (Instagram, X, Threads) atau `nama`
(YouTube); kolom suka dari `suka`, `FAVORITE_COUNT`, atau `LIKE_COUNT`.

### C. Berita — 10 portal

| Analisis | Metode / library | Syarat | Status |
|---|---|---|---|
| Kata pembeda antarportal (halaman Kata Pembeda) | Weighted log-odds ratio dengan prior Dirichlet informatif (Monroe dkk., 2008, pers. 22): tiap portal melawan semua portal lain, prior = frekuensi kata seluruh korpus, skor z (\|z\| ≥ 1,96 signifikan); teks dibersihkan tanpa stemming, kata < 5 kemunculan diabaikan | kolom `portal` (atau `sumber_data`) dengan ≥ 2 nilai, ≥ 5 dokumen per kelompok | Tahap 3 |
| Sentimen per portal (di halaman Sentimen) | model yang sama dengan A; proporsi positif/netral/negatif dan skor bersih per portal, uji chi-kuadrat independensi dengan V Cramér (SciPy) | hasil Sentimen dengan kolom `portal` | Tahap 3 |
| Topik dari waktu ke waktu, tingkat ringan (halaman Topik dari Waktu ke Waktu) | TF-IDF + NMF (scikit-learn) dari teks bersih; porsi tiap topik per periode; koherensi NPMI (kemunculan bersama 10 kata teratas dalam dokumen; Bouma, 2009) | ≥ 50 artikel disarankan | Tahap 3 |
| Topik dari waktu ke waktu, tingkat model | BERTopic 0.17 (MIT; Grootendorst, 2022) dengan `paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0, 256 token, vektor dinormalisasi) → UMAP (5 dimensi, jarak kosinus, seed 42, tata letak lama dikunci agar hasil tetap sama antarversi) → HDBSCAN (ukuran topik minimal bisa diatur, bawaan 15) → c-TF-IDF dari teks bersih (`min_df` 1, karena dipasang pada satu dokumen per topik). Berjalan sebagai pekerjaan antrean; hasilnya dataset turunan `bertopic` dengan kolom `topik_id`, `topik_nama`, `topik_prob`; teks tanpa topik (outlier) dilaporkan terpisah; koherensi NPMI per topik dan rata-rata | ≥ 50 artikel | Tahap 3 |
| Entitas bernama | model NER IndoBERT (opsional, setelah cek lisensi) | — | menunggu cek lisensi |

Paket tingkat model (BERTopic, UMAP, HDBSCAN, sentence-transformers) ada di
`requirements-analysis.txt`; bobot embedding (± 480 MB) diunduh setelah pengguna setuju.

### D. Deret waktu — Saham, Harga Pangan, Cuaca

Semua analisis ada di satu halaman, Deret Waktu. Pengguna memilih dataset, kolom tanggal, kolom nilai (atau
"jumlah kejadian per periode" untuk gempa, titik api, bencana), saringan satu deret (simbol, komoditas,
lokasi), periode (harian, hari kerja, mingguan, bulanan, triwulan), dan cara menggabungkan baris dalam satu
periode. Periode tanpa data diinterpolasi (nol bila menghitung kejadian); bila lebih dari 30 % periode
terisi, deret ditolak. Hanya 2.500 titik terakhir yang dipakai model.

| Analisis | Metode / library | Syarat | Status |
|---|---|---|---|
| Forecast | statsforecast 2.1 (Apache-2.0): naif (random walk), naif musiman, AutoETS, AutoARIMA, MSTL dengan tren ETS; siklus > 24 (mingguan) menjalankan AutoETS/AutoARIMA tanpa musiman. Uji mundur bergulir pada 3 jendela di ujung data (tiap jendela hanya dilatih dari data sebelumnya, minimal 2 siklus data latih); MASE (dibagi galat naif musiman di data latih), MAPE (hanya bila tidak ada nilai nol), RMSE, cakupan selang 95 %; model terbaik menurut MASE dilatih ulang pada seluruh data dengan selang 80 % dan 95 %; selalu dilaporkan apakah ia mengalahkan baseline naif | ≥ 30 titik dan ≥ 2 siklus musiman (harian 7, hari kerja 5, mingguan 52, bulanan 12, triwulan 4); horizon ≤ 20 % panjang data | Tahap 4 |
| Dekomposisi dan stasioneritas | statsmodels 0.15 (BSD): STL robust; kekuatan tren dan musiman (Wang dkk., 2006); uji ADF (autolag AIC) dan KPSS pada level dan selisih pertama; kesimpulan hanya tegas bila kedua uji sepakat | ≥ 30 titik dan ≥ 2 siklus | Tahap 4 |
| Efek Ramadan dan Lebaran | tanggal Idul Fitri dari `holidays` (MIT); Ramadan didekati 30 hari sebelum 1 Syawal, pekan Lebaran H-7 sampai H+7; simpangan log harga terhadap median bergulir terpusat 91 hari; tabel per tahun dan uji Mann–Whitney (hari berdekatan berkorelasi sehingga p cenderung terlalu kecil; tabel per tahun yang utama) | deret harian ≥ 120 titik; tahun dihitung hanya bila ada 45 hari data sebelum dan sesudah | Tahap 4 |
| Saham | return log, volatilitas tahunan (× √252), drawdown maksimum, Jarque–Bera; MA 20/50, RSI 14 (Wilder), MACD (12, 26, 9) ditulis dengan pandas; volatilitas bersyarat GARCH(1,1) galat Student-t dengan `arch` 9 (NCSA) dan prakiraan 10 hari; forecast di bagian Forecast selalu dibanding random walk | harga positif; ≥ 30 return; GARCH ≥ 250 return | Tahap 4 |
| Cuaca | anomali = nilai − rata-rata hari dalam tahun yang sama (dihaluskan ±7 hari, bila data ≥ 3 tahun) atau rata-rata bulan; skor z; ini rata-rata rentang data yang ada, bukan normal iklim 30 tahun | ≥ 1 tahun | Tahap 4 |

Paket (statsforecast, statsmodels, arch, holidays) ada di `requirements-analysis.txt`. statsforecast
mensyaratkan pandas < 3, jadi memasangnya menurunkan pandas ke 2.x; seluruh tes lulus di kedua versi.

### E. Panel negara-tahun — World Bank, IMF, OECD

Titik tahunan sedikit, jadi **tanpa forecast**.

| Analisis | Metode / library | Syarat | Status |
|---|---|---|---|
| Tren, CAGR, perbandingan antarnegara | pandas | ≥ 2 tahun | Tahap 6 |
| Korelasi antarindikator | Pearson / Spearman | ≥ 2 indikator | Tahap 6 |
| Klaster negara | k-means pada indikator terstandardisasi (scikit-learn) | ≥ 5 negara | Tahap 6 |
| Regresi panel efek tetap | `linearmodels` (NCSA) | ≥ 2 indikator dan ≥ 3 negara | Tahap 6 |

### F. Kejadian geospasial — USGS, NASA FIRMS, Bencana, Google Maps Lokasi

Semua analisis ada di satu halaman, Peta Kejadian. Pengguna memilih dataset, menyaring kolom bernilai sedikit
(jenis, negara, keyakinan) dan memilih bobot opsional (magnitudo, FRP, kecerahan). Baris tanpa koordinat,
dengan koordinat di luar ±90/±180, atau tepat (0, 0) dibuang dan jumlahnya ditampilkan.

| Analisis | Metode / library | Syarat | Status |
|---|---|---|---|
| Peta kepadatan | pydeck (Apache-2.0) `HexagonLayer`, agregasi di peramban (hingga 200 ribu titik, selebihnya sampel acak seed 42 untuk peta saja); jari-jari heksagon bisa diatur; bobot opsional; peta dasar CARTO butuh internet | koordinat | Tahap 5 |
| Klaster titik | HDBSCAN (scikit-learn; Campello dkk., 2013), jarak haversine pada radian, `excess of mass`, ukuran klaster minimal bisa diatur; pusat (rata-rata vektor satuan di bola) dan radius median/90 % tiap klaster; di atas 15 ribu titik (HDBSCAN: 10 ribu titik 3 detik, 20 ribu 16 detik, 30 ribu 26 detik) dikelompokkan sampel acak seed 42 | ≥ 30 titik | Tahap 5 |
| Agregasi per kabupaten/kota dan provinsi | point-in-polygon aturan genap-ganjil dengan `scrapers/bencana/wilayah_idn.json` (geoBoundaries IDN ADM2, CC BY 3.0 IGO; provinsi menurut OCHA COD-AB dan pemekaran 2022), luring dan bervektor (numpy); titik di luar semua batas diberi kabupaten terdekat bila ≤ 20 km dari garis batas (jarak dari titik garis batas yang dirapatkan tiap 1 km, akurat ± 1 km; sama dengan aturan scraper Bencana, diuji terhadap `locate` scraper itu); jumlah, jumlah bobot, dan rata-rata per wilayah | titik di Indonesia | Tahap 5 |
| Gempa | Gutenberg–Richter log₁₀ N(≥M) = a − bM; Mc dengan kurvatur maksimum (Wiemer & Wyss, 2000) + 0,2 (Woessner & Wiemer, 2005), bisa diganti pengguna; b dengan penaksir kemungkinan maksimum Aki (1965) dengan koreksi lebar kelas Utsu, galat baku Shi & Bolt (1982), selang 95 %; b tidak dihitung bila gempa di atas Mc < 50; kelas kedalaman dangkal (< 70 km), menengah (70–300 km), dalam (> 300 km) | ≥ 50 gempa bermagnitudo | Tahap 5 |

Tidak ada paket baru: pydeck ikut Streamlit, HDBSCAN dan KD-tree dari scikit-learn dan SciPy.

### G. Bibliometrik — Google Scholar, SINTA

| Analisis | Metode / library | Syarat | Status |
|---|---|---|---|
| Jaringan co-author | networkx dari kolom `authors` Google Scholar | ≥ 10 publikasi | Tahap 7 |
| Sitasi per tahun, topik judul | pandas, NMF | — | Tahap 7 |
| Sebaran h-index dan sitasi per prodi/afiliasi, Scopus vs GS | pandas | data profil SINTA | Tahap 7 |

Daftar dokumen SINTA hanya sampel 10 dokumen per penulis dan isinya berubah,
sehingga analisis tingkat dokumen SINTA **tidak** ditawarkan.

### Lintas dataset

Diselaraskan per tanggal (dan wilayah bila ada), lalu korelasi berlag dan uji
kausalitas Granger (statsmodels), dengan catatan bahwa korelasi bukan sebab
akibat. Urutan: harga pangan × cuaca → bencana/gempa × volume berita →
saham × sentimen berita.

## Rujukan metode

- Wilie, B. dkk. (2020). IndoNLU: Benchmark and Resources for Evaluating Indonesian Natural Language Understanding. AACL-IJCNLP.
- Liu, Y. dkk. (2019). RoBERTa: A Robustly Optimized BERT Pretraining Approach. arXiv:1907.11692.
- Pedregosa, F. dkk. (2011). Scikit-learn: Machine Learning in Python. JMLR 12.
- Cohen, J. (1960). A coefficient of agreement for nominal scales. Educational and Psychological Measurement 20(1).
- Monroe, B. L., Colaresi, M. P., & Quinn, K. M. (2008). Fightin' Words. Political Analysis 16(4).
- Bouma, G. (2009). Normalized (pointwise) mutual information in collocation extraction. GSCL.
- Röder, M., Both, A., & Hinneburg, A. (2015). Exploring the space of topic coherence measures. WSDM.
- McInnes, L., Healy, J., & Melville, J. (2018). UMAP: Uniform Manifold Approximation and Projection for dimension reduction. arXiv:1802.03426.
- Reimers, N., & Gurevych, I. (2019). Sentence-BERT: Sentence embeddings using Siamese BERT-networks. EMNLP-IJCNLP.
- Grootendorst, M. (2022). BERTopic: Neural topic modeling with a class-based TF-IDF procedure. arXiv:2203.05794.
- Garza, A., Mergenthaler-Canseco, M., & Challú, C. (2022). StatsForecast: Lightning fast forecasting with statistical and econometric models. PyCon Salt Lake City.
- Cleveland, R. B., Cleveland, W. S., McRae, J. E., & Terpenning, I. (1990). STL: A seasonal-trend decomposition procedure based on loess. Journal of Official Statistics 6(1).
- Wang, X., Smith, K., & Hyndman, R. (2006). Characteristic-based clustering for time series data. Data Mining and Knowledge Discovery 13(3).
- Dickey, D. A., & Fuller, W. A. (1979). Distribution of the estimators for autoregressive time series with a unit root. JASA 74(366).
- Kwiatkowski, D., Phillips, P. C. B., Schmidt, P., & Shin, Y. (1992). Testing the null hypothesis of stationarity against the alternative of a unit root. Journal of Econometrics 54.
- Hyndman, R. J., & Koehler, A. B. (2006). Another look at measures of forecast accuracy. International Journal of Forecasting 22(4).
- Mann, H. B., & Whitney, D. R. (1947). On a test of whether one of two random variables is stochastically larger than the other. Annals of Mathematical Statistics 18(1).
- Gutenberg, B., & Richter, C. F. (1944). Frequency of earthquakes in California. Bulletin of the Seismological Society of America 34(4).
- Utsu, T. (1966). A statistical significance test of the difference in b-value between two earthquake groups. Journal of Physics of the Earth 14(2).
- Shi, Y., & Bolt, B. A. (1982). The standard error of the magnitude-frequency b value. Bulletin of the Seismological Society of America 72(5).
- Wiemer, S., & Wyss, M. (2000). Minimum magnitude of completeness in earthquake catalogs. Bulletin of the Seismological Society of America 90(4).
- Woessner, J., & Wiemer, S. (2005). Assessing the quality of earthquake catalogues: Estimating the magnitude of completeness and its uncertainty. Bulletin of the Seismological Society of America 95(2).
- Hyndman, R. J., & Athanasopoulos, G. (2021). Forecasting: Principles and Practice (3rd ed.). OTexts.
- Bollerslev, T. (1986). Generalized autoregressive conditional heteroskedasticity. Journal of Econometrics 31(3).
- Campello, R., Moulavi, D., & Sander, J. (2013). Density-based clustering based on hierarchical density estimates. PAKDD.
- Aki, K. (1965). Maximum likelihood estimate of b in the formula log N = a − bM. Bull. Earthquake Research Institute 43.
- Hagberg, A. A., Schult, D. A., & Swart, P. J. (2008). Exploring network structure, dynamics, and function using NetworkX. Proc. 7th Python in Science Conference.
- Page, L., Brin, S., Motwani, R., & Winograd, T. (1999). The PageRank citation ranking: Bringing order to the web. Stanford InfoLab.
- Brandes, U. (2001). A faster algorithm for betweenness centrality. Journal of Mathematical Sociology 25(2).
- Blondel, V. D., Guillaume, J.-L., Lambiotte, R., & Lefebvre, E. (2008). Fast unfolding of communities in large networks. J. Stat. Mech. P10008.
- Saputri, M. S., Mahendra, R., & Adriani, M. (2018). Emotion classification on Indonesian Twitter dataset. IALP.
- Susanto, L. dkk. (2024). IndoToxic2024: A demographically-enriched dataset of hate speech and toxicity types for Indonesian language. arXiv:2406.19349.
- Koto, F., Lau, J. H., & Baldwin, T. (2021). IndoBERTweet: A pretrained language model for Indonesian Twitter with effective domain-specific vocabulary initialization. EMNLP.
- Kruskal, W. H., & Wallis, W. A. (1952). Use of ranks in one-criterion variance analysis. JASA 47(260).
- Tomczak, M., & Tomczak, E. (2014). The need to report effect size estimates revisited. Trends in Sport Sciences 21(1).
- Granger, C. W. J. (1969). Investigating causal relations by econometric models and cross-spectral methods. Econometrica 37(3).
