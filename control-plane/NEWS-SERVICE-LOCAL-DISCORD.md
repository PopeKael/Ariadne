# Local Discord delivery test

This is a manually operated Windows console command. It does not create a web endpoint, activate monitoring, or send anything on startup. Use a normal interactive PowerShell or Windows Terminal, signed in as the Windows account that owns the Ariadne credentials. Python and Node.js must both be on PATH. The News Service browser remains preview-only.

```powershell
python D:\Downloads\Ariadne\control-plane\news_discord_local.py
```

## Register or check credentials

Do not put webhook URLs in a chat, command line, script, environment variable, or browser address bar. Start the command and choose **1 — Register or check webhook** to open the local Windows window. Select **Thailand News** or **AI Noise**, then click **Paste from Clipboard**. The field is masked by default; the window reports whether a URL was received and whether its format is recognised. Select **Show URL** only if you need to inspect it locally. Click **Save Credential** and check for the success message. Each destination is independent; updating a stored credential requires confirmation. **Cancel** closes the window without saving pending input.

The selected destination's status appears in the window and can be refreshed with **Check Stored Status** without revealing the secret. Credentials are saved as separate generic credentials in the current user's Windows Credential Manager. The command ignores environment-variable overrides for these credentials. Correctly mapping each webhook to its Discord destination is the operator's responsibility: format validation does not contact Discord or verify the channel. Existing credentials can be retained; this interface does not rotate webhooks.

## First live test

1. Review the publication in the News Service browser, including its thumbnail and destination.
2. Start the console command above and choose **2**. Select the corresponding publication by number.
3. Check the displayed title, destination, thumbnail URL, original video link and formatted text. The same formatter generates the browser preview and the message payload. Enter nothing at the confirmation prompt to cancel.
4. When ready to send that one item, type `SEND` in the console. If it was previously sent, the command first requires the separate word `RESEND`.
5. Check the displayed outcome and Discord message ID, then inspect the correct Discord channel. Choose **3** in the command to view recorded attempts, HTTP status, Discord channel ID and message ID. Publication cards show the stored delivery status after a browser refresh.

The command posts directly to Discord over HTTPS, using `wait=true` and `with_components=true`, never follows redirects, and has no arbitrary destination argument. It records a start before the request and a sanitized response outcome, HTTP status, Discord message ID, channel ID and timestamp afterward. It does not store webhook URLs in the News Service database. A 429 blocks subsequent local attempts until its retry interval expires; there is no automatic retry. The cooldown is local to this database and does not coordinate with other Discord clients.

If an outcome is **uncertain** or remains **inflight**, inspect Discord and do not retry blindly. The tool intentionally blocks subsequent attempts for that publication until an explicit reconciliation mechanism is implemented. A failure to persist the response after Discord accepted a message can leave the attempt in this state. The command does not make web-based sending available or alter existing n8n workflows.
