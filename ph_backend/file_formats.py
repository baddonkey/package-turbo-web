"""Archival suitability of file formats according to the KOST-CECO "Katalog archivischer Dateiformate" (KaD 7.1, 2026).

Source: https://kost-ceco.ch/cms/kad_main_de.html (Bewertungsmatrix v7.1.0). Formats are identified by file
extension only, so variants that share an extension (PDF vs. PDF/A-3, TIFF vs. GeoTIFF) get the rating of the
more common format.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

RECOMMENDED = "recommended"
ACCEPTED = "accepted"
NOT_ACCEPTED = "not_accepted"
UNKNOWN = "unknown"

RATING_LABELS = {
    RECOMMENDED: "Empfohlen / Zielformat",
    ACCEPTED: "Akzeptiert",
    NOT_ACCEPTED: "Nicht akzeptiert",
    UNKNOWN: "Nicht im KaD erfasst",
}


@dataclass(frozen=True)
class FileFormat:
    name: str
    category: str
    rating: str


def _formats(category: str, rating: str, name: str, *extensions: str) -> dict[str, FileFormat]:
    return {ext: FileFormat(name, category, rating) for ext in extensions}


KAD_FORMATS: dict[str, FileFormat] = {
    # Text / Bild
    **_formats("Text", ACCEPTED, "TXT", "txt"),
    **_formats("Text", RECOMMENDED, "PDF, PDF/A", "pdf"),
    **_formats("Text", NOT_ACCEPTED, "ODT, ODP (ODF)", "odt", "odp"),
    **_formats("Text", ACCEPTED, "DOCX, PPTX (OOXML)", "docx", "pptx"),
    **_formats("Bild", RECOMMENDED, "TIFF", "tif", "tiff"),
    **_formats("Bild", RECOMMENDED, "JPEG", "jpg", "jpeg"),
    **_formats("Bild", RECOMMENDED, "JPEG2000", "jp2", "jpx", "j2k"),
    **_formats("Bild", ACCEPTED, "PNG", "png"),
    **_formats("Bild", NOT_ACCEPTED, "DNG", "dng"),
    # Audio / Video
    **_formats("Audio", RECOMMENDED, "WAV", "wav"),
    **_formats("Audio", RECOMMENDED, "FLAC", "flac"),
    **_formats("Audio", NOT_ACCEPTED, "ALAC", "m4a"),
    **_formats("Audio", RECOMMENDED, "MP3", "mp3"),
    **_formats("Audio", ACCEPTED, "Ogg Vorbis", "ogg", "oga"),
    **_formats("Video", RECOMMENDED, "MKV", "mkv"),
    **_formats("Video", RECOMMENDED, "MP4", "mp4"),
    **_formats("Video", NOT_ACCEPTED, "MJP2", "mj2"),
    **_formats("Video", ACCEPTED, "AVI", "avi"),
    # Tabellenkalkulation / strukturierte Daten / Datenbanken
    **_formats("Tabellenkalkulation", ACCEPTED, "XLS", "xls"),
    **_formats("Tabellenkalkulation", RECOMMENDED, "ODS", "ods"),
    **_formats("Tabellenkalkulation", RECOMMENDED, "XLSX", "xlsx"),
    **_formats("Strukturierte Daten", ACCEPTED, "CSV", "csv"),
    **_formats("Strukturierte Daten", RECOMMENDED, "XML", "xml", "xsd"),
    **_formats("Strukturierte Daten", ACCEPTED, "JSON", "json"),
    **_formats("Datenbanken", RECOMMENDED, "SIARD", "siard"),
    **_formats("Datenbanken", ACCEPTED, "SQLX", "sqlx"),
    **_formats("Datenbanken", ACCEPTED, "SQLScript", "sql"),
    # E-Mail / Mailboxen / Hypertext / Webseiten
    **_formats("E-Mail", RECOMMENDED, "EML", "eml"),
    **_formats("E-Mail", ACCEPTED, "MSG", "msg"),
    **_formats("Mailboxen", ACCEPTED, "PST", "pst"),
    **_formats("Mailboxen", RECOMMENDED, "MBOX", "mbox"),
    **_formats("Hypertext-Datei", RECOMMENDED, "HTML", "html", "htm"),
    **_formats("Hypertext-Datei", NOT_ACCEPTED, "MHTML", "mht", "mhtml"),
    **_formats("Webseiten", NOT_ACCEPTED, "ARC", "arc"),
    **_formats("Webseiten", RECOMMENDED, "WARC", "warc"),
    **_formats("Webseiten", RECOMMENDED, "WACZ", "wacz"),
    # GIS / CAD-CAM
    **_formats("GIS", RECOMMENDED, "INTERLIS", "itf", "xtf", "ili"),
    **_formats("GIS", ACCEPTED, "Shapefile", "shp", "shx", "dbf", "prj", "cpg"),
    **_formats("GIS", ACCEPTED, "GeoPackage", "gpkg"),
    **_formats("GIS", RECOMMENDED, "Rasterdaten mit World-File", "tfw", "tifw", "jgw", "j2w", "pgw", "wld"),
    **_formats("CAD/CAM", RECOMMENDED, "DWG", "dwg"),
    **_formats("CAD/CAM", ACCEPTED, "DXF", "dxf"),
    **_formats("CAD/CAM", NOT_ACCEPTED, "STEP", "stp", "step"),
    **_formats("CAD/CAM", RECOMMENDED, "IFC", "ifc"),
}

DROID_KAD_FORMATS: dict[str, FileFormat] = {
    "fmt/1756": KAD_FORMATS["odt"],
    "fmt/412": KAD_FORMATS["docx"],
}


def classify(filename: str) -> FileFormat:
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")
    return KAD_FORMATS.get(ext, FileFormat(ext.upper() or "(ohne Endung)", "", UNKNOWN))


def classify_puid(puid: str) -> FileFormat | None:
    return DROID_KAD_FORMATS.get(puid.strip())
