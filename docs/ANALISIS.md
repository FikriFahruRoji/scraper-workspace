# Rekomendasi Analisis per Karakteristik Data

Dokumen ini adalah kontrak fitur Analisis di nOCturnal Scraper: dataset apa
cocok dengan analisis apa, memakai library gratis apa, dan dengan syarat data
apa. Semua berjalan di komputer pengguna, tanpa token dan tanpa LLM.

Status: **Tahap 1** (hub Rekomendasi Analisis + keluarga A Ulasan) dan
**Tahap 2** (keluarga B Media sosial) sudah dibangun.
Keluarga lain menyusul berurutan C → D → F → E → G → Lintas.

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

| Analisis | Metode / library | Syarat |
|---|---|---|
| Kata pembeda antarportal | Weighted log-odds ratio dengan prior Dirichlet (Monroe dkk., 2008) | ≥ 2 portal |
| Sentimen per portal | model yang sama dengan A | — |
| Topik dari waktu ke waktu | BERTopic (MIT) + `paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0); NMF sebagai tingkat ringan; coherence ditampilkan | ≥ 50 artikel |
| Entitas bernama | model NER IndoBERT (opsional, setelah cek lisensi) | — |

### D. Deret waktu — Saham, Harga Pangan, Cuaca

| Analisis | Metode / library | Syarat |
|---|---|---|
| Forecast | statsforecast (Apache-2.0): AutoARIMA, AutoETS, MSTL; backtest pada data terakhir; MAPE/MASE dibanding naif | ≥ 2 siklus musiman dan ≥ 30 titik per seri; horizon ≤ 20 % panjang data |
| Dekomposisi dan stasioneritas | statsmodels (BSD): STL, uji ADF | ≥ 2 siklus |
| Efek Ramadan / Lebaran | penanda dari `holidays` (MIT) | harga pangan harian |
| Saham | return dan sebarannya; volatilitas GARCH(1,1) dengan `arch` (NCSA); MA, RSI, MACD ditulis dengan pandas; forecast selalu dibanding random walk | ≥ 250 hari untuk GARCH |
| Cuaca | anomali terhadap rata-rata periode | — |

### E. Panel negara-tahun — World Bank, IMF, OECD

Titik tahunan sedikit, jadi **tanpa forecast**.

| Analisis | Metode / library | Syarat |
|---|---|---|
| Tren, CAGR, perbandingan antarnegara | pandas | ≥ 2 tahun |
| Korelasi antarindikator | Pearson / Spearman | ≥ 2 indikator |
| Klaster negara | k-means pada indikator terstandardisasi (scikit-learn) | ≥ 5 negara |
| Regresi panel efek tetap | `linearmodels` (NCSA) | ≥ 2 indikator dan ≥ 3 negara |

### F. Kejadian geospasial — USGS, NASA FIRMS, Bencana, Google Maps Lokasi

| Analisis | Metode / library | Syarat |
|---|---|---|
| Peta kepadatan | pydeck (Apache-2.0) agregasi heksagon (200 ribu titik tetap ringan) | koordinat |
| Klaster titik | HDBSCAN (BSD) jarak haversine | ≥ 30 titik |
| Agregasi per kabupaten/kota | point-in-polygon dengan `scrapers/bencana/wilayah_idn.json` (geoBoundaries, CC BY 3.0 IGO) | titik di Indonesia |
| Gempa | kurva Gutenberg–Richter dan b-value (estimator Aki), sebaran kedalaman | ≥ 50 gempa |

### G. Bibliometrik — Google Scholar, SINTA

| Analisis | Metode / library | Syarat |
|---|---|---|
| Jaringan co-author | networkx dari kolom `authors` Google Scholar | ≥ 10 publikasi |
| Sitasi per tahun, topik judul | pandas, NMF | — |
| Sebaran h-index dan sitasi per prodi/afiliasi, Scopus vs GS | pandas | data profil SINTA |

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
- Grootendorst, M. (2022). BERTopic: Neural topic modeling with a class-based TF-IDF procedure. arXiv:2203.05794.
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
