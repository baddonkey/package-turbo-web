# Package Turbo web workspace

The service is intended for a single trusted operator on a local machine. Bind
it to loopback and run it under an account that can access the archival material.
Local login protects the API, including downloads and API documentation.
The multi-user foundation now enforces per-package ownership and sharing,
but shared hosting still requires Keycloak login, HTTPS, quotas and job isolation.
Do not expose the local development login as a shared service.

## Development

Run the API and Angular development server in separate terminals from the repository
root. Angular proxies `/api` to the loopback API, so browser requests use one origin.

```sh
uv sync --locked --extra web --extra web-test
# Generate a hash interactively; the password is not echoed or stored.
uv run --locked --extra web python -m ph_web.auth
export PACKAGE_TURBO_LOGIN_USERNAME="archivist"
export PACKAGE_TURBO_LOGIN_PASSWORD_HASH='paste-generated-hash-here'
export PACKAGE_TURBO_OPERATOR="Your name"
uv run --locked --extra web --extra web-test uvicorn ph_web.app:app --reload
```

```sh
npm ci --prefix web
npm start --prefix web -- --host 127.0.0.1
```

Open [the development workspace](http://127.0.0.1:4200). For a single-process
local run using a production browser build, follow the main README.

## Login and future OIDC

Sign in with the configured local username and password. There are no built-in
credentials, self-registration or password recovery. Missing credentials leave
the workspace locked and login returns HTTP 503; partial or invalid configuration
is rejected explicitly. Generate a new hash and restart to change the password.
Keep the hash in a private environment file or secret manager, not source control.
`./start-web.sh` loads `~/.config/package-turbo-web/login.env` if neither login
environment variable is already set. This private shell file should export
`PACKAGE_TURBO_LOGIN_USERNAME` and `PACKAGE_TURBO_LOGIN_PASSWORD_HASH`, use
permissions `600`, and live in a directory with permissions `700`. For a direct
Uvicorn launch, source it first with
`. "$HOME/.config/package-turbo-web/login.env"`.

Sessions use random opaque cookies, expire after eight hours, and are revoked
on logout. Only token hashes are stored in the service SQLite database; sessions
survive application restarts and reloads. Disabled local service identities cannot
use their existing sessions. The session cookie is HttpOnly and both cookies use SameSite=Strict.
Mutating API calls require `X-XSRF-TOKEN` matching the session's `XSRF-TOKEN`
cookie; Angular sends it automatically. API scripts must log in with JSON,
retain both cookies and send that header for POST/PATCH/DELETE calls.
No passwords or bearer tokens are stored in browser local storage.
Login is limited to 20 attempts per minute per server process.

Use one Uvicorn worker for now: the bounded local login throttle is still
process-local, and heavy work still runs within requests. Session expiry
returns the browser to login and unsaved edits must be re-entered. **Abmelden**
asks for confirmation if edits are pending. Saved packages remain on the server.
`/api/v1/health` and the static login page remain public.

For any non-loopback deployment, use HTTPS and set
`PACKAGE_TURBO_COOKIE_SECURE=true`. Configure trusted proxy headers only for a
trusted reverse proxy so the app sees the correct public scheme and host.
Do not expose this single-operator workspace as a multi-user service.

The browser uses a dedicated authentication service and login shell.
`ph_web.auth` owns credential verification, sessions and the current identity
(`subject`, `display_name`) separately from package handling. OIDC/Keycloak
is **not connected yet**. A future integration should replace local credential
verification with authorization-code flow and PKCE, validate issuer/audience
and state/nonce, and retain server-side sessions and CSRF protection. Audit
identity must be mapped explicitly before enabling multiple users. Local identities
use issuer `urn:package-turbo:local` and the configured login username as subject.
`user_id` in the session response is the stable service identity UUID. Preservation
events use this UUID as their agent identifier and web-edit details retain a
display-name/issuer/subject snapshot. Notes and reports use the display name.
`PACKAGE_TURBO_OPERATOR` supplies that display name for local login, not ownership.
Recovery uses a service identity while preserving the prepared transaction's original actor.

## Ownership and sharing foundation

The private data directory now contains `service.sqlite3`, a versioned SQLite
catalog using WAL and foreign keys, plus `catalog.lock`. It stores identities,
hashed sessions, ownership, viewer/editor grants and sharing security events.
It is separate from preservation journals and is not included in SIP exports.

New and imported packages belong to the authenticated user. Package lists are
filtered before pagination and do not open unrelated packages. Every package
endpoint checks permissions before opening, recovery, uploads or export allocation,
then rechecks under the existing package lock. Inaccessible IDs return 404;
insufficient rights on an accessible package return 403. Different packages retain
independent locks. Concurrent edits still require `If-Match` and return 412 on conflict.

- Owners edit and manage sharing and retention cleanup.
- Editors edit but cannot share or clean preservation history.
- Viewers read, search, validate and export saved state, but cannot edit.

Sharing is currently an API feature; there is no sharing UI or global user directory.
The recipient must already have a known service identity. Owners read
`GET /api/v1/packages/{id}/sharing` and update it with
`PUT /api/v1/packages/{id}/sharing`, supplying JSON
`{"user_id":"recipient-uuid","role":"viewer","version":1}`.
Use `editor` to permit edits or `null` to revoke. The version is the separate ACL
version from the GET response; stale changes return 412. The PUT requires CSRF.
Revocation blocks future access, not bytes already downloaded or streaming.

Publication records ownership as pending before the filesystem rename and ready
after it. Startup reconciles registered pending publications under the catalog lock.
Unregistered UUID folders remain inaccessible and are never assigned to the first
user who logs in. Missing/invalid pending workspaces require review; unexplained
directories and preservation companions are not deleted automatically.

## Migrating existing workspaces

Stop all API processes and back up the entire data directory, preservation companions
and trusted checkpoints before migration. Restore to the same absolute paths.
Explicitly select package UUIDs and verify the owner's issuer/subject with your
identity administrator. For the existing local login, use
`urn:package-turbo:local` and its configured username. For future Keycloak access,
use the exact realm issuer and immutable Keycloak subject, not an email address.

```sh
uv run --locked --extra web python -m tools.migrate_web_workspaces \
  --data-dir /absolute/path/to/workspaces \
  --package-id 00000000-0000-0000-0000-000000000001 \
  --issuer urn:package-turbo:local --subject archivist \
  --display-name "Your name"
```

The default is a read-only dry run. Review the output, then repeat with `--apply`.
Repeat `--package-id` to select several packages. Existing catalog ownership cannot
be overwritten. Apply changes only catalog ownership and revokes all service sessions;
SIP and preservation journal bytes and paths remain unchanged. If interrupted,
let startup reconcile pending records and review which IDs remain unregistered
before retrying. Assignments across several packages are not one atomic batch.
Keep the original backup until migration and journal verification are complete.

To revoke all sessions offline after a credential change or restore, stop the
service and run:

```sh
uv run --locked --extra web python -c \
  'from pathlib import Path; from ph_web.database import ServiceDatabase; ServiceDatabase(Path("/absolute/path/to/workspaces/service.sqlite3")).revoke_all_sessions()'
```

Remaining shared-service work: Keycloak authorization-code/PKCE integration and
account-deactivation policy, bounded durable jobs, quotas and free-space reservations,
role-aware sharing/progress UI, and supervised HTTPS deployment with capacity testing.

## Using the browser workspace

Right-click navigator items, metadata section headings, result rows or table
headings to open their context menus. Navigator items also have a menu button
on hover/focus (always visible on touch devices). Shift+F10 or the Context Menu
key opens the menu for a focused item; arrow keys navigate and Escape closes it.
Menus stay within the viewport and close on outside clicks, scrolling or a new
operation. Browser-native text menus remain available in inputs and textareas.

The menus follow the desktop application's classification, dossier, package,
note, validation and metadata-section menus. Table headings offer checkable
column visibility and table copying; search, sample, changes and history rows
also expose navigation/copying and their relevant commands. Read-only packages
and viewers cannot use mutation commands; pending metadata edits block immediate
package mutations. The server remains authoritative for every command.

Web adaptations: opening a content file downloads it, cut/paste moves one object
within the current package revision, and file/folder selections are sent as bounded
ZIP imports rather than exposing server filesystem paths. Bulk object creation is
limited to 100 children per request. Import folders merge; existing files are skipped
unless replacing equal names is explicitly selected and confirmed. Case/type name
collisions are rejected. Batch replacement uses same-name files in the selected
folder, as in the desktop implementation, and retains the existing audited staged
commit/rollback workflow. The server's upload and expansion limits still apply.

The workspace uses German labels and a four-pane layout.
The classification tree stays visible above the Dossiers and Paketinhalt tabs.
The right side shows metadata above validation, notes, search, and history.
Click an editable metadata value, change it, then leave the field to stage it.
Press Escape to discard the current field edit. The **Änderungen** tab lists
pending values and supports removing
individual changes, and offers clipboard and CSV export. **Speichern** commits
the staged fields together. The **Metadaten-Historie** tab shows the preserved
audit events. Pending field edits stay in the current browser tab and are lost
if the user leaves without saving. Structure, file, note, and specialized
metadata actions commit individually. Search, validation and exports read the
saved package.
Use **Paket → Paket zippen…** to export the committed SIP.

## Architecture and data

- `ph_backend` implements SIP loading, XML edits, validation, and preservation.
  `ph_web` owns HTTP contracts, input limits, and the private workspace store.
  The `web` extra contains the API server dependencies.
- The API uses `/api/v1`. Pydantic models generate OpenAPI and the Angular
  client uses TypeScript types generated from that contract. The browser sends
  ZIP and content file bytes as `application/zip` and `application/octet-stream`
  respectively. Upload names are query parameters.
- The service imports a private copy of each SIP into a UUID workspace. ZIPs
  may contain a wrapping folder or contain `header/metadata.xml` at the root.
  Imports preserve packaged XSDs and content. The web editor and validator
  use bundled schemas. Validation can perform metadata checks or include file
  checks such as fixity.
- Package and node reads return a SHA-256 metadata revision. Mutations require
  its quoted value in `If-Match`. A stale revision returns HTTP 412. Node IDs
  are positional and should be resolved again after a revision changes.
- A per-package filesystem lock coordinates requests. Metadata changes use
  the existing recoverable SQLite preservation journal. Content uploads use
  staged package commits and verified rollback copies. Uploading to an eligible archival object also creates the schema-appropriate
  reference. The service checks for interrupted commits when reopening a SIP.
- ZIP downloads contain the committed SIP. Preserve the adjacent
  `<SIP-name>.package-turbo` companion and trusted checkpoints separately when
  backing up the service. Checkpoints are tied to the journal's absolute path,
  so back up and restore to the same path. A SIP ZIP contains the committed
  package; preservation history stays with the server workspace.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `PACKAGE_TURBO_DATA_DIR` | `.package-turbo-web` | Private local directory for workspaces, staging and exports |
| `PACKAGE_TURBO_OPERATOR` | `local operator` | Set the confirmed operator's name before editing |
| `PACKAGE_TURBO_LOGIN_USERNAME` | Unset | Single local login account; required for access |
| `PACKAGE_TURBO_LOGIN_PASSWORD_HASH` | Unset | Hash generated by `python -m ph_web.auth`; required for access |
| `PACKAGE_TURBO_COOKIE_SECURE` | `false` | Set `true` for HTTPS; `false` is for loopback HTTP development only |
| `PACKAGE_TURBO_STATIC_DIR` | Unset | Angular production build directory served from `/` |
| `PACKAGE_TURBO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed HTTP hostnames |
| `PACKAGE_TURBO_MAX_UPLOAD_BYTES` | 268435456 | Maximum compressed ZIP or individual content upload |
| `PACKAGE_TURBO_MAX_EXPANDED_BYTES` | 1073741824 | Maximum expanded ZIP size |
| `PACKAGE_TURBO_TRUSTED_STATE_DIR` | OS user state directory | Parent of the preservation checkpoint directory |

Use a private local filesystem with space for staged and rollback copies.
Back up `service.sqlite3` together with package directories, companions and trusted
checkpoints while all writers are stopped; preserve paths and revoke restored sessions.
Imports also cap ZIP entry count at 10,000 and depth at 40. Full validation,
content commits, and ZIP exports run within the request. Interrupted staging
that cannot be verified requires manual review. Workspaces and retained copies
accumulate on disk, so back them up and manage their lifecycle outside the web UI.

## Verification and contract changes

```sh
uv run --locked --extra web --extra web-test python -m pytest -q tests/test_web_api.py tests/test_web_auth.py
uv run --locked --extra web python tools/export_web_openapi.py
npm run generate:api --prefix web
npm test --prefix web -- --watch=false
npm run build --prefix web
```

Commit both `web/openapi.json` and `web/src/app/api.types.ts` after an API contract
change. For the full Python suite, install the `dev`, `web`, and `web-test`
extras and run pytest. CI also provisions
and verifies the official KOST fixture before running the suite.

## Browser workflows

The browser supports SIP ZIP import and export, metadata editing, package
structure and file operations, validation, search, sampling, notes, reports,
and preservation history. Remaining work includes package-wide undo/redo,
resumable drafts, directory and multi-file import/replacement, DROID
identification, task progress/cancellation, saved perspectives, and French
localization.
