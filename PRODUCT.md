# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Researchers and students (mahasiswa, dosen, peneliti) in Indonesia who collect
social media posts, news articles, reviews, and market data for theses,
dissertations, and research projects, then clean the text for analysis. They
work alone at a laptop, usually one research project at a time, and come back
to the same data over weeks: collect, check what arrived, rerun with other
parameters, preprocess, export to Excel/CSV for their analysis tools.

## Product Purpose

nOCturnal Scraper (Scraper Workspace) gathers data from twenty sources in one
application and prepares it for analysis: Berita (10 Indonesian news portals),
Google Maps reviews and place search, Instagram, Play Store and App Store reviews, SINTA
(Indonesian academic index), World Bank, IMF and OECD country statistics, USGS earthquakes, NASA FIRMS fire hotspots, GDACS and NASA EONET disasters (floods, cyclones, volcanoes, droughts, landslides), Open-Meteo weather, PIHPS Bank Indonesia food prices,
Saham (OHLCV, dividends,
splits), Threads, X/Twitter, and YouTube comments. Every job is stored with its
parameters, log, and rows, grouped under a project name, and exported as Excel,
CSV, or JSON. Success means a researcher gets a usable, traceable dataset for
their study without paying per row and without handing their data or logins to
a third party.

## Positioning

- **Free, no tokens.** No cost per row; the only limits are time and the
  maximums the user sets.
- **Local and private.** Data, cookies, and login browser profiles stay on the
  user's own machine (or their own server), not on a third-party service.
- **Stealth scraping with manual platform login.** Scrapling stealth browsers
  plus a manual login flow reach data that needs a signed-in session.
- **Indonesian NLP preprocessing built in.** Slang normalisation, stopwords,
  and Sastrawi stemming tuned for Indonesian text, with presets for transformer
  models and word-level analysis.

## Operating Context

- Runs locally via `Jalankan Scraper Workspace.command` (macOS) or `.bat`
  (Windows) at `http://127.0.0.1:8510`; can also run on a single-operator Linux
  server reached over SSH (see docs/DEPLOYMENT.md).
- One job (scraping, preprocessing, or platform login) runs at a time; jobs keep
  running while the user moves between pages and can be stopped.
- Core flow: Data catalogue → source form → job runs → Data → Dataset Kamu
  (view data, details, rename, delete) → Preprocessing → export.
- History lives in SQLite under the user's application data directory; an
  unexpected shutdown marks jobs as Terputus while keeping saved rows.

## Capabilities and Constraints

- Built with Streamlit (pinned 1.67.0) and Scrapling 0.5.1; Python 3.11.
- Every job requires a project name (Nama proyek); projects group jobs and name
  download files.
- One queue, one history, and one set of browser profiles per installation; no
  per-user isolation yet, so shared servers need operator-only access.
- Platform login is manual in a separate browser window (not offered for
  Google Maps).
- Excel cells cap at 32,767 characters; long text should go to CSV or JSON.
- Sastrawi's stem dictionary is CC-BY-NC-SA 3.0, so stemming is for
  non-commercial use.
- Interface copy is currently Indonesian with an informal "kamu" voice; whether
  this is a binding commitment is undecided.

## Brand Commitments

- Name **nOCturnal Scraper**, credited "Scraping stealth by Fikri Fahru Roji".
- The studio-style flow (catalogue of sources → form per source → Dataset Kamu)
  stays the core interaction, without tokens or pricing.
- The interface stays a Streamlit application.

## Evidence on Hand

- Real job history on the developer's machine (e.g. 25 completed jobs, about
  100k rows across Berita, Google Maps, Instagram, Saham at the time of writing).
- Documentation: README.md, docs/DEPLOYMENT.md, docs/STEALTH_MODE.md.
- No testimonials, user counts, institutional adopters, or benchmarks exist;
  future work must not invent them.

## Product Principles

1. **Traceable data over raw volume.** Every dataset keeps its parameters, log,
   status, and project so a researcher can explain how it was collected.
2. **The user's machine is the boundary.** Never route data, cookies, or
   credentials through services the user did not choose.
3. **Honest limits.** Say plainly when a source returned nothing, was blocked,
   or stopped early, and keep whatever was saved.
4. **One clear path from question to dataset.** Pick a source, set it up,
   watch it run, find it again later; everything else stays secondary.
5. **Free means no artificial friction.** No tokens, quotas, or upsell patterns
   borrowed from paid platforms.
