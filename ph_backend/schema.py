"""Static knowledge about the eCH-0160 / arelda metadata structure (verified against the v1.2 XSDs)."""

from dataclasses import dataclass

TEXT = "text"
MULTILINE = "multiline"
TIME_SPAN = "timespan"
TIME_POINT = "timepoint"
READONLY = "readonly"

READ_ONLY_KINDS: set[str] = set()


@dataclass(frozen=True)
class FieldDef:
    tag: str
    label: str
    type: str = TEXT
    primary: bool = False
    required: bool = False


KIND_LABELS = {
    "paket": "Paket",
    "ablieferung": "Ablieferung",
    "provenienz": "Provenienz",
    "ordnungssystem": "Ordnungssystem",
    "ordnungssystemposition": "Ordnungssystemposition",
    "dossier": "Dossier",
    "subdossier": "Subdossier",
    "dokument": "Dokument",
    "vorgang": "Vorgang",
    "aktivitaet": "Aktivität",
    "mappe": "Mappe",
    "datei": "Datei",
    "ordner": "Ordner",
    "archivischerVorgang": "Archivischer Vorgang",
}

# "dossier" also hosts subdossiers.
EDITOR_TABS = [
    ("ueberblick", "Überblick"),
    ("statistik", "Statistik"),
    ("validierung", "Validierung"),
    ("ordnungssystem", "Ordnungssystem"),
    ("ordnungssystemposition", "Ordnungssystemposition"),
    ("dossier", "Dossier"),
    ("dokument", "Dokument"),
    ("vorgang", "Vorgang"),
    ("aktivitaet", "Aktivität"),
    ("mappe", "Mappe"),
    ("datei", "Datei"),
    ("ordner", "Ordner"),
]

# Child elements that form the object hierarchy and are never shown as editable fields.
STRUCTURAL_TAGS = {
    "inhaltsverzeichnis", "ablieferung", "provenienz", "ordnungssystem",
    "ordnungssystemposition", "dossier", "dokument", "vorgang", "aktivitaet",
    "mappe", "datei", "ordner", "dateiRef", "archivischerVorgang", "zusatzDaten",
    "eigenschaft", "archivischeNotiz", "unstrukturierterAnhang", "referenzUebernahmedossier",
}

_F = FieldDef

# Fields per object kind; list order is the display order, primary fields are always visible.
FIELDS: dict[str, list[FieldDef]] = {
    "paket": [
        _F("paketTyp", "Paket Typ", READONLY, primary=True, required=True),
    ],
    "ablieferung": [
        _F("ablieferungstyp", "Ablieferungstyp", READONLY, primary=True, required=True),
        _F("ablieferndeStelle", "Abliefernde Stelle", primary=True, required=True),
        _F("ablieferungsnummer", "Ablieferungsnummer", primary=True),
        _F("entstehungszeitraum", "Entstehungszeitraum", TIME_SPAN, primary=True),
        _F("schutzfristenkategorie", "Schutzfristenkategorie", primary=True),
        _F("schutzfrist", "Schutzfrist", primary=True),
        _F("ablieferungsteile", "Ablieferungsteile"),
        _F("angebotsnummer", "Angebotsnummer"),
        _F("referenzBewertungsentscheid", "Referenz Bewertungsentscheid"),
        _F("referenzSchutzfristenFormular", "Referenz Schutzfristenformular"),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "provenienz": [
        _F("aktenbildnerName", "Aktenbildner Name", primary=True, required=True),
        _F("systemName", "System Name", primary=True),
        _F("registratur", "Registratur", primary=True),
        _F("systemBeschreibung", "System Beschreibung", MULTILINE),
        _F("existenzzeitraum", "Existenzzeitraum", TIME_SPAN),
        _F("geschichteAktenbildner", "Geschichte Aktenbildner", MULTILINE),
        _F("verwandteSysteme", "Verwandte Systeme", MULTILINE),
        _F("archivierungsmodusLoeschvorschriften", "Archivierungsmodus Löschvorschriften", MULTILINE),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "ordnungssystem": [
        _F("name", "Name", primary=True),
        _F("generation", "Generation", primary=True),
        _F("anwendungszeitraum", "Anwendungszeitraum", TIME_SPAN, primary=True),
        _F("mitbenutzung", "Mitbenutzung", MULTILINE, primary=True),
        _F("bemerkung", "Bemerkung", MULTILINE, primary=True),
    ],
    "ordnungssystemposition": [
        _F("nummer", "Nummer", primary=True),
        _F("titel", "Titel", primary=True),
        _F("schutzfristenkategorie", "Schutzfristenkategorie", primary=True),
        _F("schutzfrist", "Schutzfrist", primary=True),
        _F("schutzfristenBegruendung", "Schutzfristen Begründung", MULTILINE),
        _F("federfuehrendeOrganisationseinheit", "Federführende Organisationseinheit"),
        _F("klassifizierungskategorie", "Klassifizierungskategorie"),
        _F("datenschutz", "Datenschutz"),
        _F("oeffentlichkeitsstatus", "Öffentlichkeitsstatus"),
        _F("oeffentlichkeitsstatusBegruendung", "Öffentlichkeitsstatus Begründung", MULTILINE),
        _F("sonstigeBestimmungen", "Sonstige Bestimmungen", MULTILINE),
    ],
    "dossier": [
        _F("aktenzeichen", "Aktenzeichen", primary=True),
        _F("titel", "Titel", primary=True, required=True),
        _F("entstehungszeitraum", "Entstehungszeitraum", TIME_SPAN, primary=True),
        _F("erscheinungsform", "Erscheinungsform", primary=True),
        _F("schutzfristenkategorie", "Schutzfristenkategorie", primary=True),
        _F("schutzfrist", "Schutzfrist", primary=True),
        _F("schutzfristenBegruendung", "Schutzfristen Begründung", MULTILINE),
        _F("zusatzmerkmal", "Zusatzmerkmal"),
        _F("inhalt", "Inhalt", MULTILINE),
        _F("formInhalt", "Form Inhalt"),
        _F("umfang", "Umfang"),
        _F("federfuehrendeOrganisationseinheit", "Federführende Organisationseinheit"),
        _F("entstehungszeitraumAnmerkung", "Entstehungszeitraum Anmerkung"),
        _F("eroeffnungsdatum", "Eröffnungsdatum", TIME_POINT),
        _F("abschlussdatum", "Abschlussdatum", TIME_POINT),
        _F("klassifizierungskategorie", "Klassifizierungskategorie"),
        _F("datenschutz", "Datenschutz"),
        _F("oeffentlichkeitsstatus", "Öffentlichkeitsstatus"),
        _F("oeffentlichkeitsstatusBegruendung", "Öffentlichkeitsstatus Begründung", MULTILINE),
        _F("sonstigeBestimmungen", "Sonstige Bestimmungen", MULTILINE),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "dokument": [
        _F("titel", "Titel", primary=True, required=True),
        _F("erscheinungsform", "Erscheinungsform", primary=True),
        _F("dokumenttyp", "Dokumenttyp", primary=True),
        _F("entstehungszeitraum", "Entstehungszeitraum", TIME_SPAN, primary=True),
        _F("autor", "Autor"),
        _F("registrierdatum", "Registrierdatum", TIME_POINT),
        _F("klassifizierungskategorie", "Klassifizierungskategorie"),
        _F("datenschutz", "Datenschutz"),
        _F("oeffentlichkeitsstatus", "Öffentlichkeitsstatus"),
        _F("oeffentlichkeitsstatusBegruendung", "Öffentlichkeitsstatus Begründung", MULTILINE),
        _F("sonstigeBestimmungen", "Sonstige Bestimmungen", MULTILINE),
        _F("anwendung", "Anwendung"),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "vorgang": [
        _F("titel", "Titel", primary=True, required=True),
        _F("arbeitsanweisung", "Arbeitsanweisung", MULTILINE, primary=True),
        _F("federfuehrung", "Federführung", primary=True),
        _F("verweis", "Verweis"),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "aktivitaet": [
        _F("vorschreibung", "Vorschreibung", MULTILINE, primary=True, required=True),
        _F("anweisung", "Anweisung", MULTILINE, primary=True),
        _F("bearbeiter", "Bearbeiter", primary=True),
        _F("abschlussdatum", "Abschlussdatum"),
        _F("verweis", "Verweis"),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "mappe": [
        _F("titel", "Titel", primary=True, required=True),
        _F("inhalt", "Inhalt", MULTILINE, primary=True),
        _F("eroeffnungsdatum", "Eröffnungsdatum", TIME_POINT, primary=True),
        _F("erscheinungsform", "Erscheinungsform"),
        _F("bemerkung", "Bemerkung", MULTILINE),
    ],
    "datei": [
        _F("name", "Name", READONLY, primary=True, required=True),
        _F("originalName", "Originalname", primary=True),
        _F("pruefalgorithmus", "Prüfalgorithmus", READONLY, primary=True),
        _F("pruefsumme", "Prüfsumme", READONLY, primary=True),
    ],
    "ordner": [
        _F("name", "Name", READONLY, primary=True, required=True),
        _F("originalName", "Originalname", primary=True),
    ],
    "archivischerVorgang": [
        _F("vorgangstyp", "Vorgangstyp", primary=True),
        _F("beschreibung", "Beschreibung", MULTILINE, primary=True),
        _F("datum", "Datum", primary=True),
        _F("bearbeiter", "Bearbeiter"),
    ],
}
FIELDS["subdossier"] = FIELDS["dossier"]

# Complete XSD child sequence (base type followed by GEVER/FILES extension) used to insert new elements.
CHILD_ORDER: dict[str, list[str]] = {
    "paket": ["paketTyp", "zusatzDaten", "inhaltsverzeichnis", "ablieferung", "archivischerVorgang",
              "referenzUebernahmedossier", "archivischeNotiz"],
    "ablieferung": ["ablieferungstyp", "ablieferndeStelle", "entstehungszeitraum", "ablieferungsteile", "bemerkung",
                    "zusatzDaten", "unstrukturierterAnhang", "ablieferungsnummer", "angebotsnummer",
                    "referenzBewertungsentscheid", "referenzSchutzfristenFormular", "schutzfristenkategorie",
                    "schutzfrist", "archivischeNotiz", "provenienz", "ordnungssystem", "mappe"],
    "provenienz": ["aktenbildnerName", "systemName", "systemBeschreibung", "existenzzeitraum",
                   "geschichteAktenbildner", "bemerkung", "registratur", "verwandteSysteme",
                   "archivierungsmodusLoeschvorschriften", "archivischeNotiz"],
    "ordnungssystem": ["generation", "anwendungszeitraum", "mitbenutzung", "bemerkung", "zusatzDaten", "name",
                       "ordnungssystemposition", "mappe", "archivischeNotiz"],
    "ordnungssystemposition": ["federfuehrendeOrganisationseinheit", "klassifizierungskategorie", "datenschutz",
                               "oeffentlichkeitsstatus", "oeffentlichkeitsstatusBegruendung", "sonstigeBestimmungen",
                               "zusatzDaten", "nummer", "titel", "schutzfristenkategorie", "schutzfrist",
                               "schutzfristenBegruendung", "ordnungssystemposition", "dossier", "mappe",
                               "archivischeNotiz"],
    "dossier": ["zusatzmerkmal", "titel", "inhalt", "formInhalt", "erscheinungsform",
                "federfuehrendeOrganisationseinheit", "entstehungszeitraum", "entstehungszeitraumAnmerkung",
                "klassifizierungskategorie", "datenschutz", "oeffentlichkeitsstatus",
                "oeffentlichkeitsstatusBegruendung", "sonstigeBestimmungen", "bemerkung", "zusatzDaten",
                "aktenzeichen", "eroeffnungsdatum", "abschlussdatum", "vorgang", "schutzfristenkategorie",
                "schutzfrist", "schutzfristenBegruendung", "dossier", "mappe", "dokument", "dateiRef",
                "archivischeNotiz", "umfang"],
    "dokument": ["titel", "autor", "erscheinungsform", "dokumenttyp", "registrierdatum", "entstehungszeitraum",
                 "klassifizierungskategorie", "datenschutz", "oeffentlichkeitsstatus",
                 "oeffentlichkeitsstatusBegruendung", "sonstigeBestimmungen", "bemerkung", "zusatzDaten",
                 "dateiRef", "anwendung", "archivischeNotiz"],
    "vorgang": ["titel", "arbeitsanweisung", "federfuehrung", "verweis", "bemerkung", "zusatzDaten", "aktivitaet"],
    "aktivitaet": ["vorschreibung", "anweisung", "bearbeiter", "abschlussdatum", "verweis", "bemerkung",
                   "zusatzDaten"],
    "mappe": ["titel", "inhalt", "eroeffnungsdatum", "erscheinungsform", "bemerkung", "zusatzDaten", "mappe",
              "dossier", "dokument", "dateiRef", "ordnungssystemposition"],
    "datei": ["name", "originalName", "pruefalgorithmus", "pruefsumme", "eigenschaft", "archivischeNotiz"],
    "ordner": ["name", "originalName", "ordner", "datei"],
    "archivischerVorgang": ["vorgangstyp", "beschreibung", "datum", "bearbeiter"],
}
CHILD_ORDER["subdossier"] = CHILD_ORDER["dossier"]


def field_defs(kind: str) -> list[FieldDef]:
    return FIELDS.get(kind, [])


KIND_HELP = {
    "ueberblick": "Metadaten, welche die Ablieferung als Ganzes identifizieren: Ablieferung, Provenienz und Paket.",
    "statistik": "Zeiträume, Verteilung der Schutzfristen sowie quantitative Angaben zu Ordnungssystem und Dossierbildung.",
    "validierung": "Durchgeführte Prüfungen mit der Anzahl gefundener Fehler und Warnungen.",
    "ordnungssystem": "Name des amtsseitig verwendeten Ordnungssystems und Angaben zu seiner Verwendung.",
    "ordnungssystemposition": "Nummer und Titel der Ordnungssystemposition sowie Angaben aus dem vorarchivischen Bereich.",
    "dossier": "Titel und Identifikationsmerkmale des Dossiers, Entstehungszeitraum und Schutzfristen.",
    "dokument": "Titel und Identifikationsmerkmale des Dokuments, Entstehungszeitraum und Anwendung.",
    "vorgang": "Geschäftsvorfall mit Auftragsbeschreibung und Federführung.",
    "aktivitaet": "Tätigkeit innerhalb eines Vorgangs mit Verantwortlichkeit und Terminierung.",
    "mappe": "Bezeichnung und Inhalt der Mappe. Zulässige Unterobjekte richten sich nach dem Paket-Schema.",
    "datei": "Name der Datei im Paket, Originalname im Ursprungssystem und technische Angaben.",
    "ordner": "Name des Ordners im Paket und in der Originalstruktur.",
}

FIELD_HELP = {
    "ablieferungstyp": "GEVER (aus einem Geschäftsverwaltungssystem) oder FILES (aus einer Dateiablage).",
    "ablieferndeStelle": "Organisationseinheit, welche die Unterlagen abliefert.",
    "ablieferungsnummer": "Vom Archiv vergebene Nummer der Ablieferung.",
    "entstehungszeitraum": "Zeitraum JJJJ oder TT.MM.JJJJ, «keine Angabe» falls unbekannt; geschätzte Angaben mit «ca.».",
    "anwendungszeitraum": "Zeitraum, in dem das Ordnungssystem verwendet wurde.",
    "existenzzeitraum": "Zeitraum, in dem der Aktenbildner existiert hat.",
    "schutzfristenkategorie": "Artikel des Archivierungsgesetzes (BGA), der die Schutzfrist begründet.",
    "schutzfrist": "Dauer der Schutzfrist in Jahren (Ganzzahl).",
    "aktenbildnerName": "Name der Stelle, welche die Unterlagen gebildet hat.",
    "systemName": "Name des Systems, aus dem die Unterlagen stammen.",
    "registratur": "Registratur, welche die Unterlagen verwaltet hat.",
    "nummer": "Eindeutige Nummer der Ordnungssystemposition innerhalb ihrer Stufe.",
    "titel": "Aussagekräftiger, langfristig verständlicher Titel.",
    "aktenzeichen": "Identifikation des Dossiers im Ursprungssystem.",
    "erscheinungsform": "digital, nicht digital oder gemischt.",
    "datenschutz": "Enthält das Objekt besonders schützenswerte Personendaten oder Persönlichkeitsprofile?",
    "eroeffnungsdatum": "Datum der Eröffnung (JJJJ oder TT.MM.JJJJ).",
    "abschlussdatum": "Datum des Abschlusses (JJJJ oder TT.MM.JJJJ).",
    "registrierdatum": "Datum der Registrierung des Dokuments.",
    "originalName": "Name im Ursprungssystem; bleibt beim Umbenennen oder Normalisieren erhalten.",
    "pruefalgorithmus": "Algorithmus der Prüfsumme (MD5, SHA-1, SHA-256, SHA-512).",
    "pruefsumme": "Prüfsumme des Dateiinhalts.",
    "name": "Datei- und Ordnernamen: ASCII-Buchstaben/Ziffern sowie !#$%()+,-.=@[]{}~_ und Leerzeichen.",
    "zusatzDaten": "Generisches Element für weitere beschreibende Metadaten als Merkmale (Name und Wert).",
}


def help_for(tag: str) -> str:
    return FIELD_HELP.get(tag, "")


def label_for(tag: str) -> str:
    if tag == "zusatzDaten":
        return "Zusatzdaten"
    for defs in FIELDS.values():
        for d in defs:
            if d.tag == tag:
                return d.label
    out = []
    for ch in tag:
        if ch.isupper() and out:
            out.append(" ")
        out.append(ch)
    text = "".join(out)
    return text[:1].upper() + text[1:]
