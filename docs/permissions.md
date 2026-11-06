# Permissions

The required permissions are unchanged: each is used by a current feature. The broad host entry remains optional; the always-running content script is a separate manifest declaration.

| Browser | Permission | Why it is needed |
| --- | --- | --- |
| Firefox | `menus` | Creates and updates the right-click save, download, and text actions. |
| Chrome | `contextMenus` | Creates and updates the same right-click actions. |
| Both | `nativeMessaging` | Sends download, conversion, transcription, and AI requests to the local Python host. |
| Both | `notifications` | Reports completion and failures when the popup is closed. |
| Both | `storage` | Keeps preferences, history, and license state locally. |
| Both | `activeTab` | Allows user-invoked inspection of the current tab without a permanent scripting host grant. |
| Both | `downloads` | Saves images through the browser download manager only when the helper is unavailable, and saves inline AI results. |
| Chrome | `scripting` | Runs media detection in the active tab and its accessible frames. |
| Chrome | `alarms` | Wakes the service worker for scheduled helper update checks. |
| Both (optional) | `cookies` | Passes session cookies to the helper only after the user enables session access. |
| Both (optional) | `<all_urls>` | Pairs with optional `cookies` so session access works on user-selected sites; it is not required for ordinary downloads. |
| Chrome (optional hosts) | `https://api.replicate.com/*`, `https://generativelanguage.googleapis.com/*` | Requested only when activating the corresponding image-AI key; validation and image enhancement go only to that provider. |
| Firefox (optional origins) | `https://api.replicate.com/*`, `https://generativelanguage.googleapis.com/*` | Firefox MV2 represents optional host access in `optional_permissions`; the key activation requests the selected provider. |

The `content_scripts` match on `<all_urls>` at `document_start` in `all_frames` so a right-click target, including an image inside an embedded frame, is remembered before the browser context menu opens. Registering only after an `activeTab` click would miss that event and break image/background detection. Chrome excludes YouTube domains from this script, and DRM download blocking remains in the existing platform and host checks.

With the helper available, image downloads use its DNS/IP-pinned HTTP client: each redirect and all resolved addresses are checked, with a 10-hop redirect cap, 20 MiB byte cap and image-header dimension cap. Without the helper, browser `fetch` enforces a 2 MiB byte cap and image-header dimension cap, but MV3 does not expose intermediate redirect targets or connected IPs. This is a best-effort fallback, not equivalent SSRF protection. The browser also fetches the original image for AI input under the 2 MiB cap; the same redirect/IP limitation applies to that read even when the helper is present. The user's provider key stays in `storage.local` and is sent only to its selected provider.
