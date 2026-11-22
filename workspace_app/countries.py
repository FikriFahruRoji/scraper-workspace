"""Countries offered in the World Bank and IMF forms (ISO 3166-1 alpha-3), with Indonesian names."""

COUNTRIES = {
    "IDN": "Indonesia", "MYS": "Malaysia", "SGP": "Singapura", "THA": "Thailand", "VNM": "Vietnam",
    "PHL": "Filipina", "BRN": "Brunei Darussalam", "KHM": "Kamboja", "LAO": "Laos", "MMR": "Myanmar",
    "TLS": "Timor-Leste", "CHN": "Tiongkok", "JPN": "Jepang", "KOR": "Korea Selatan", "IND": "India",
    "AUS": "Australia", "NZL": "Selandia Baru", "USA": "Amerika Serikat", "CAN": "Kanada",
    "MEX": "Meksiko", "BRA": "Brasil", "ARG": "Argentina", "GBR": "Inggris Raya", "DEU": "Jerman",
    "FRA": "Prancis", "ITA": "Italia", "ESP": "Spanyol", "NLD": "Belanda", "RUS": "Rusia",
    "TUR": "Turki", "SAU": "Arab Saudi", "ARE": "Uni Emirat Arab", "EGY": "Mesir", "ZAF": "Afrika Selatan",
    "NGA": "Nigeria", "KEN": "Kenya", "PAK": "Pakistan", "BGD": "Bangladesh", "LKA": "Sri Lanka",
    "CHL": "Chili", "COL": "Kolombia", "PER": "Peru", "POL": "Polandia", "CHE": "Swiss",
}


def clean_codes(text):
    """Split free text such as 'nor, che; swe' into upper-case three-letter codes."""
    parts = [part.strip().upper() for part in text.replace(";", ",").replace(" ", ",").split(",")]
    return [part for part in dict.fromkeys(parts) if part]
