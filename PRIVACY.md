# Privacy policy

Tomo is a browser extension plus a small helper that runs on your computer. The author runs no server, has no accounts and receives no data from Tomo: no analytics, no telemetry, no ads.

## What stays on your computer

- **Settings and history** are stored by your browser (`storage.local`) and by the helper in your user profile folder. You can turn history off or clear it in Settings.
- **Downloads and temporary files** go to your Downloads folder and a temporary folder. Temporary files are deleted when a job ends.
- **Cache.** To make repeated actions fast, the helper keeps video metadata and subtitle or transcript text in a cache folder inside its install folder (`%LOCALAPPDATA%\Tomo`). It is bounded by the cache size in Settings and you can empty it any time with **Clear cache**. Uninstalling the helper removes it. Cached text is not used when you download with **Use my session**.
- **Optional local metrics** (off by default) are timings written to a file on your PC. They are never sent anywhere.
- **API keys** you enter for AI are stored in the extension's local storage on your PC. They are not written to history, logs or metrics.

## What Tomo sends over the network

Tomo only makes these connections, all started by you or by a setting you control:

1. **The site you download from.** The helper (yt-dlp) requests the page, media and subtitles of the address you chose. With **Use my session** turned on, the cookies of that one site are passed to the helper for that download only. They are not stored or sent anywhere else.
2. **Updates of yt-dlp.** The helper updates yt-dlp from PyPI (`pypi.org`) about every three days, and when a download fails because the site changed. This sends no personal data beyond what any `pip` request involves, such as your IP address.
3. **Your AI provider (optional, off by default).** If you pick a provider and give it a key, Tomo sends the video's subtitles or transcript text and title to **that provider** to summarize or translate. This is the only case where page content leaves your computer. Tomo asks for your consent first: Firefox shows its own data collection permission ("website content") and you can withdraw it any time in `about:addons`; Chrome, Edge and Brave show a question that names the provider and ask again when you pick another one. In every browser, setting the provider to Off withdraws the consent. What the provider does with the text is governed by its own policy. If you use a local provider such as Ollama, the text stays on your network.
4. **Installation only.** The installer may download Python, FFmpeg and Deno through `winget`, and yt-dlp and faster-whisper through `pip`. Offline transcription downloads its speech model from Hugging Face the first time you use a model.

5. **Thumbnails.** The panel shows the video's thumbnail, which your browser loads directly from the image server the video site points to (often its CDN). Tomo does not proxy or store it.

Nothing else is contacted. Tomo does not read your browsing, bookmarks or passwords.

## Permissions, in plain words

- `nativeMessaging`: talk to the helper on your PC.
- `menus`, `notifications`, `storage`, `downloads`: right-click menu, alerts, saving settings, saving images.
- `activeTab` and access to all sites: detect the video on the page you are looking at and keep the menu over videos. The page script runs on every site, since any site can host a video. It only stops a site from hiding Tomo's right-click menu over a video (`extension/context.js`, 13 lines). It reads no page content and sends nothing out. The Chromium version has a second script that remembers which element you right-clicked and describes it only when you ask Tomo to detect it.
- Optional `cookies` and all-sites access: only if you turn on **Use my session**; turning it off removes them.

## Contact

Questions or concerns: open an issue at <https://github.com/C-Rosende/Tomo/issues>.

Last updated: 2026-11-01.
