# Dialogues: Telegram CRM

[Русская версия](README.md)

A local app for sorting out your Telegram, built on Telethon 1.45 / MTProto, with a web panel in English and Russian. Switch the language with **English / Русский** in the sidebar.

Setup, including creating API_ID and API_HASH, is in [SETUP.en.md](SETUP.en.md).

## Quick start

Click **Telegram CRM** in the Dock. The app starts the server and opens the panel at http://127.0.0.1:4180. Running it again reuses the service that is already running.

From a terminal:

```bash
.venv/bin/python -m telegram_crm
```

If there is no environment yet, you need Python 3.11+ and:

```bash
uv venv --python 3.12
uv pip install -e '.[test]'
```

## What the app does

| Category | Default rule |
| --- | --- |
| Important | Two-way chat: at least 20 messages in the last 30 days, with at least 2 incoming and 2 outgoing; also pins, manual marks and confident LLM recommendations |
| Jobs | Words about vacancies, interviews and hiring in messages or the chat title; groups, channels and bots need at least 3 such messages, making up at least 10% of those loaded. You can confirm or cancel this by hand |
| Stalled acquaintances | More than 21 days since the last message or manually set progress; the stage “Met” and every later stage are excluded |
| Suspicious | Telegram's SCAM/FAKE label, incoming requests for codes or passwords, risky prepayments, fake Telegram domains, LLM recommendations |
| Inactive | More than 90 days without messages |
| Very old | Last message before the 5-calendar-month boundary |
| Dead | Last message before the 2-calendar-year boundary |

You can change the thresholds in settings, and categories can overlap. By default, the list shows the chats you wrote to most in the last 30 days first, then total messages and importance signals. Frequency is counted from loaded messages, up to 100 per chat by default; a `+` means it may be an undercount. You can raise the depth to 500.

Gender is never inferred from a name. “Acquaintance” from keywords or an LLM is a guess that you can confirm or cancel. “Scam” is a signal to check, not a proven fact. Forwarded warnings and your own messages are excluded from the text risk rules. Analysis is limited to the available part of the history, and media is not transcribed.

Keyword detection is built mainly for Russian. Jobs also recognizes common English terms, but romantic and scam rules recognize few English phrases, so an LLM helps more for English chats.

## Triage and links

`python -m telegram_crm triage` (with the server stopped) sorts chats into “Delete first”, “Review manually” and “Keep”. A verdict is tied to the newest message: if a new one arrives, the chat goes to manual review. Triage itself deletes nothing.

The panel opens from links: `/?triage=remove&select=all` (a group with its chats selected), `/?triage=review_romantic_old` (one bucket), `/?category=job&kind=channel&sort=oldest&q=text`.

## Telegram view

`/telegram` shows chats in the familiar two-pane layout. On opening, the order, pins, archive and your folders are requested from Telegram (dialog list requests only, at most once every 30 seconds), while chat text comes from the last scan. Folders, type and sorting are on the left; color highlighting and smart selection are on the right.

Colors: green means keep, yellow means review, red means to delete, and crimson means strongly recommended to delete (empty chats, deleted accounts, the SCAM label, adult bots, and bots and channels inactive for 2+ years). Removal uses the same cleanup dialog with a backup. Links: `/telegram?color=crimson,red&kind=bot&select=all` (also `sort`, `dir`).

## Search all history

In the Telegram view, press Enter in the search box or click **Search all history**. The query goes to Telegram (`messages.searchGlobal`), so it finds messages older than those loaded. The whole phrase and each meaningful word are searched separately, and messages and chats matching more words come first. Only private chats are searched by default. Clicking a result opens the message with its surroundings from Telegram; use **Load earlier** and **Load later** to read on. Enter in the regular panel's search opens the same search.

## Romance review

In the Telegram view, **Romance review** gathers romantic chats: the triage buckets, chats marked “Yes, an acquaintance” and chats detected by keywords. An LLM rates promise from 0 to 100, explains how the chat ended, and advises to write, wait or let it go. When it advises writing, it suggests an idea for a first message. You can start with a test on 5 chats. It works with an API key or by copying the prompt into your own LLM. Message text goes to the provider only after you confirm, and the app itself never sends messages.

The advice to write is removed in code, whatever the model answers, if the other person said no or you already wrote 3 times in a row without a reply. This is an estimate from a fragment: the LLM cannot see meetings or calls outside Telegram.

## Exporting a chat

The chat card and the Telegram view have two buttons. **Export 100 messages** gives you a TXT file from the local database right away, so the newest messages may be missing. **Export all history** streams the chat from Telegram, oldest first, without marking it as read. If Telegram interrupts the download, the file ends with “EXPORT INCOMPLETE”. Attachments are not saved, only their type is listed.

The same place has a button to remove one chat (**Delete chat**, **Leave channel** or **Leave group**). It opens the regular cleanup dialog with confirmation and a backup.

## Photos and videos

Attachments are not downloaded during a scan. When a chat card opens, the panel gets a list of them, previews and photos load as you scroll, and video and audio stream only after you press Play. Previews and photos are cached in `.data/media-cache` (up to 1 GB, removed along with the chat); streamed files are not written to disk. Self-destructing attachments are never opened. The browser only displays allowlisted types; other files are downloaded.

## Working with an LLM

1. Select chats with the checkboxes or click **Select the rest for LLM**. “The rest” means chats that are not important yet and have no fresh analysis.
2. Click **Copy for LLM**. For each chat this takes the latest messages (50 by default, adjustable in settings), with dates and direction. The ready prompt and JSON schema are included.
3. Paste the batch into your LLM. Then click **Import reply** and paste the JSON. External chat_ids are aliases, and the mapping is kept locally for 7 days.
4. To analyze inside the app, paste an OpenAI or Anthropic key in **Settings**, set a model available to your API account and save. After selecting chats, click **LLM analysis**: the panel shows an estimate of volume and cost. Requests go in parts of 15 chats, up to 3 at once. These are paid API requests.

Models named `claude-*` use the Anthropic API; others use the OpenAI Responses API with `store=False`. Both use structured output. The default model is `gpt-6-luna`; availability depends on your API account. ChatGPT and Claude subscriptions do not replace an API key. No LLM requests are made until you set a key and start an API analysis. Automatic sending of the remaining chats after a scan has its own switch and a configurable chat limit.

The prompt follows the interface language. In English, the instructions and labels sent to the LLM are English; message text is sent as written.

By default, titles become “Chat N”, and phones, emails and @usernames in the text are hidden. This is not full anonymization: other personal details can remain in the content. Results are checked against IDs and message references, and a changed history needs a new batch. Manual marks take priority, and outdated LLM recommendations do not affect categories.

## Automation and folders

Set **Refresh every N hours**. The server keeps running after you close the browser. The Mac must be on and able to reach Telegram; scanning all chats can take minutes. The archive is included. New messages update the history; unchanged chats use the local cache. FloodWait stops a scan, keeps the partial result and observes the pause. After errors, the next scheduled attempt happens no more often than every 15 minutes.

**Telegram folders** shows the exact plan. Applying it creates or updates the CRM folders, using names in the current language; folders from other apps are not changed. Large lists are not cut to fit Telegram's limits, so a result across several folders may be partial. Empty categories are skipped, and existing folders that become empty keep their previous content. Check the plan again before applying. Demo chats never reach Telegram.

The service reads history without marking it as read. Drafts are saved locally and copied by hand. Messages are never sent. Chats are removed or left only through the cleanup dialog after you confirm, and accounts are never blocked.

Commands:

```bash
.venv/bin/python -m telegram_crm report                  # JSON to stdout
.venv/bin/python -m telegram_crm folders                 # plan only
.venv/bin/python -m telegram_crm folders --apply         # apply folders
.venv/bin/python -m telegram_crm scan                    # one refresh
.venv/bin/python scripts/service.py status               # status
.venv/bin/python scripts/service.py stop                 # stop the server started by the shortcut
.venv/bin/python scripts/install_macos_app.py            # reinstall the shortcut and add it to the Dock
```

Run CLI `scan` and `folders --apply` with the web service stopped: a Telethon session must have one owner. The demo is available from a button in the empty panel, and it never mixes with real data. CSV export is in the header. Demo chats are in Russian in both languages.

## Storage and checks

`.data/credentials.json`: API_ID, API_HASH and optional LLM keys. `.data/telegram.session`: the Telegram session. `.data/crm.sqlite3`: local history, marks, batches and LLM results. The folder has `700` permissions and secret files `600`. Storage is local, and the database is not encrypted by the app. The API does not return secrets, and `.data` is excluded from Git. The panel is reachable through loopback only, and changes are protected by a token and an Origin check.

```bash
.venv/bin/python -m pytest -q
```

Documentation: [Telethon](https://docs.telethon.dev/en/stable/basic/signing-in.html), [Telegram API_ID](https://core.telegram.org/api/obtaining_api_id), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
