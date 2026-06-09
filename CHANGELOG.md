# Changelog

All versions use [CalVer](https://calver.org/) in `YYYY.0M.DD` format. If multiple releases happen on the same day, a patch number is appended (e.g., `2026.03.18.1`).

## 2026.03.23 — Hybrid Deployment & Auth Lockdown

Eliminates GHCR image builds from CI/CD, adopts lightweight Portainer-only deployment, adds CalVer versioning, and changes the default auth model to allowlist for safer access control.

### Deployment Model Overhaul

#### No more GHCR image builds
The GitHub Actions workflow no longer builds Docker images or pushes to GitHub Container Registry. This eliminates wasted Actions minutes and org storage consumption. The workflow now only triggers a Portainer git-based redeploy via a single API call, typically completing in under 30 seconds.

#### Portainer builds locally
Portainer pulls the repository and builds images on the Docker host using `docker compose build`. This was already happening before (the GHCR image was an unused artifact), but now it's the only path -- no ambiguity.

#### CalVer versioning
Each deploy is tagged with a calendar version (`APP_VERSION = YYYY.MM.DD.BUILD`, e.g., `2026.03.23.42`). This version is used as the Docker image tag in `docker-compose.yml` to force Portainer to rebuild images on every deploy, even when the Dockerfile hasn't changed. The `pullImage` flag in the Portainer payload is set to `false` since images are built locally, not pulled from a registry.

### Auth Default Changed to Allowlist

The default `AUTH_MODE` is now `allowlist` instead of `all_org`. New projects start locked down -- no one has access until the developer explicitly adds email addresses to `ALLOWED_USERS`. This is especially important for dev environments where you don't want the whole org stumbling into a half-built app. Developers can still opt into `all_org` for organization-wide tools.

### Template Cleanup Step

Step 10 (Build Your App) now includes a template cleanup phase:
- Rewrite `README.md` with project-specific content
- Delete `SETUP.md` (no longer needed after setup)
- Delete or reset `CHANGELOG.md` for the project's own history
- Update `templates/index.html` with the project display name
- Commit as "Initialize {project name} from template"

### Changes by File

#### `.github/workflows/build-deploy.yml`
- Removed GHCR login step (`docker/login-action`)
- Removed Docker build-push step (`docker/build-push-action`)
- Removed image tag/repo step
- Added CalVer metadata step (`CALVER = YYYY.MM.DD.BUILD`)
- Added `APP_VERSION` to the Portainer env payload
- Changed `pullImage: true` to `pullImage: false`
- Renamed workflow from "Build and Deploy" to "Deploy"
- Renamed job from `build-and-deploy` to `deploy`
- Added header comment block explaining the hybrid deployment model
- Updated summary step (no GHCR image reference)

#### `docker-compose.yml`
- Added `image: webapp:${APP_VERSION:-dev}` to force rebuild on each deploy
- Added `APP_VERSION` to the environment section
- Changed `AUTH_MODE` default from `all_org` to `allowlist`

#### `app.py`
- Changed `AUTH_MODE` default from `all_org` to `allowlist`

#### `.env.example`
- Changed `AUTH_MODE` default from `all_org` to `allowlist`
- Reordered auth mode options (allowlist first as recommended)

#### `project.yaml`
- Bumped `_template_version` to `2026.03.23`
- Updated `auth.mode` comment to show `allowlist` as recommended

#### `README.md`
- Removed all GHCR/container-registry references
- Updated architecture diagram (no registry, lightweight Actions)
- Added CalVer and `APP_VERSION` documentation
- Changed `AUTH_MODE` default to `allowlist` in all tables
- Added note about template cleanup after setup
- Added note about Actions runtime being under 30 seconds

#### `SETUP.md`
- Step 5: flipped auth default (allowlist recommended, all_org opt-in)
- Step 9: replaced "build success" with "deploy success"
- Step 10: added template cleanup sub-step

#### `.cursor/rules/setup-guide.mdc`
- Step 5: flipped auth default with confirmation prompt for all_org
- Step 9: updated deploy language
- Step 10: added template cleanup behavior (README rewrite, SETUP.md deletion, CHANGELOG reset, index.html update)

#### `.cursor/rules/dev-guide.mdc`
- Updated CI/CD flow description (no GHCR, Portainer builds locally)
- Changed `AUTH_MODE` default to `allowlist` in auth config table
- Replaced GHCR artifact line with CalVer explanation
- Reordered authorization mode descriptions (allowlist first)

---

## 2026.03.18 — Setup Flow Overhaul

Major overhaul of the guided setup experience and CI/CD configuration. Fixes critical logic flaws found during real-world usage of the template.

### Critical Fixes

#### Cloudflare now comes before Azure Auth (step order corrected)
The original flow created the Azure App Registration (Step 3) before the Cloudflare tunnel (Step 4). This is backwards — the Azure app registration requires the callback URL (`https://{slug}.rowancountync.io/getAToken`), which depends on the Cloudflare tunnel being established first. If the URL changed during tunnel setup, you had to go back and fix the app registration. The new order is:

1. Project Identity (name, slug, port)
2. Environment Model (deployed dev or local-only)
3. **Cloudflare Tunnels** (establishes public URLs)
4. **Azure App Registration** (uses confirmed URLs for callback)
5. Auth Access Model
6. Portainer Stacks
7. GitHub Config
8. Dev Branch
9. First Deploy
10. Build Your App

#### GitHub Secrets vs Variables separation
Previously, all CI/CD values were stored as GitHub Secrets (write-only, invisible after being set). This made debugging deployments difficult since you couldn't verify what values were configured. Now only true secrets use `secrets.*`, and configuration values use `vars.*` (readable, auditable).

**Secrets** (sensitive, write-only): `FLASK_SECRET_KEY`, `AZURE_CLIENT_SECRET`, `PORTAINER_API_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, `GH_PAT`

**Variables** (configuration, readable): `PORTAINER_URL`, `PORTAINER_STACK_ID`, `PORTAINER_ENDPOINT_ID`, `AUTH_ENABLED`, `AUTH_MODE`, `ALLOWED_USERS`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `REDIRECT_URI`, `POST_LOGOUT_REDIRECT_URI`, `ALLOWED_TENANT_ID`, `ALLOWED_EMAIL_DOMAIN`

#### Port conflict detection
Step 1 now checks whether port 5001 is already in use before suggesting it as the default. On Windows this uses `Get-NetTCPConnection`, on macOS/Linux `lsof`. If there's a conflict, the setup suggests the next available port (5002, 5003, etc.). This prevents the situation where you complete the entire setup, then discover the port is taken and have to go back and adjust the Cloudflare tunnel configuration.

### New Features

#### Auth access model choice (all_org vs allowlist)
Previously, authentication automatically allowed every `@rowancountync.gov` user in the Rowan County Azure AD tenant. There was no way to restrict access to specific people. Now the setup asks:

- **All org users** (`AUTH_MODE=all_org`): Anyone in the org with the right email domain can log in. Good for organization-wide internal tools.
- **Specific users only** (`AUTH_MODE=allowlist`): Only email addresses listed in `ALLOWED_USERS` can log in. Good for department-specific or sensitive applications.

The allowlist is configured via a comma-separated `ALLOWED_USERS` environment variable and can be updated at any time without redeploying.

#### Incremental tool detection
The old setup listed all prerequisites as a wall of checkboxes at the top. The new setup checks for each tool (`az`, `gh`, etc.) right when it's needed, and offers to help install it:
- Azure CLI missing? Offers `winget install`, `brew install`, or direct download link
- GitHub CLI missing? Same pattern
- Can't install right now? Provides manual portal/dashboard instructions as fallback

#### Cloudflare manual path
If someone doesn't have Cloudflare API credentials, the setup no longer just points them at the Zero Trust dashboard and wishes them luck. It offers three clear paths:
- **API** (if credentials available): automated tunnel creation
- **Manual walkthrough**: step-by-step instructions for creating a tunnel in the dashboard
- **Already have tunnels**: just asks for the token and hostname directly

#### Portainer API key walkthrough
The old guide said "Get API keys from Portainer" with no further explanation. Now it walks through the exact UI path: log in → click username → My Account → Access Tokens → Add Access Token → copy immediately (it won't be shown again).

### Changes by File

#### `app.py`
- Added `AUTH_MODE` environment variable reading (default: `all_org`)
- Added `ALLOWED_USERS` environment variable reading (comma-separated emails, parsed to lowercase list)
- Rewrote `is_user_authorized()` to branch on `AUTH_MODE`:
  - `all_org`: checks tenant ID + email domain (original behavior)
  - `allowlist`: checks tenant ID + whether email is in the explicit `ALLOWED_USERS` list
  - Empty allowlist in `allowlist` mode safely denies everyone (with a warning log)

#### `auth.py`
- Changed default MSAL scopes from `["User.Read", "GroupMember.Read.All"]` to `["User.Read"]`
- `GroupMember.Read.All` requires Azure AD admin consent and was unnecessary for most apps

#### `.github/workflows/build-deploy.yml`
- Moved 11 non-sensitive config values from `secrets.*` to `vars.*`
- Added `AUTH_MODE` and `ALLOWED_USERS` to the deploy env block and Portainer jq payload
- Added clear section comments separating Configuration (Variables) from Secrets

#### `docker-compose.yml`
- Added `AUTH_MODE` and `ALLOWED_USERS` environment variables to the webapp service

#### `project.yaml`
- Added `auth.mode` field (`all_org` or `allowlist`)
- Added `auth.allowed_users` field (list of email addresses)

#### `.env.example`
- Reorganized into clearly labeled Secrets vs Configuration sections
- Added `AUTH_MODE` and `ALLOWED_USERS` with documentation
- Clarified which values are sensitive (go in GitHub Secrets) vs non-sensitive (go in GitHub Variables)

#### `SETUP.md`
- Complete rewrite matching the corrected 10-step flow
- Cloudflare is now Step 3 (before Auth at Step 4)
- Added port conflict check in Step 1
- Added auth access model decision in Step 5
- Added three Cloudflare paths: API, manual walkthrough, and "already have tunnels"
- Added Portainer API key walkthrough with exact UI navigation
- Split "GitHub Secrets" section into "GitHub Secrets and Variables" with concrete `gh secret set` and `gh variable set` commands

#### `README.md`
- Updated "What You Get" to describe both auth access models
- Added 10-step setup flow overview
- Split single "Required Secrets" table into separate Secrets and Variables tables
- Added `AUTH_MODE` and `ALLOWED_USERS` to the environment variables table
- Updated secret handling table to show the secrets/variables distinction

#### `.cursor/rules/setup-guide.mdc`
- Complete rewrite with corrected step order
- Added incremental tool detection instructions for each step
- Added port conflict check before defaulting to 5001
- Added Cloudflare manual path (just ask for token + hostname)
- Added Portainer API key walkthrough
- Added auth access model choice
- Added GitHub secrets vs variables separation with specific commands

#### `.cursor/rules/dev-guide.mdc`
- Updated authorization description for the new `AUTH_MODE` behavior
- Split "New environment variables" guidance into Secrets (`gh secret set`) vs Variables (`gh variable set`)
- Added auth configuration reference table with all auth-related env vars

#### `.operator.env.example`
- Marked all sections as OPTIONAL with explanation that manual alternatives exist
- Added inline Portainer API key instructions (exact UI path)
- Clarified that Cloudflare credentials aren't needed if you provide the token directly

---

## 2026.03.16 — Initial Template

Initial release of the Rowan County web app template.

- Flask application shell with Gunicorn
- Azure AD (Entra ID) authentication via MSAL
- Cloudflare tunnel sidecar for secure public access
- Portainer git-based deployment via CI/CD
- GitHub Actions workflow: build Docker image, push to GHCR, redeploy Portainer stack
- Branch model: `dev` for development, `main` for production
- Cursor AI guided setup rules
- Starter templates, health check endpoint, Docker Compose stack
