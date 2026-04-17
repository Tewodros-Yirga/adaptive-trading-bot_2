# MT5 Bridge Base Image — Setup Guide

## Why a Pre-Built Base Image?

Running the MT5 bridge on Render previously required:
- Wine prefix initialisation (~1 min)
- Windows Python 3.9 download + install (~8 min)
- pip install MetaTrader5, mt5linux (~5 min)
- MT5 terminal download + install (~10 min)

**Total cold-start time: 25–35 minutes per deploy.**

By pre-baking all of this into a Docker base image, Render pulls the image once and **deploys are ready in ~2 minutes**.

---

## One-Time Setup: Build the Base Image

You only need to do this **once** (and again when you want to update Wine, Python, or the MT5 version).

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed
- GitHub Container Registry access (uses your GitHub credentials)

### Step 1: Find the MT5 setup.exe URL

Download the MetaTrader 5 installer from your broker's website or from MetaQuotes:
```
https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
```

### Step 2: Build the image locally

```bash
# Replace <username> with your GitHub username (lowercase).
IMAGE=ghcr.io/loriloha/mt5-bridge-base:latest

docker build \
  --build-arg MT5_URL="https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" \
  -t $IMAGE \
  ./mt5-bridge-base

# This takes 30–45 minutes. Wine needs to run three installers inside Docker.
```

> **Tip:** You can omit `--build-arg MT5_URL=...` to build a Python-only base image
> (Wine prefix + Python + pip packages, but no MT5 terminal). The terminal will then
> be installed at first Render startup via the `MT5_INSTALLER_URL` env var — still
> much faster than the full cold start.

### Step 3: Log in to GitHub Container Registry (ghcr.io)

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u loriloha --password-stdin
```
*(Create a GitHub personal access token with `write:packages` scope.)*

### Step 4: Push the image

```bash
docker push ghcr.io/loriloha/mt5-bridge-base:latest
```

### Step 5: Make the package public (or add Render's deploy key)

1. Go to **github.com → Your profile → Packages → mt5-bridge-base**
2. Click **Package Settings → Change visibility → Public**

*(Or keep it private and set `GITHUB_TOKEN` in Render's environment.)*

---

## Automated Builds via GitHub Actions

The workflow at `.github/workflows/build-mt5-base.yml` automatically rebuilds
the base image whenever `mt5-bridge-base/Dockerfile` changes.

To trigger a manual rebuild with a specific MT5 URL:
1. GitHub → Actions → **Build & Push MT5 Bridge Base Image**
2. Click **Run workflow**
3. Paste the MT5 setup.exe URL in the input field

To bake the URL into CI permanently, add it as a repository secret:
- **Name:** `MT5_INSTALLER_URL`
- **Value:** `https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe`

---

## Render Configuration

In your Render service, make sure the **Dockerfile path** points to:
```
mt5-bridge-service/Dockerfile
```

The bridge's Dockerfile has:
```dockerfile
ARG BASE_IMAGE=ghcr.io/loriloha/mt5-bridge-base:latest
FROM ${BASE_IMAGE}
```

**No Render changes needed** — it already uses Docker, and will pull the new base image on the next deploy.

---

## Deploy Time Comparison

| Scenario | Cold Start Time |
|----------|----------------|
| Without base image (scottyhardy/docker-wine) | ~25–35 min |
| Base image (Python + mt5linux only, no terminal) | ~2 min startup + MT5 install ~10 min |
| Base image (Python + mt5linux + terminal baked in) | **~2 min (everything ready)** |

---

## Troubleshooting

### Check what's pre-baked in the running container

```bash
curl -H "X-Bridge-Secret: <secret>" https://mt5-bridge-service.onrender.com/debug/mt5
```

Look for:
- `bootstrap.terminal_ready: true` — terminal is installed
- `mt_terminal_exe_discovered` — path bootstrap found
- `adapter.connected: true` — actively connected to MT5

### Force adapter reconnect (without restart)

```bash
curl -X POST -H "X-Bridge-Secret: <secret>" https://mt5-bridge-service.onrender.com/reset
```

### Building failed mid-way?

Docker layer caching means you can re-run the build and it will resume from the last successful layer. The Wine and Python install layers are cached after the first successful run.
