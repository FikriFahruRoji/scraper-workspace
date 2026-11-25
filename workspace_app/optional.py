"""Optional analysis packages: whether they are installed, and the command that installs them."""
from importlib.util import find_spec

GROUPS = {
    "transformer": ("torch", "transformers", "huggingface_hub", "safetensors"),
    "topic": ("torch", "transformers", "huggingface_hub", "safetensors", "sentence_transformers", "bertopic",
              "umap", "hdbscan"),
    "timeseries": ("statsforecast", "statsmodels", "arch", "holidays"),
    "panel": ("linearmodels",),
    "cross": ("statsmodels",),
}
INSTALL_COMMAND = "pip install -r requirements-analysis.txt"


def missing(group):
    """Packages of `group` that cannot be imported."""
    return [name for name in GROUPS[group] if find_spec(name) is None]


def available(group):
    return not missing(group)
