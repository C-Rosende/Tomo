# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## [0.7.12]

### Added
- Images can be downloaded in their original format or converted in the browser to PNG, JPG or WebP. The menu and popup also offer full, 75% and 50% sizes; the preferred format is remembered.
- Gallery links and lazy-loading hints help locate the full-size image instead of the displayed thumbnail.
- Romanian interface locale.
- Report a problem opens the bug form with the Tomo version, component version, system and browser already filled in. It never includes visited pages or personal data.

### Fixed
- Image-only pages now show image download controls instead of an empty video quality panel.
- The Chrome source manifest now carries the same version as Firefox; the build checks that they agree.

## [0.7.11]

### Fixed
- Bug reports no longer place diagnostic data in the GitHub URL or copy media titles and error messages. The report is copied for review and must be pasted into the form.
- The Chrome store package received the Firefox version at build time; the source manifest kept a 0.0.0 placeholder (replaced in 0.7.12).
- AI consent takes effect immediately after changing providers, and native messaging write failures now end the affected job instead of leaving it running.
- Installation and development documentation now links to the published Chrome Web Store listing.

## [0.7.10]

### Fixed
- Chrome, Edge and Brave: the optional AI features could not be turned on, because the consent request they used only exists in Firefox. They now ask once, naming the provider, and ask again when you pick another one. Turning AI off withdraws the consent.
- Chrome, Edge and Brave: the settings panel now loads the same compatibility layer as the background worker.
## [0.7.9]

### Fixed
- An error message said the request came from Firefox even in Chrome, Edge and Brave. It now says the browser, in all ten languages.
- The privacy notes and the FAQ no longer attribute browser behaviour to Firefox alone.

## [0.7.8]

### Fixed
- Wording review of the panel and helper messages in all ten languages: fewer literal translations, one term per idea, shorter button and status texts. Checked in a 360 px panel for every language: nothing overflows or gets cut off.

## [0.7.7]

### Added
- Four new languages for the panel and the helper's messages: Russian, Polish, Turkish and Japanese. Tomo now has 10. Native speakers are welcome to suggest wording fixes.

## [0.7.6]

### Fixed
- Wording fixes in the German, Italian and English texts added in 0.7.5.

## [0.7.5]

### Added
- Tomo now checks every finished download before saving it. An empty file, or one with no audio or video, is reported as an error instead of a success.
- Settings can send downloads to a folder inside Downloads and name files with a template such as `{author} - {title}` (fields: title, author, id, date, site).
- The video panel states the limits up front (no DRM, no live streams, 12 hours per video, no download limits). The Chrome version also says it does not download from YouTube.

### Changed
- The privacy notes explain exactly what the page script that runs on all sites does, and the FAQ says which browsers are supported.

## [0.7.4]

### Fixed
- The first-run message no longer tells you to double-click a file you do not have. It now says the helper takes a few minutes to install, depending on your connection, and points to the guide.
- The one-line installer now works on a default Windows, where PowerShell blocks script files, and leaves your PowerShell session settings as they were.
- The installer's last message tells you to go back to Firefox and open Tomo instead of asking for a file you already have, and it works the same in any browser.
- `verify.ps1` says plainly when the helper is not installed.

## [0.7.3]

### Added
- Right-click any image, GIF or picture drawn as a CSS background and Tomo saves the original: the largest `srcset` entry, the link around the picture, lazy-load attributes (`data-src`, `data-original`…) and known site rules (Wikipedia, Imgur, X, Pinterest, Reddit, YouTube thumbnails, WordPress sizes).
- Pictures under a transparent layer (galleries, Pinterest-style cards) are found when the layer covers a single large image.

### Fixed
- The saved file has the extension of its real type, and nothing is converted: transparent PNG, animated GIF, SVG and WebP keep their bytes.
- Addresses suggested by a page must be public `http(s)` addresses and are checked to be images before saving, so a page cannot steer Tomo to your local network or save an HTML page as a picture.
- Wikipedia and Imgur thumbnails now resolve to the full-size file.

## [0.7.2]

### Security
- The helper checks every download address, redirect and DNS answer against private and local networks, blocks FFmpeg-driven downloads and keeps secrets out of logs and errors.
- AI requests no longer follow redirects, so an API key is never sent to a different address.

### Fixed
- A redirect that changes scheme (HTTPS to HTTP) no longer carries `Authorization` or `Cookie` headers.
- A stale history lock left by a crash is recovered, and a history failure no longer marks a finished download as failed.
- Image downloads no longer hang when the browser finishes before the listener is registered, and one failing URL no longer stops the fallbacks.
- Replies from the helper are bounded (titles, language lists and total size), so a huge page can no longer break the connection to the browser.
- The extension keeps at most 20 resolved pages and 30 finished jobs in memory.
- Forced cancellation removes the job's temporary files immediately.
- Subtitles in other formats are converted to VTT (when FFmpeg is available) so they can be transcribed. URLs in helper replies are never truncated.
- `verify.ps1` now hashes `config.json` and checks that the configured tools exist; `install.sh` restores the previous `.venv` if an update fails.
- Installer and verifier messages are in English, like the rest of the project.
- File names drop bidirectional and zero-width control characters.
- The installer now stops with a clear message when FFmpeg cannot be installed, restores `config.json` if the self-test fails and removes its temporary backup when it succeeds.
- The new logo is used everywhere (icons, README banner, social preview).

## [0.7.1]

### Fixed
- Tests failed on Linux and macOS because a work folder was missing. The helper now creates it when needed.

### Changed
- First version published as a listed add-on on Firefox Add-ons. Same features as 0.7.0.

## [0.7.0]

### Added
- Chrome version for Chrome, Edge and Brave (Manifest V3), built from the same code as the Firefox version. It does not download from YouTube.
- `node tools/build.js` builds both packages.
- The installers register the helper for Chrome, Edge and Brave from `chromium-ids.txt`.
- CODEOWNERS and issue forms for orderly reports from every browser and system.

### Changed
- README rewritten for users; technical notes moved to `docs/DEVELOPMENT.md`.
- GitHub Actions pinned to exact commits.

## [0.6.5]

### Changed
- Licence changed from MIT to PolyForm Noncommercial 1.0.0. Version 0.6.4 stays under MIT.
- Clearer message when a video has no subtitles and offline transcription is not installed.
- The report button now says the report is ready to review.

### Added
- Report forms for bugs and suggestions, with system and browser fields, a required screenshot and automatic labels.
- Contributing guide, code of conduct and security policy.

## [0.6.4]

First public release.
