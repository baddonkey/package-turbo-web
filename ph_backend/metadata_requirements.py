"""Access to the packaged release/profile-specific mandatory metadata fields."""

import csv
from functools import lru_cache
from pathlib import Path

RELEASE_BY_SCHEMA_VERSION = {"4.0": "1.0", "4.1": "1.1", "5.0": "1.2.0", "5.1": "1.3.0"}
ENTITY_TAGS = {
    "Paket": "paket", "Ablieferung": "ablieferung", "Provenienz": "provenienz",
    "Ordnungssystem": "ordnungssystem", "Ordnungssystemposition": "ordnungssystemposition",
    "Dossier": "dossier", "Vorgang": "vorgang", "Aktivität": "aktivitaet", "Mappe": "mappe",
    "Dokument": "dokument", "Inhaltsv.": "inhaltsverzeichnis", "Ordner": "ordner", "Datei": "datei",
    "Datei Referenz": "dateiRef", "Arch. Vorgang": "archivischerVorgang", "Arch. Notiz": "archivischeNotiz",
}
ENTITY_BY_KIND = {value: key for key, value in ENTITY_TAGS.items()}
ENTITY_BY_KIND["subdossier"] = "Dossier"


@lru_cache(maxsize=1)
def mandatory_metadata_fields() -> tuple[dict[str, str], ...]:
    source = Path(__file__).parent / "resources" / "metadata_requirements.csv"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        return tuple(csv.DictReader(stream))


def is_mandatory_metadata_field(schema_version: str, profile: str, kind: str, field: str) -> bool:
    release = RELEASE_BY_SCHEMA_VERSION.get(schema_version)
    entity = ENTITY_BY_KIND.get(kind)
    if release is None or entity is None:
        return False
    return any(row["release"] == release and row["profile"] == profile.upper()
               and row["entity"] == entity and row["xsdName"] == field
               for row in mandatory_metadata_fields())