# New Project Setup Guide

This checklist walks through setting up a new web application from this template. It is designed to be followed either by a human or by Cursor AI reading the steps and guiding the user interactively.

**Goal:** Go from idea to deployable web app in ~15 minutes with proper auth, Cloudflare tunnels, and correct infrastructure (Portainer stacks).

**Approach:** Tools are checked incrementally as each step needs them -- not listed as a wall of prerequisites upfront. If something is missing, the guide helps you install it right then.

---

## Step 1: Project Identity

**Goal:** Establish the project name, display name, and port.

**What to determine:**
- **Project slug** -- URL-safe lowercase name (e.g., `permit-tracker`). Infer from the repo name or directory.
- **Display name** -- Human-readable title (e.g., `Permit Tracker`).
- **Port** -- Default is `5001`. **Check for conflicts first:**
  - Windows: `Get-NetTCPConnection -LocalPort 5001 -ErrorAction SilentlyContinue`
  - macOS/Linux: `lsof -i :5001 2>/dev/null`
  - If the port is in use, try `5002`, `5003`, etc. until you find a free one.

**Action:** Update `project.yaml`:
```yaml
project:
  name: "permit-tracker"
  display_name: "Permit Tracker"
  port: 5001
```

---

## Step 2: Environment Model

**Goal:** Decide whether dev is local-only or a deployed environment.

**Options:**
- **A) Dev + Prod deployed** -- `dev` branch auto-deploys to a dev stack with its own tunnel and URL. `main` deploys to prod.
- **B) Prod only (dev is local)** -- `dev` branch is used for local development on `localhost`. Only `main` triggers deployment.

**What to determine:**
- Production URL (e.g., `https://permit-tracker.rowancountync.io`)
- Dev URL if deploying dev (e.g., `https://permit-tracker-dev.rowancountync.io`)

**Naming convention defaults:**
- Prod: `{slug}.rowancountync.io`
- Dev: `{slug}-dev.rowancountync.io`

**Action:** Update `project.yaml`:
```yaml
environments:
  production:
    url: "https://permit-tracker.rowancountync.io"
    deployed: true
  development:
    url: "https://permit-tracker-dev.rowancountync.io"
    deployed: true    # or false for local-only dev
```

---

## Step 3: Cloudflare Tunnels

**Goal:** Create Cloudflare tunnel(s) for secure public access. This must happen **before** Azure App Registration because the tunnel establishes the public URLs that the auth callback depends on.

**What to create:**
- Production tunnel: routes `{slug}.rowancountync.io` to `http://webapp:{port}`
- Dev tunnel (if deployed dev): routes `{slug}-dev.rowancountync.io` to `http://webapp:{port}`

**Naming convention:**
- Prod tunnel: `{slug}`
- Dev tunnel: `{slug}-dev`

### Option A: Using Cloudflare API (automated)

Requires `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in `.operator.env`.

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/tunnels" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "permit-tracker", "config_src": "cloudflare"}'
```

Then configure the tunnel's public hostname in the Cloudflare Zero Trust dashboard to point to `http://webapp:{port}`.

### Option B: Manual (no API credentials needed)

If you don't have Cloudflare API credentials, that's fine. You just need two things: the **tunnel token** and the **public hostname**.

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) > **Networks** > **Tunnels**
2. Click **Create a tunnel**
3. Choose **Cloudflared** connector
4. Name the tunnel `{slug}` (e.g., `permit-tracker`)
5. **Copy the tunnel token** -- this is the long `eyJ...` string shown during setup
6. Add a public hostname:
   - **Subdomain:** `{slug}` (e.g., `permit-tracker`)
   - **Domain:** `rowancountync.io`
   - **Service:** `http://webapp:{port}`
7. If you have a deployed dev environment, repeat steps 2-6 with the name `{slug}-dev` and subdomain `{slug}-dev`

### Option C: Already have tunnels

If tunnels already exist, just provide:
- **Production tunnel token** and **public URL**
- **Dev tunnel token** and **public URL** (if applicable)

**Output needed for next steps:** Tunnel token(s) and confirmed public URL(s).

---

## Step 4: Azure App Registration (Auth)

**Goal:** Create Azure AD app registration(s) for Microsoft Entra ID authentication.

**Tool check:** Run `az --version` to see if Azure CLI is installed.
- If **not installed:**
  - Windows: `winget install -e --id Microsoft.AzureCLI`
  - macOS: `brew install azure-cli`
  - Manual: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli
- After installing: `az login` and select the **Rowan County Monthly** subscription
- If you can't install `az`, skip to the Manual section below

**Decision:** Should dev and prod share the same app registration, or use separate ones?
- **Shared** -- One app registration with multiple redirect URIs. Simpler.
- **Separate** -- Two app registrations. Dev is fully isolated from prod.

**For each app registration, you need:**
1. Display name (e.g., `Permit Tracker` for prod, `Permit Tracker - Dev` for dev)
2. Redirect URI: uses the URLs confirmed in Step 3 -- `https://{url}/getAToken`
3. Client ID (generated by Azure)
4. Client secret (generated by Azure)

### Automated (Azure CLI)

```bash
# Check login
az account show

# Production app registration
az ad app create \
  --display-name "Permit Tracker" \
  --web-redirect-uris "https://permit-tracker.rowancountync.io/getAToken" \
  --sign-in-audience "AzureADMyOrg"

# Note the appId from output, then create a secret:
az ad app credential reset --id <APP_ID> --display-name "prod-secret"

# If separate dev app:
az ad app create \
  --display-name "Permit Tracker - Dev" \
  --web-redirect-uris "https://permit-tracker-dev.rowancountync.io/getAToken" \
  --sign-in-audience "AzureADMyOrg"
az ad app credential reset --id <DEV_APP_ID> --display-name "dev-secret"
```

### Manual (no Azure CLI)

1. Go to [Azure Portal > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Name: `{Display Name}` (or `{Display Name} - Dev`)
4. Supported account types: **Accounts in this organizational directory only**
5. Redirect URI: Web > `https://{url}/getAToken` (URL from Step 3)
6. After creation, go to **Certificates & secrets** > **New client secret**
7. Copy the **Client ID** (from Overview page) and **Secret value** (from the secret you just created)

**Action:** Update `project.yaml`:
```yaml
auth:
  enabled: true
  shared: true    # or false for separate registrations
```

---

## Step 5: Auth Access Model

**Goal:** Decide who can access the application.

**Options:**
- **A) Specific users only (allowlist -- recommended):** Only email addresses you explicitly add will have access. Everyone else in the org gets denied. This is the safer default -- access is granted intentionally, not by default.
- **B) All org users (opt-in):** Anyone with a `@rowancountync.gov` email in the Rowan County Azure AD tenant can log in. Only use this for tools intended for the entire organization.

Provide the initial list of authorized email addresses (comma-separated). More can be added later by updating the `ALLOWED_USERS` environment variable.

**Action:** Update `project.yaml`:
```yaml
auth:
  mode: "allowlist"      # or "all_org"
  allowed_users: []      # e.g. ["jane.doe@rowancountync.gov", "john.smith@rowancountync.gov"]
```

---

## Step 6: Portainer Stacks

**Goal:** Create Portainer stack(s) for deployment.

**What to create:**
- Production stack pointing at `main` branch
- Dev stack (if deployed dev) pointing at `dev` branch

### Getting a Portainer API Key

If you need an API key, here's how to get one:

1. Log into your Portainer instance
2. Click your **username** (bottom-left corner) or navigate to **My Account**
3. Scroll down to the **Access Tokens** section
4. Click **Add Access Token**
5. Description: `github-actions-prod` (or `github-actions-dev`)
6. Click **Create**
7. **Copy the token immediately** -- it will not be shown again

### Automated (Portainer API)

If you have `.operator.env` with `PORTAINER_PROD_URL` and `PORTAINER_PROD_API_KEY`, stacks can be created via the Portainer API.

### Manual

1. Open your Portainer instance (e.g., `https://portainer.rowancountync.gov`)
2. Go to **Stacks** > **Add stack**
3. Choose **Git repository**
4. Repository URL: `https://github.com/{owner}/{repo}`
5. Reference: `refs/heads/main` (prod) or `refs/heads/dev` (dev)
6. Compose path: `docker-compose.yml`
7. Enable **Authentication** and provide your GitHub username + PAT
8. Add environment variables: leave empty for now (CI/CD will inject them)
9. Click **Deploy the stack**
10. Note the **Stack ID** from the URL (e.g., `/stacks/42` means ID is `42`)
11. Note the **Endpoint ID** from the URL (e.g., `endpointId=2` means ID is `2`)
12. If deployed dev: repeat for the dev Portainer instance pointing at the `dev` branch

**Output needed:** Portainer URL(s), API key(s), stack ID(s), and endpoint ID(s).

---

## Step 7: GitHub Secrets and Variables

**Goal:** Populate GitHub Environment configuration so CI/CD can deploy.

**Key concept:** Only actual secrets go in GitHub Secrets (write-only, invisible after being set). Everything else goes in GitHub Variables (readable, auditable).

**Tool check:** Run `gh --version` to see if GitHub CLI is installed.
- If **not installed:**
  - Windows: `winget install -e --id GitHub.cli`
  - macOS: `brew install gh`
  - Manual: https://cli.github.com/
- Then: `gh auth login`
- If you can't install `gh`, set these manually in the repo's Settings > Environments

### Create environments

```bash
gh api repos/{owner}/{repo}/environments/Production -X PUT
gh api repos/{owner}/{repo}/environments/Development -X PUT   # only if deployed dev
```

### Set Secrets (sensitive values only)

```bash
# Generate a Flask secret key
FLASK_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')

gh secret set FLASK_SECRET_KEY --env Production --body "$FLASK_KEY"
gh secret set AZURE_CLIENT_SECRET --env Production --body "{client_secret}"
gh secret set PORTAINER_API_KEY --env Production --body "{api_key}"
gh secret set CLOUDFLARE_TUNNEL_TOKEN --env Production --body "{tunnel_token}"
gh secret set GH_PAT --env Production --body "{github_pat}"
```

### Set Variables (non-sensitive configuration)

```bash
gh variable set PORTAINER_URL --env Production --body "{portainer_url}"
gh variable set PORTAINER_STACK_ID --env Production --body "{stack_id}"
gh variable set PORTAINER_ENDPOINT_ID --env Production --body "{endpoint_id}"
gh variable set AUTH_ENABLED --env Production --body "true"
gh variable set AUTH_MODE --env Production --body "{allowlist or all_org}"
gh variable set ALLOWED_USERS --env Production --body "{comma-separated emails, if allowlist}"
gh variable set AZURE_CLIENT_ID --env Production --body "{client_id}"
gh variable set AZURE_TENANT_ID --env Production --body "{tenant_id}"
gh variable set REDIRECT_URI --env Production --body "https://{prod-url}/getAToken"
gh variable set POST_LOGOUT_REDIRECT_URI --env Production --body "https://{prod-url}"
gh variable set ALLOWED_TENANT_ID --env Production --body "977b42ab-7737-4552-86e7-b09ed296213d"
gh variable set ALLOWED_EMAIL_DOMAIN --env Production --body "@rowancountync.gov"
```

Repeat for the **Development** environment with dev-specific values (if applicable).

---

## Step 8: Create Dev Branch

**Goal:** Set up the dev branch for the development workflow.

```bash
git checkout -b dev
git push -u origin dev
```

This triggers the GitHub Actions workflow for the `dev` branch. If `Development` environment variables are configured, it will deploy to the dev Portainer stack.

---

## Step 9: First Deploy

**Goal:** Verify the deployment pipeline works.

1. **Push to `dev`** (if deployed dev is configured):
   - Verify GitHub Actions deploys successfully (should complete in under 30 seconds)
   - Verify the dev URL loads and shows the template welcome page

2. **Merge `dev` to `main`:**
   ```bash
   git checkout main
   git merge dev
   git push origin main
   ```
   - Verify GitHub Actions deploys successfully
   - Verify the production URL loads
   - If auth is enabled, verify Microsoft login redirects and grants access

**Troubleshooting:**
- Check GitHub Actions logs for deploy errors
- Check Portainer stack logs for container startup or build errors
- Verify Cloudflare tunnel is **Healthy** in Zero Trust dashboard
- Verify Azure AD redirect URIs match the URLs exactly

---

## Step 10: Build Your App

**Goal:** Replace the template stub with your actual application.

### Template Cleanup

Before adding application logic, clean up the template scaffolding:

1. **Rewrite `README.md`** -- Replace the template README with documentation specific to your project (architecture, deployment, environment variables). Use IT-On-Call's README as a model.
2. **Delete `SETUP.md`** -- Setup is complete; this file is no longer needed.
3. **Delete or reset `CHANGELOG.md`** -- The template's changelog is not relevant to your project. Either delete it or clear it to start your own.
4. **Update `templates/index.html`** -- Replace "Web Application" and "Template starter application" with your project's display name and description.
5. **Commit** these cleanup changes (e.g., "Initialize {project name} from template").

If you're using the Cursor guided setup, the AI will handle this cleanup automatically.

### Add Your Application

1. Add your routes in `app.py` below the `YOUR APPLICATION ROUTES BELOW` marker
2. Add your dependencies to `requirements.txt`
3. Add your templates to `templates/`
4. Add your static assets to `static/`
5. Add app-specific env vars to `docker-compose.yml`, `build-deploy.yml`, and `.env.example` (see dev-guide for details on secrets vs variables)

**Done!** Your app is now set up with the full Rowan County deployment pipeline.

---

## Setup State Tracking

As each step completes, update `project.yaml` > `setup_state`:

```yaml
setup_state:
  cloudflare_tunnels_created: true
  azure_app_created: true
  portainer_stacks_created: true
  github_secrets_populated: true
```

This allows Cursor (and future automation) to know which steps have been completed.
