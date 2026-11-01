# Connecting Telegram CRM for the first time

[Русская версия](SETUP.md)

## What you need

| Item | Where to get it | Where it goes |
| --- | --- | --- |
| **API_ID** (a number) | my.telegram.org → API development tools | Web panel → Connect Telegram → API_ID |
| **API_HASH** (32 characters) | Same page, App api_hash field | Web panel → Connect Telegram → API_HASH |
| Phone number | The number of your Telegram account | Same form, in international format, for example +79991234567 |
| Login code | Arrives the way Telegram chooses: usually the Telegram service chat, sometimes SMS | The next screen, “Login code” |
| Cloud password | Your existing two-step verification password, if enabled | The “Two-step verification” screen |

Reading your personal history requires a user MTProto client. API_ID and API_HASH identify the app; your number, code and password authorize your account. You do not need a bot or a @BotFather token: a bot cannot see your private chats. [Telethon's description of signing in](https://docs.telethon.dev/en/stable/basic/signing-in.html).

## 1. Create API_ID and API_HASH

1. Open [my.telegram.org](https://my.telegram.org) in a browser.
2. Enter your Telegram number with the country code.
3. Confirm with the code Telegram sends for this login. This is a separate login to the site.
4. Open **API development tools**.
5. If you have not created an app yet, fill in **Create new application**:
   - App title: `Telegram CRM`.
   - Short name: `dialoguecrm` (Latin letters without spaces; use another unique value if needed).
   - Platform: `Desktop`.
   - URL: can stay empty.
   - Description: `Personal local chat organizer`.
6. Click **Create application**.
7. Find **App api_id** and **App api_hash**, and keep the page open for copying. If you already have an app, use its existing values.

These steps follow [Telegram's official instructions](https://core.telegram.org/api/obtaining_api_id). The site may look slightly different. Never publish your API_HASH.

## 2. Enter the details in the CRM

1. Click **Telegram CRM** in the Dock. The [local panel](http://127.0.0.1:4180) opens. To use English, click **English** in the sidebar.
2. Click **Connect Telegram** at the bottom left, in the empty panel or on the demo banner. On a narrow screen, use **Refresh chats**.
3. Copy the **App api_id** number into the **API_ID** field.
4. Copy **App api_hash** into the **API_HASH** field.
5. Enter your Telegram number in the format `+79991234567`.
6. Click **Get code** and enter the code in the CRM panel.
7. If two-step verification is on, enter your Telegram cloud password.
8. Once connected, chats start loading. Private chats, groups, channels and the archive appear in the panel.

If the code expires, click **Request again**. On a FloodWait, wait for the stated pause. The CRM does not save the login code or cloud password. API_ID, API_HASH and the session are stored locally, so you normally will not need to log in again. To revoke the session, open Telegram → Settings → Devices and terminate the Dialogue CRM session. Secret chats are not available as regular cloud history to an MTProto client.

## If the code does not arrive: QR login

In the panel, click **Connect Telegram → Log in with QR**. If API_ID and API_HASH are already saved, you do not need to enter them again. On first connection, fill in only API_ID and API_HASH, then click **Log in with QR**; this method does not need a phone number.

On your phone, open **Telegram → Settings → Devices → Link Desktop Device**, scan the QR code in the CRM panel and confirm the login. The QR code refreshes automatically when it expires. If two-step verification is on, enter your cloud password in the CRM next. Scan from the Devices section inside Telegram, not with your regular camera.

For a code login, the panel shows the method returned by the Telegram server: service chat on connected devices, SMS, email or a call. This is the API's answer about the chosen delivery, not proof that the code arrived. When another method is available, **Resend** works, within Telegram's timer. Forced SMS to a third-party client is not guaranteed, so do not send repeated requests. If Telegram requires a login form the app does not support, use QR.

After a server restart, an unfinished code login must be started again. Saved API_ID and API_HASH stay in place. More details: [Telegram authorization](https://core.telegram.org/api/auth), [official QR login](https://core.telegram.org/api/qr-login).

## 3. LLM: optional

Everything else works without an LLM. There are two ways to connect one.

Without an API: select chats → **Copy for LLM** → paste into your LLM → copy the JSON → **Import reply**. This does not need any LLM keys in the CRM.

With an API: create a key in the [OpenAI Platform](https://platform.openai.com/api-keys) or the [Anthropic Console](https://console.anthropic.com/settings/keys), open CRM → **Settings**, paste the key and set a model available to your API account: models named `claude-*` use Anthropic, others use OpenAI. Save. Then select chats → **LLM analysis**. The panel shows an estimate before sending. The API is billed separately from ChatGPT and Claude subscriptions. Automatic LLM analysis of the remaining chats after a scan has its own checkbox.

During analysis, the text of the selected messages goes to the chosen provider. The prompt follows the interface language.

## 4. Dock and managing the service

The app installs to `~/Applications/Telegram CRM.app`. Run the commands below from the project folder. Do not move the project without reinstalling the shortcut: the app contains an absolute launch path.

Closing the browser does not stop the server. To stop it:

```bash
.venv/bin/python scripts/service.py stop
```

The server starts again the next time you click the Dock icon. Autostart at macOS login is not installed.

If you changed the project path, first recreate `.venv` and install the dependencies, then:

```bash
.venv/bin/python scripts/install_macos_app.py
```

If port 4180 is busy, startup details are in `.data/server.log`. The first launch may ask for macOS access to the Documents folder; grant it to this local app if macOS shows the request.

## What to keep secret

The file `.data/telegram.session` gives full access to your Telegram account. Never share it or add it to git, and the same goes for `.data/credentials.json`, which holds API_ID, API_HASH and LLM keys. The whole `.data` folder is already excluded in `.gitignore`.
