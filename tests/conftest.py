import hashlib
import shutil
from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

PDF_BYTES = b"%PDF-1.4\n% test document\n"
TXT_BYTES = b"hello world\n"


@pytest.fixture(autouse=True)
def isolated_trusted_checkpoint_directory(tmp_path, monkeypatch):
  monkeypatch.setenv("PACKAGE_TURBO_TRUSTED_STATE_DIR", str(tmp_path / "trusted-state"))


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


METADATA_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<paket xmlns="http://bar.admin.ch/arelda/v4" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
       xsi:type="paketSIP" schemaVersion="4.0">
  <paketTyp>SIP</paketTyp>
  <inhaltsverzeichnis>
    <ordner>
      <name>header</name>
    </ordner>
    <ordner>
      <name>content</name>
      <ordner>
        <name>d0001</name>
        <datei id="_file1">
          <name>p000001.pdf</name>
          <originalName>Bericht.pdf</originalName>
          <pruefalgorithmus>MD5</pruefalgorithmus>
          <pruefsumme>{pdf_md5}</pruefsumme>
        </datei>
        <datei id="_file2">
          <name>p000002.txt</name>
          <originalName>Notiz.txt</originalName>
          <pruefalgorithmus>MD5</pruefalgorithmus>
          <pruefsumme>{txt_md5}</pruefsumme>
        </datei>
      </ordner>
      <ordner>
        <name>empty</name>
      </ordner>
    </ordner>
  </inhaltsverzeichnis>
  <ablieferung xsi:type="ablieferungGeverSIP">
    <ablieferungstyp>GEVER</ablieferungstyp>
    <ablieferndeStelle>Testamt</ablieferndeStelle>
    <entstehungszeitraum>
      <von><datum>2006</datum></von>
      <bis><datum>2010</datum></bis>
    </entstehungszeitraum>
    <ablieferungsnummer>2026/1</ablieferungsnummer>
    <schutzfristenkategorie>BGA</schutzfristenkategorie>
    <schutzfrist>30</schutzfrist>
    <provenienz>
      <aktenbildnerName>Testamt</aktenbildnerName>
      <registratur>Registratur A</registratur>
    </provenienz>
    <ordnungssystem>
      <name>Testplan</name>
      <ordnungssystemposition id="_osp1">
        <nummer>1</nummer>
        <titel>Allgemeines</titel>
        <dossier id="_dos1">
          <titel>Dossier mit Datei</titel>
          <entstehungszeitraum>
            <von><datum>2007</datum></von>
            <bis><datum>2008</datum></bis>
          </entstehungszeitraum>
          <aktenzeichen>1.1</aktenzeichen>
          <dokument id="_doc1">
            <titel>Bericht</titel>
            <erscheinungsform>digital</erscheinungsform>
            <dateiRef>_file1</dateiRef>
          </dokument>
        </dossier>
        <dossier id="_dos2">
          <titel>Leer</titel>
          <entstehungszeitraum>
            <von><datum>2009</datum></von>
            <bis><datum>keine Angabe</datum></bis>
          </entstehungszeitraum>
          <aktenzeichen>1.2</aktenzeichen>
        </dossier>
      </ordnungssystemposition>
    </ordnungssystem>
  </ablieferung>
</paket>
"""


@pytest.fixture
def sip_dir(tmp_path: Path) -> Path:
    root = tmp_path / "SIP_20260101_Testamt_Ref"
    (root / "header").mkdir(parents=True)
    (root / "content" / "d0001").mkdir(parents=True)
    (root / "content" / "empty").mkdir()
    (root / "content" / "d0001" / "p000001.pdf").write_bytes(PDF_BYTES)
    (root / "content" / "d0001" / "p000002.txt").write_bytes(TXT_BYTES)
    (root / "header" / "metadata.xml").write_text(
        METADATA_TEMPLATE.format(pdf_md5=_md5(PDF_BYTES), txt_md5=_md5(TXT_BYTES)), encoding="utf-8"
    )
    return root


@pytest.fixture
def sample_sip(tmp_path: Path) -> Path:
    """Copy of the KOST-CECO GEVER test package, if it has been downloaded to samples/."""
    source = SAMPLES_DIR / "SIP_20220328_KOST_eCH1.2G_1a-3d-IO"
    if not source.is_dir():
        pytest.skip("KOST-CECO sample SIP not available in samples/")
    target = tmp_path / source.name
    shutil.copytree(source, target)
    return target
