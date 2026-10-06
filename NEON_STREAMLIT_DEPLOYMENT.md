# Move EasyOats to Neon and Streamlit Community Cloud

This procedure keeps the current Render app unchanged while the replacement is prepared on the `Neon_DB_Streamlite_Cloud` branch. The final setup uses Streamlit Community Cloud for the application, Neon PostgreSQL for permanent data, and Google OIDC for sign-in.

The generated Excel workbook and its local backup copies live on Community Cloud's temporary filesystem. They can be recreated from PostgreSQL and downloaded from the app, but they are not durable cloud backups. Neon is the source of truth.

## 1. Create the Neon project

1. Sign in at [Neon](https://console.neon.tech/) and select **New project**.
2. Name the project `EasyOats`.
3. Choose a European region such as Frankfurt when it is available.
4. Keep the default `main` branch, database, and owner role.
5. Select **Connect** in the Neon project.
6. Turn **Connection pooling** off and copy the direct connection URL. This is the temporary `TARGET_DATABASE_URL` used for the one-time migration.
7. Turn **Connection pooling** on and copy the pooled connection URL. This becomes `DATABASE_URL` in Streamlit Community Cloud.

Both URLs are secrets. Do not save them in Git, a committed file, or a chat message. A valid URL normally ends with `sslmode=require` and may also include `channel_binding=require`.

## 2. Copy the Render data into Neon

Perform this step before deploying the Streamlit app so the Neon database is still empty.

1. Tell the team to stop creating or updating orders for the short cutover window.
2. In Render, open the existing PostgreSQL database and copy its **External Database URL**. This is the source URL.
3. In PowerShell, open this project directory and run:

   ```powershell
   git switch Neon_DB_Streamlite_Cloud
   git pull origin Neon_DB_Streamlite_Cloud

   $env:SOURCE_DATABASE_URL = Read-Host "Paste the Render external PostgreSQL URL"
   $env:TARGET_DATABASE_URL = Read-Host "Paste the Neon DIRECT PostgreSQL URL"

   .\.venv\Scripts\python.exe scripts\migrate_postgres_to_postgres.py --dry-run
   .\.venv\Scripts\python.exe scripts\migrate_postgres_to_postgres.py

   Remove-Item Env:SOURCE_DATABASE_URL
   Remove-Item Env:TARGET_DATABASE_URL
   ```

The dry run checks both connections and reports the source record counts without changing either database. The real run creates the Neon schema, reads Render in one consistent transaction, refuses a Neon target that already contains operational activity, copies all application tables, resets PostgreSQL sequences, and verifies every table count.

Keep the Render database and service. They are the rollback copy until the new app has been verified. After the transfer begins, do not enter new production data in the Render app.

## 3. Add the Streamlit callback to Google OAuth

Choose the Streamlit app address before editing Google. A suitable address is:

```text
https://easyoats-order-manager.streamlit.app
```

In Google Cloud Console:

1. Open **APIs & Services → Credentials**.
2. Open the existing EasyOats **Web application** OAuth client.
3. Add this **Authorized redirect URI**, using the exact Streamlit hostname selected during deployment:

   ```text
   https://easyoats-order-manager.streamlit.app/oauth2callback
   ```

4. Keep the Render callback during verification.
5. Save. Keep the existing client ID and client secret ready for Streamlit's secret settings.
6. If the OAuth consent screen is in testing mode, confirm every EasyOats user is included as a test user.

## 4. Deploy the branch to Streamlit Community Cloud

1. Open [Streamlit Community Cloud](https://share.streamlit.io/) and sign in with the GitHub account that owns or administers the repository.
2. Select **Create app** and choose the existing EasyOats repository.
3. Set **Branch** to `Neon_DB_Streamlite_Cloud`.
4. Set **Main file path** to `app.py`.
5. Select the app URL used in the Google callback. If that exact subdomain is unavailable, choose another one and update the Google redirect URI accordingly.
6. Open **Advanced settings**, select Python 3.12, and paste a completed copy of `.streamlit/secrets.example.toml` into the Secrets field.
7. Use the Neon **pooled** URL for `DATABASE_URL`.
8. Generate a new cookie secret locally with:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

9. Put the generated value in `auth.cookie_secret`, enter the existing Google client credentials, and set `BOOTSTRAP_ADMIN_EMAILS` to the administrator email address.
10. Select **Deploy**.

The application runs Alembic automatically once per Streamlit process before connecting the service. Future commits containing a new migration will therefore update Neon during the next deployment.

## 5. Verify the replacement

Sign in with the administrator account and check all of the following before inviting the team:

1. Dashboard totals match Render.
2. Existing customer search, the newest order, payments, inventory, products, feedback, users, and audit history are present.
3. A new test order receives the next expected `EO-` number.
4. A second device can see the new order after refreshing.
5. **الإعدادات والتصدير → Excel والتصدير** creates and downloads a valid workbook.
6. A staff account can sign in but cannot manage accounts or product definitions.
7. After cancelling the test order, its inventory reservation is released.

If verification fails, stop using the Streamlit app and return the team to Render. The migration does not modify the Render database.

## 6. Complete the cutover

1. Give the team the new `streamlit.app` URL and state that Render is read-only from this point.
2. Keep Render for several days while normal orders are verified.
3. Review Neon storage and compute use from its dashboard.
4. Download Excel periodically for an additional operational copy.
5. When the replacement is proven, remove the old Render callback from Google OAuth and cancel the Render web service and database.

After 12 hours without traffic, Community Cloud displays a sleeping page. Any viewer can select **Yes, get this app back up!**, wait for startup, and then use the normal Google sign-in.
