# Third-Party Licenses and Notices

This register identifies third-party packages, tools, icons, schemas, standards data, and sample material currently used or shipped with Package Turbo. Package Turbo's original code is licensed under the MIT License in [`LICENSE`](LICENSE). That license does not replace or extend the separate terms of third-party material.

Python dependency versions are constrained in [`pyproject.toml`](pyproject.toml) but are not locked. The license of the resolved version and its transitive dependencies must be checked for each release. This register is informational and is not legal advice.

## Python Dependencies

| Component | Use | License | Source and license information |
| --- | --- | --- | --- |
| lxml | Runtime XML parsing and validation | BSD-3-Clause | [Upstream license](https://github.com/lxml/lxml/blob/master/LICENSE.txt) |
| msoffcrypto-tool | Runtime Office encryption detection | MIT | [PyPI project and license metadata](https://pypi.org/project/msoffcrypto-tool/) |
| pypdf | Runtime PDF encryption detection | BSD-3-Clause | [Upstream license](https://github.com/py-pdf/pypdf/blob/main/LICENSE) |
| rfc8785 | Runtime canonical JSON for audit records | Apache-2.0 | [Upstream license](https://github.com/trailofbits/rfc8785.py/blob/main/LICENSE) |
| pytest | Development and test dependency | MIT | [Upstream license](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| openpyxl | Development/test tooling for workbook-based matrices | MIT | [PyPI project and license metadata](https://pypi.org/project/openpyxl/) |

Binary lxml wheels may include native libraries; consult the selected wheel's notices.

## Tools and Visual Assets

The browser uses [fflate](https://github.com/101arrowz/fflate) (MIT) to package
selected files and folders for bounded server-side content imports. Its resolved
version is recorded in `web/package-lock.json`.

| Component | Use | License | Source and license information |
| --- | --- | --- | --- |
| DROID | Optional external file-format identification executable; not bundled by this repository | BSD-3-Clause | [Official repository license](https://github.com/digital-preservation/droid/blob/main/LICENSE) |
| Lucide icons | SVG icons in `web/public/` | ISC; certain Feather-derived icons retain MIT terms | [Lucide license and icon-specific notices](https://github.com/lucide-icons/lucide/blob/main/LICENSE) |

Follow the upstream Lucide license for its icon-specific attribution and exceptions. The app branding file `web/public/package-turbo.svg` is not identified here as a Lucide asset.

## Schemas, Standards Data, and Samples

| Material | Shipped location/use | License status | Source |
| --- | --- | --- | --- |
| METS 1.12.1 and METS 2 schemas | `ph_backend/resources/preservation/` | CC0 1.0; the bundled schema files state this explicitly | [METS at the Library of Congress](https://www.loc.gov/standards/mets/) and [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| PREMIS 3.0 schema | `ph_backend/resources/preservation/premis-v3-0.xsd` | No license statement found in the bundled file or the linked official schema page; confirm before redistribution | [PREMIS at the Library of Congress](https://www.loc.gov/standards/premis/) |
| XLink schema | `ph_backend/resources/preservation/xlink.xsd` | No license statement found in the bundled file; its LOC-hosted source does not establish a license in the local copy. Confirm applicable terms before redistribution | [LOC-hosted schema](https://www.loc.gov/standards/mets/xlink.xsd) |
| eCH-0160 XSDs and derived metadata requirements | `ph_backend/resources/xsd/` and `ph_backend/resources/metadata_requirements.csv` | No explicit reuse license found in the bundled files or on the official standard page checked; not relicensed by this project | [eCH-0160 v1.3.0 and attachments](https://www.ech.ch/de/ech/ech-0160/1.3.0) |
| KOST KaD ratings and curated PUID mapping | `ph_backend/file_formats.py` | The KOST source page does not state a reuse license; confirm terms for redistribution of derived catalog data | [KOST catalog of archival file formats](https://kost-ceco.ch/cms/kad_main_de.html) |
| KOST eCH-0160 sample SIPs | `samples/` | The KOST page publishes test packages but no reuse license was identified there; confirm terms before redistributing the packages or their contents | [KOST eCH-0160 test packages](https://kost-ceco.ch/cms/ech-0160-archivische-ablieferungsschnittstelle.html) |

The eCH, PREMIS, XLink, KOST catalog, and KOST sample entries above have unresolved reuse terms. Their inclusion in this repository is not a representation that they are covered by the Package Turbo MIT License. Obtain or verify applicable permission before redistributing those materials in releases.

## Notices for Redistributed Builds

This source-level inventory is not a generated bill of materials. Before producing a redistributable build, record the resolved dependency versions and include the license texts and notices required by those exact packages and bundled components.
