# Tomo: frequently asked questions

Short answers, no jargon. If yours is not here, see [How to report a problem](#how-do-i-report-a-problem).

## The basics

**What is Tomo?**
A browser extension for Firefox, Chrome, Edge and Brave. Right-click a video, a song or an image and Tomo saves it to your computer, or copies what is said in it as text.

**Is it free?**
Yes. There are no download limits and no waiting between downloads. If you like it, you can say thanks on [Ko-fi](https://ko-fi.com/crosende). Nothing is locked behind it.

**Can I share it, change it or use it at work?**
Tomo is free for the noncommercial uses allowed by the PolyForm Strict License 1.0.0. Commercial use, redistribution and modifications require separate permission. You can read the runtime code in the distributed packages. See the [licence](LICENSE).

**Does it work on any website?**
On almost any site that shows a video, a song or an image: YouTube (Firefox version only), Vimeo, TikTok, Reddit, SoundCloud, Bandcamp, news sites, plain video files and more. A few sites block every download tool on purpose. When that happens, Tomo says so and stops.

**Which browsers and systems does it work on?**
Firefox ([Firefox Add-ons](https://addons.mozilla.org/firefox/addon/tomo/)), and Chrome, Edge and Brave ([Chrome Web Store](https://chromewebstore.google.com/detail/tomo/ejdlbopdblhemopclkioijjcnmlbenfp)), on **Windows 10/11**. Mac and Linux are not ready yet. Phones are not supported.

**What about other browsers?**
Tomo is made for Firefox and Chromium browsers (Chrome, Edge, Brave). Other browsers are not supported, but some may work. If you try one, [tell us](https://github.com/C-Rosende/Tomo/issues/new/choose) how it went.

**Why does the Chrome version not download from YouTube?**
It is a deliberate choice for the Chrome Web Store version. The Chrome version works the same on every other site. If you want YouTube, use the Firefox version.

**Which version should I choose?**
If you want YouTube, Firefox. If you use Chrome, Edge or Brave and do not need YouTube, the Chrome version.

**Why does it ask me to run a line in PowerShell?**
Browsers do not let extensions save videos on their own. Tomo needs one small helper on your computer. The line installs it for you, in your own user folder, with no administrator rights. You do it once.

## Using it

**How do I download something?**
Right-click the video, choose **Download or transcribe**, then **Video**, **Audio** or **Text**. Or click the Tomo button in the toolbar and pick from there.

**Where do my files go?**
To your **Downloads** folder. Tomo never replaces a file: if the name exists it adds `(1)`, `(2)`…

**Is the quality really the original?**
Yes. Tomo saves what the site itself offers, in its best quality. If the page shows a small preview, Tomo tries to find the full version.

**What is the difference between "Max quality" and "MP4 compatible"?**
*Max quality* is the best the site has and is fast. *MP4 compatible* makes a file that plays on every device (TV, phone, old players) but can take longer.

**Why is a video slow to save?**
Long, very sharp (4K or higher) videos are big. Choosing *MP4 compatible* on those can take minutes because the file is converted.

**Can I get the text of a video?**
Yes. Right-click, **Text**. If the video has subtitles you get them in about a second. If not, Tomo can write them out itself, but that needs the optional speech add-on (about 300 MB) and is slower.

**Can I save images in full size?**
Yes. Right-click an image and choose **Save image in max quality**. Many sites show a shrunk copy; Tomo looks for the big one.

## When something goes wrong

**"There's no video on this page."**
Play the video first, then try again. Some pages only load the video after you press play.

**"The site asks you to sign in."**
Some sites only show videos to logged-in people (Reddit, for example). Sign in to the site in your browser, then open Tomo, go to **Settings** and turn on **Use my session**. Your browser asks you to confirm. Tomo uses your own login, only for that site, only on your computer.

**"The site refused access."**
The site may block downloads or may need you to sign in. Try *Use my session*. If it still fails, that site does not allow it.

**It says the helper is missing.**
Run the install line again (it is on the project page). It is safe to repeat and it updates what is already there.

**A site used to work and now it does not.**
Sites change often. Open Tomo, **Settings → Advanced → Update yt-dlp**, then try again.

## Rules and privacy

**Is it legal?**
Tomo is a tool, like a pen. Save only what you have the right to save (your own videos, free licences, things you are allowed to keep) and follow the rules of each site. Tomo does **not** break copy protection and does **not** get around paywalls. Netflix, Disney+ and similar will not work, by design.

**Does Tomo watch what I do?**
No. No account, no ads, no tracking, no statistics. Tomo only contacts the site you asked it to download from.

**Does it read my passwords or cookies?**
No, unless you turn on **Use my session**. That switch is off by default. When it is on, only the cookies of the site you are downloading from are used, for that one download, and they are never saved or sent anywhere else.

**How do I remove Tomo?**
Remove the extension in your browser (in Firefox, `about:addons`). To remove the helper too, run `uninstall.ps1`. Your downloaded files stay where they are.

## How do I report a problem?

Tomo has a button for it, so you do not need to explain anything technical.

1. Click the **Tomo button** in the toolbar and open **Settings** (the sliders icon).
2. Press **Report a problem**.
3. A form opens with your system, browser, Tomo version and component version filled in. Tomo also copies a report with versions, component availability and an error count. Review it and paste it into the **Report** box; it contains no address, title or file name.
4. Write one or two lines: what you did and what you expected.
5. Add a **screenshot** of the problem (required): press **Windows + Shift + S** (**Command + Shift + 4** on a Mac), drag over the area, then paste it into the box with **Ctrl + V**.
6. Press **Submit new issue**.

Questions and ideas are welcome too: [open an issue on GitHub](https://github.com/C-Rosende/Tomo/issues/new).

No GitHub account? Press **Report a problem** anyway: the report is also copied to your clipboard, so you can paste it into an email or a message.

A good report has three things: **what you did**, **what you saw** (the screenshot) and the **report text** Tomo prepared.
