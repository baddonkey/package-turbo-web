# package-turbo

An independent workspace for creating, editing and validating eCH-0160
Submission Information Packages (SIPs), built with FastAPI and Angular.
Package Turbo is experimental.

## Browser workspace

See the illustrated [user manual](docs/manual/user-manual.html) and
[gallery of 30 screenshots](docs/manual/gallery.html) for step-by-step browser workflows.

Globally install
```sh
pip install --user uv
```

Requires Python 3.11 or newer, uv, and Node.js 24 LTS. On macOS or Linux, run
these commands from the repository root:

```sh
uv sync --locked --extra web
npm ci --prefix web
npm run build --prefix web
export PACKAGE_TURBO_LOGIN_USERNAME="archivist"
# Copy the generated hash into the environment variable below.
uv run --locked --extra web python -m ph_web.auth
export PACKAGE_TURBO_LOGIN_PASSWORD_HASH='paste-generated-hash-here'
PACKAGE_TURBO_OPERATOR="Your name" \
PACKAGE_TURBO_STATIC_DIR=web/dist/package-turbo-web/browser \
uv run --locked --extra web uvicorn ph_web.app:app --host 127.0.0.1 --port 8000
```

On Windows 11, open PowerShell in the repository root. Generate the password
hash interactively, then set the login details and start the workspace. The
script installs the locked dependencies, builds the browser app, and serves it
on port 8000:

```powershell
$env:PACKAGE_TURBO_LOGIN_USERNAME = "archivist"
uv run --locked --extra web python -m ph_web.auth
$env:PACKAGE_TURBO_LOGIN_PASSWORD_HASH = 'paste-generated-hash-here'
$env:PACKAGE_TURBO_OPERATOR = "Your name"
.\start-web.ps1
```

Use `.\start-web.ps1 -Port 8001` to choose another port.

Open [Package Turbo](http://127.0.0.1:8000) or the
[interactive API docs](http://127.0.0.1:8000/docs). The API contract is available at
[`/openapi.json`](http://127.0.0.1:8000/openapi.json).
Sign in with the configured local account first; API documentation also requires
a session. Without configured credentials, workspace access remains locked.

The service now stores durable sessions and owner-private package permissions.
Existing workspaces need [explicit owner migration](docs/web-workspace.md#migrating-existing-workspaces).
Keycloak login, bounded jobs and quotas are still pending; keep this deployment
on loopback rather than exposing the local account as a shared service.

The workspace provides German menus, classification and dossier/package
navigators, a metadata editor, and a separate results pane. Use **Öffnen** to
select a saved workspace or import a SIP ZIP.

The browser supports SIP ZIP import and export, GEVER and FILES package
creation, metadata editing, classification objects, notes, content uploads and
downloads, search, validation, statistics, and preservation history. It supports
the four bundled eCH-0160 releases. Unknown releases open read-only.
Use **Änderungen** to review pending field edits and **Speichern** to commit
them. Advanced search, sampling, package actions and PREMIS/METS export are
also available in the browser.
See the [web development and operations guide](docs/web-workspace.md) for
configuration, storage, and testing.

On macOS or Linux, `./start-web.sh 8001` installs the locked dependencies,
builds the Angular app and serves this checkout on port 8001. Open
[Package Turbo](http://127.0.0.1:8001) and select **Ansicht → Änderungen** to
open the pending changes pane. The same pane is available in the lower-right
tab bar. Set the login environment variables above before running the script.

## Engineering references

The generated [eCH-0160 XSD inventory](docs/ech0160-xsd-inventory.csv) shows declarations,
cardinalities, facets, implementation surfaces and linked tests across bundled releases. It
is an engineering inventory, not proof of complete normative eCH or receiving-archive
compliance.

The preservation foundation in `ph_backend.audit` provides an append-only SQLite
journal, RFC 8785 canonical JSON and SHA-256 chaining, trusted checkpoint checks,
and PREMIS 3.0 / METS 2 / METS 1.12.1 companion serializers. Application metadata
edits now require a confirmed operator and are recorded when committed or explicitly
discarded. The Metadata History view survives Save and reopening, independently of
the temporary Changes view. An external `<SIP-name>.package-turbo` companion holds
the journal and interrupted-save recovery state.

Metadata saves detect external/stale-session changes and use durable preparation,
atomic XML replacement and exactly-once completion/recovery. If a save is interrupted,
use Metadata History's recovery action before resuming edits. Application file commands
now operate on a staged copy: Save commits the combined XML/files snapshot, while Discard
leaves the original SIP untouched. Prepared directory replacements recover against
SHA-256 manifests; verified original copies are retained in the companion.

Package processing copies and hashes complete packages, rejects links and junctions,
and uses a configurable workspace storage limit. Large SIPs can take time and storage.

Storage cleanup is previewed and audited. See the [web operations guide](docs/web-workspace.md)
for backup, recovery, storage, and configuration details. Keep an untouched source copy.

## Sample SIP files

Sample SIPs are available from [KOST/CECO](https://kost-ceco.ch/cms/ech-0160-archivische-ablieferungsschnittstelle.html).

## License and Third-Party Notices

Package Turbo's original code is provided under the MIT License; see [LICENSE](LICENSE).
Third-party packages, tools, icons, schemas and sample-material notices are listed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Their terms remain separate from the
project license.
