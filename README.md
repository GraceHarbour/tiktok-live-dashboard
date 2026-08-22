# TikTok LIVE Creator Dashboard

A private Streamlit dashboard for manager performance and creator-level TikTok LIVE data.

## What admins can do

- View creators grouped under each manager
- Track diamonds, valid LIVE days, valid LIVE hours, and tier status
- Filter and download dashboard data
- Refresh the shared database by uploading a Backstage Creator data Excel export

The GitHub deployment package contains no creator records, local database, TikTok login, or Supabase password.

## Deploy on Streamlit Community Cloud

1. Upload these files to the root of your private GitHub repository.
2. In Streamlit Community Cloud, create an app from the repository and choose `app.py` as the main file.
3. Open **Advanced settings → Secrets** and enter:

   ```toml
   DATABASE_URL = "your Supabase Session pooler connection string"
   VIEWER_PASSWORD = "a strong password every approved viewer must enter"
   ADMIN_PASSWORD = "a password only dashboard admins know"
   ```

4. If the Supabase connection string begins with `postgresql://`, leave it as-is; the app selects the correct driver automatically.
5. Deploy the app and invite approved viewers. Do not make a creator-data dashboard public.

Never put any secret in GitHub. `VIEWER_PASSWORD` protects all dashboard data; `ADMIN_PASSWORD` separately protects data updates. Use different strong passwords. In Supabase, use the **Session pooler** connection string shown under **Connect**. Replace the password placeholder with the database password you created. If that password contains characters such as `@`, `:`, `/`, `#`, or `%`, URL-encode it or reset it to a strong password without URL-reserved characters.

## First data update

1. In TikTok LIVE Backstage, go to **Data → Creator data**.
2. Export the creator data as an Excel file.
3. Open the deployed dashboard and select **Update data**.
4. Enter the admin password, upload the Excel export, review the preview, confirm, and select **Update shared dashboard**.

The update replaces the creator snapshot so removed or reassigned creators do not remain in old manager lists. Manager goals already stored in the database are preserved when the same manager appears in the new export.

## Local use

Install Python 3.11 or newer, install `requirements.txt`, add `DATABASE_URL` to a local `.env`, then run:

```text
streamlit run app.py
```
