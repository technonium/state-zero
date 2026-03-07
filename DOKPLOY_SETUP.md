# State Zero Dokploy Setup

This project deploys with a public/private split:

- Public code: this repo
- Private data: mounted at `/opt/state-zero-private`

## 1. Initialize Git Locally

Run these from the public repo folder:

```bash
cd "$HOME/State Zero"
git init -b main
git add .
git commit -m "Initial public State Zero repo"
```

## 2. Create and Push the GitHub Repo

Create a GitHub repo named `state-zero`, then connect it:

```bash
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

## 3. Prepare the Private Folder on the VPS

Create this folder structure on the VPS:

```text
/opt/state-zero-private/
  astrology/
    natal.yaml
    dasha_periods.yaml
  runtime/
    database/
      cards.db
    output/
    state/
      instagram_token_state.json
      instagram_token_health_state.json
      whoop_token_state.json
```

Upload the private files with your VPS file manager, Cyberduck, or FileZilla.
Do not put these files in GitHub.

Create a separate host folder for public Instagram media, for example:

```text
/srv/state-zero-media
```

Your VPS web server must already serve that folder publicly, for example at:

```text
https://yourdomain.com/media
```

## 4. Connect Dokploy to GitHub

In Dokploy:

1. Create a new application from GitHub.
2. Select the `state-zero` repo and the `main` branch.
3. Use the repo `Dockerfile` as the build source.
4. Mount `/opt/state-zero-private` into the container at `/opt/state-zero-private`.
5. Add environment variables from `.env.example`.

Required Dokploy environment values:

- `STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-private`
- `PIPELINE_MEDIA_MODE=live_vps`
- `OPENROUTER_API_KEY`
- `GOOGLE_API_KEY_PRIMARY`
- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- `WHOOP_ACCESS_TOKEN` and `WHOOP_REFRESH_TOKEN` for bootstrap only
- `INSTAGRAM_ACCESS_TOKEN` and `INSTAGRAM_USER_ID` for bootstrap only
- `VPS_PUBLIC_BASE_URL`
- `VPS_SSH_HOST`
- `VPS_SSH_USER`
- `VPS_SSH_PATH`
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if you use Telegram/manual mode

Recommended token settings:

- `INSTAGRAM_AUTO_REFRESH_MODE=hybrid`
- `INSTAGRAM_TOKEN_HEALTHCHECK_ENABLED=true`

## 5. Create the Two Dokploy Cron Jobs

Main pipeline:

```bash
python3 -u src/scripts/pipeline.py
```

Instagram token healthcheck:

```bash
python3 -u src/scripts/instagram_token_healthcheck.py
```

Treat the healthcheck cron as mandatory in production.

## 6. First Deployment Check

Before a real post:

```bash
PIPELINE_MODE=automatic PIPELINE_POST_TO_INSTAGRAM=false python3 src/scripts/pipeline.py
```

Confirm that:

- validation passes
- output is written under `/opt/state-zero-private/runtime/output`
- token state is written under `/opt/state-zero-private/runtime/state`
- media uploads go to your host folder such as `/srv/state-zero-media`
- no runtime data appears inside the repo checkout
