"""Where the bundled files live: in a wheel next to the package, else in a source checkout."""

from importlib import resources
from pathlib import Path

CHECKOUT = Path(__file__).parents[2]


def bundled(packaged_name: str, checkout_name: str) -> Path:
    packaged = Path(str(resources.files("miseenplace") / packaged_name))
    return packaged if packaged.is_dir() else CHECKOUT / checkout_name


def demo_data_dir() -> Path:
    return bundled("demo_data", "examples/demo-data")


def skills_dir() -> Path:
    return bundled("skills", "skills")
