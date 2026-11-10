# Package Turbo web workspace

The service is intended for a single trusted operator on a local machine. Bind
it to loopback and run it under an account that can access the archival material.
Local login protects the API, including downloads and API documentation.
Shared hosting still requires per-workspace authorization, HTTPS, quotas
and job isolation. The configured operator is a preservation label, not a
separate account.

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
on logout. The session cookie is HttpOnly and both cookies use SameSite=Strict.
Mutating API calls require `X-XSRF-TOKEN` matching the session's `XSRF-TOKEN`
cookie; Angular sends it automatically. API scripts must log in with JSON,
retain both cookies and send that header for POST/PATCH/DELETE calls.
No passwords or bearer tokens are stored in browser local storage.
Login is limited to 20 attempts per minute per server process.

Use one Uvicorn worker: sessions and the bounded login throttle are in memory.
Restarting (including development reload) invalidates sessions. Session expiry
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
identity and package authorization must be mapped explicitly before enabling
multiple users; the current account uses `PACKAGE_TURBO_OPERATOR` for audit events.

## Using the browser workspace

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
