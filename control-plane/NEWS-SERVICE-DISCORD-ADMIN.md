# News Service Discord admin

The News Service uses an administrator password for its Discord settings and manual publication sends. The webhooks stay in the existing Windows Credential Manager entries; they are never returned to the browser. Posting is manual only.

## One-time setup

On the Windows desktop, signed into the same Windows account that runs Ariadne, run:

```powershell
python D:\Downloads\Ariadne\control-plane\news_admin_setup.py
```

The local window creates or replaces an admin password. Use at least 8 characters; no other complexity rules apply. Only a salted PBKDF2 verifier is stored in Windows Credential Manager. The setup window is needed to establish an initial trusted administrator; it does not register or reveal webhook credentials. Do not share your password. Replacing it requires access to that Windows account and explicit confirmation. Admin sessions expire after one hour or when Ariadne restarts; five failed login attempts block logins for 15 minutes.

The updated Python routes require the normal Ariadne restart before the page can use them. Restart only when appropriate for your running workloads.

## Settings and posting

Open https://ariadne.dia.net.au/news-service, log in under Discord Settings, and select Thailand News or AI Noise. Status reads Stored, Missing, or Error. Paste a new URL into the masked field and choose Save, or choose Replace and confirm to update an existing credential. The field is cleared after saving. Test Connection performs a read-only lookup with Discord and sends no message. Select Post to Discord on a publication, review its preview and destination, then select Send and confirm. Previously sent publications require an additional confirmation.

A successful send updates the card after the response and stores the Discord message ID and outcome in the existing delivery history. An uncertain outcome requires checking Discord; do not retry blindly. No automatic polling or sending is enabled. Log out when finished.

The local console workflow is retained as a fallback until the restarted page is manually accepted; it can be retired after that check.

## Add another destination

After logging in, enter the destination name and its webhook URL in Discord Settings, then select **Add Destination**. The URL is saved in Windows Credential Manager; only the destination name and credential reference are stored in the News Service database. The destination appears in the source and publication filters. Existing cached publications retain their previously stored destinations; changing a source does not automatically move older cached publications.
