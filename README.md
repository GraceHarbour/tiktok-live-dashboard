# TikTok LIVE Creator Dashboard

A private Streamlit dashboard for manager performance and creator-level TikTok LIVE data.

## What admins can do

- View creators grouped under each manager
- Track diamonds, valid LIVE days, valid LIVE hours, and tier status
- Filter and download dashboard data
- Automatically refresh the shared database from Backstage every 30 minutes
- Request an immediate refresh from the dashboard
- Upload a Backstage Creator data Excel export as a manual fallback

The GitHub deployment package contains no creator records, local database, TikTok login, or Supabase password.

## Deploy on Streamlit Community Cloud

1. Upload these files to the root of your private GitHub repository.
2. In Streamlit Community Cloud, create an app from the repository and choose `app.py` as the main file.
3. Open **Advanced settings → Secrets** and enter:

   ```toml
   DATABASE_URL = "your Supabase Session pooler connection string"
   VIEWER_PASSWORD = "a strong password every approved viewer must enter"
   ADMIN_PASSWORD = "a password only dashboard admins know"
   GITHUB_REPOSITORY = "GraceHarbour/tiktok-live-dashboard"
   GITHUB_WORKFLOW_TOKEN = "a fine-grained GitHub token for the Refresh Now button"
   ```

4. If the Supabase connection string begins with `postgresql://`, leave it as-is; the app selects the correct driver automatically.
5. Deploy the app and invite approved viewers. Do not make a creator-data dashboard public.

Never put any secret in GitHub. `VIEWER_PASSWORD` protects all dashboard data; `ADMIN_PASSWORD` separately protects data updates. Use different strong passwords. In Supabase, use the **Session pooler** connection string shown under **Connect**. Replace the password placeholder with the database password you created. If that password contains characters such as `@`, `:`, `/`, `#`, or `%`, URL-encode it or reset it to a strong password without URL-reserved characters.

## Automatic updates every 30 minutes

The included GitHub Actions workflow runs `backstage_sync.py` every 30 minutes. It uses a saved TikTok Backstage browser session; it does not store a TikTok password in GitHub.

1. On the laptop, install the browser once:

   ```text
   python -m playwright install chromium
   ```

2. Run `python capture_backstage_session.py`, sign in to TikTok LIVE Backstage in the browser window, and press Enter in the terminal.
3. Convert the resulting local file to base64 in PowerShell:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("tiktok-storage-state.json"))
   ```

4. In GitHub, open **Settings → Secrets and variables → Actions** and add repository secrets:
   - `DATABASE_URL`: the working Supabase Session pooler connection string
   - `TIKTOK_STORAGE_STATE_B64`: the base64 text from step 3
   - `BACKSTAGE_CREATOR_DATA_URL`: optional; omit it to use the current month automatically
5. Open **Actions → Refresh Backstage data → Run workflow** for the first update. Future runs are scheduled every 30 minutes.

The local `tiktok-storage-state.json` file is excluded by `.gitignore`. Never commit or share it. TikTok may eventually expire the session or require verification; if a run reports an expired session, repeat steps 2–4.

For the dashboard’s **Request fresh Backstage data now** button, create a fine-grained GitHub token limited to this repository with Actions write permission, then store it only in Streamlit Secrets as `GITHUB_WORKFLOW_TOKEN`. The schedule works without this token; only the button needs it.

## Manual fallback update

1. In TikTok LIVE Backstage, go to **Data → Creator data**.
2. Export the creator data as an Excel file.
3. Open the deployed dashboard and select **Refresh data**.
4. Enter the admin password, upload the Excel export, review the preview, confirm, and select **Update shared dashboard**.

The update replaces the creator snapshot so removed or reassigned creators do not remain in old manager lists. Manager goals already stored in the database are preserved when the same manager appears in the new export.

## Local use

Install Python 3.11 or newer, install `requirements.txt`, add `DATABASE_URL` to a local `.env`, then run:

```text
streamlit run app.py
```
