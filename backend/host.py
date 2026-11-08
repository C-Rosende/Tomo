from __future__ import annotations

import copy
from datetime import date
import hashlib
import html
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import unquote, urljoin, urlsplit
import urllib.error
import urllib.request

BACKEND_VERSION = "0.8.1"
PROTOCOL = 2
MAX_MESSAGE = 256 * 1024
HOME = Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"tomo_{name}", HOME / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sysdep = _sibling("sysdep")
DATA = Path(os.environ.get("TOMO_DATA") or HOME)
TMP_ROOT = DATA / "tmp"
CACHE = DATA / "cache" / "public-v2"
AUTHENTICATED = False
HISTORY = DATA / "history.json"
RECORD_HISTORY = True
CONFIG = HOME / "config.json"
INFO_TTL = 20 * 60
STALE_TMP = 6 * 3600
NO_WINDOW = sysdep.NO_WINDOW
OUT_TEMPLATE = "media.%(ext)s"


ORIGINAL_CONTAINER = ["--merge-output-format", "mp4/mkv", "--remux-video", "mp4>mp4/mkv"]
PRESETS: dict[str, list[str]] = {
    "best": ["-f", "bv*+ba/b", *ORIGINAL_CONTAINER],
    "mp4": ["-f", "bv*+ba/b", "-S", "res,fps,hdr:sdr,vcodec:h264,acodec:aac",
            "--merge-output-format", "mp4"],
    "mp4-remux": ["-f", "bv*+ba/b", "-S", "res,fps,ext:mp4:m4a",
                  "--merge-output-format", "mp4", "--remux-video", "mp4"],
    "mkv": ["-f", "bv*+ba/b", "--merge-output-format", "mkv", "--remux-video", "mkv"],
    "webm": ["-f", "bv*[ext=webm]+ba[ext=webm]/b[ext=webm]", "--merge-output-format", "webm"],
    "audio": ["-f", "ba/b", "-x"],
    "mp3": ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"],

    "m4a": ["-f", "ba[acodec^=mp4a]/ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a"],
}
AUDIO_PRESETS = {"audio", "mp3", "m4a"}
CODEC_SORT = {"auto": "", "h264": "vcodec:h264", "vp9": "vcodec:vp9", "av1": "vcodec:av01"}
CONTAINERS = {"auto": ORIGINAL_CONTAINER, "mp4": ["--merge-output-format", "mp4"],
              "mkv": ["--merge-output-format", "mkv", "--remux-video", "mkv"],
              "webm": ["--merge-output-format", "webm"]}
EXPORT_FORMATS = {"txt", "srt", "vtt"}
ASR_MODELS = {"tiny", "base", "small", "medium", "large-v3-turbo"}
SOURCES = {"auto", "subs", "asr"}
LEGACY = {
    "mp4": ("download", {"preset": "mp4"}), "mp4-fast": ("download", {"preset": "mp4-remux"}),
    "mkv": ("download", {"preset": "mkv"}), "webm": ("download", {"preset": "webm"}),
    "mp3": ("download", {"preset": "mp3"}),
    "captions": ("transcript", {"source": "subs"}),
    "captions-timed": ("transcript", {"source": "subs", "timed": True}),
    "asr": ("transcript", {"source": "asr"}),
}
FORMAT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=-]{0,79}$")
LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,3}$")
SKIP_SUBS = {"live_chat", "rechat", "danmaku", "comments"}


_LANG = "en"
_CATALOG: dict | None = None


def set_lang(value) -> None:
    global _LANG
    code = str(value or "").split("-")[0].lower()
    _LANG = code if re.fullmatch(r"[a-z]{2,3}", code) else "en"


def T(text: str, **values) -> str:
    global _CATALOG
    if _LANG != "en":
        if _CATALOG is None:
            try:
                _CATALOG = json.loads(Path(__file__).with_name("locales.json").read_text("utf-8"))
            except (OSError, ValueError):
                _CATALOG = {}
        text = (_CATALOG.get(_LANG) or {}).get(text) or text
    return text.format(**values) if values else text


_OUT = sys.stdout.buffer
_out_lock = threading.Lock()
CANCEL = threading.Event()
FINISHED = threading.Event()
_IN = None
T0 = time.time()
STATS: dict[str, object] = {}
METRICS = DATA / "metrics.jsonl"


class Cancelled(Exception):
    pass


_JOB_DIRS: set[str] = set()
MAX_LANGS = 200
MAX_REPLY = 900_000


MAX_URL = 2000


def clip(value, limit: int):
    return value[:limit] if isinstance(value, str) else value


def short_url(value):
    return value if not isinstance(value, str) or len(value) <= MAX_URL else None


def send(message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_REPLY:
        payload = json.dumps({"status": "error", "message": T("Response too large.")},
                             ensure_ascii=False).encode("utf-8")
    with _out_lock:
        _OUT.write(struct.pack("<I", len(payload)))
        _OUT.write(payload)
        _OUT.flush()


def _read_exact(stream, size: int) -> bytes:
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream=None) -> dict | None:
    stream = stream or _IN or sys.stdin.buffer
    length = _read_exact(stream, 4)
    if not length:
        return None
    if len(length) != 4:
        raise ValueError(T("No valid request was received from the browser."))
    size = struct.unpack("<I", length)[0]
    if size < 2 or size > MAX_MESSAGE:
        raise ValueError(T("Request size not allowed."))
    data = _read_exact(stream, size)
    if len(data) != size:
        raise ValueError(T("No valid request was received from the browser."))
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError(T("Invalid request."))
    return result


def receive() -> dict:
    message = read_message()
    if message is None:
        raise ValueError(T("No valid request was received from the browser."))
    return message


def check_cancel() -> None:
    if CANCEL.is_set():
        raise Cancelled()


def watch_input() -> None:
    try:
        if sysdep.IS_WINDOWS:
            handle = sysdep.stdin_handle(_IN)
            while True:
                state = sysdep.pipe_has_data(handle)
                if state is None:
                    break
                if state:
                    message = read_message(_IN)
                    if message is None or message.get("cmd") == "cancel":
                        break
                else:
                    time.sleep(0.2)
        else:
            while True:
                message = read_message()
                if message is None or message.get("cmd") == "cancel":
                    break
    except Exception:
        pass
    CANCEL.set()
    if not FINISHED.wait(8):


        try:
            send({"status": "cancelled", "message": T("Cancelled.")})
        except Exception:
            pass
        sysdep.kill_children()
        for folder in list(_JOB_DIRS):
            shutil.rmtree(folder, ignore_errors=True)
        os._exit(0)


class Progress:

    def __init__(self) -> None:
        self.stage, self.last = None, 0.0
        self.started = time.monotonic()
        self.stages: dict[str, float] = {}
        self._current, self._since = None, self.started

    def timings(self) -> dict[str, float]:
        result = dict(self.stages)
        if self._current:
            result[self._current] = result.get(self._current, 0) + time.monotonic() - self._since
        return {k: round(v, 2) for k, v in result.items()}

    def __call__(self, stage: str, percent: float | None = None, detail: str = "",
                 force: bool = False, meta: dict | None = None) -> None:
        check_cancel()
        now = time.monotonic()
        if stage != self._current:
            if self._current:
                self.stages[self._current] = self.stages.get(self._current, 0) + now - self._since
            self._current, self._since = stage, now
        if not force and stage == self.stage and now - self.last < 0.33:
            return
        self.stage, self.last = stage, now
        send({"status": "progress", "stage": stage, "detail": detail, **(meta or {}),
              "percent": None if percent is None else round(max(0.0, min(100.0, percent)), 1)})


contain_children = sysdep.contain_children
job_usage = sysdep.job_usage


def record_metrics(action: str, req: dict, progress: Progress, status: str, result: dict, error: str = "") -> None:
    if action == "clear-history" or req.get("record") is False:
        return
    if not (req.get("metrics") is True or load_config().get("metrics") is True):
        return
    if action not in NEEDS_URL and action != "update-ytdlp":
        return
    stages = progress.timings()
    entry = {
        "t": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, "status": status,
        "kind": req.get("preset") or "/".join(str(x) for x in (req.get("mode") or "", req.get("format") or "",
                                                               "timed" if req.get("timed") else "",
                                                               req.get("source") or "") if x),
        "site": urlsplit(req.get("url", "")).hostname or "", "extractor": result.get("extractor"),
        "secs": round(time.monotonic() - progress.started, 2), "stages": stages,
        "duration": result.get("duration"), "source": STATS.get("source"),
        "info_cache": STATS.get("info_cache"), "tx_cache": STATS.get("tx_cache"), **job_usage(),
    }
    if isinstance(req.get("t_click"), (int, float)):
        entry["spawn_ms"] = round(T0 * 1000 - req["t_click"])
    if isinstance(req.get("queued_ms"), (int, float)):
        entry["queued_ms"] = round(req["queued_ms"])
    path = result.get("path")
    if path and os.path.isfile(path):
        entry["bytes"] = os.path.getsize(path)
        if stages.get("download"):
            entry["mb_s"] = round(entry["bytes"] / 1048576 / stages["download"], 2)
    if error:
        entry["error"] = error[:160]
    try:
        if METRICS.exists() and METRICS.stat().st_size > 5 * 1024 * 1024:
            os.replace(METRICS, METRICS.with_suffix(".1.jsonl"))
        with METRICS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_config() -> dict:
    try:
        value = json.loads(CONFIG.read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def tool(name: str) -> str:
    configured = load_config().get(name)
    if isinstance(configured, str) and Path(configured).is_file():
        return configured
    return shutil.which(name) or name


def js_runtime_args() -> list[str]:
    for name in ("deno", "node"):
        path = tool(name)
        if os.path.isabs(path):
            return ["--js-runtimes", f"{name}:{path}"]
    return []


def sweep_stale_tmp() -> None:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for folder in (*TMP_ROOT.glob("job-*"), *TMP_ROOT.glob("session-*.txt")):
        try:
            if now - folder.stat().st_mtime > STALE_TMP:
                folder.unlink() if folder.is_file() else shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def prune_cache(limit_mb: int) -> None:
    now = time.time()
    for path in (CACHE / "info").glob("*.json"):
        try:
            if now - path.stat().st_mtime > INFO_TTL:
                path.unlink()
        except OSError:
            pass
    files = []
    for path in CACHE.glob("*/*.json"):
        try:
            stat = path.stat()
            files.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            pass
    files.sort()
    total = sum(size for _, size, _ in files)
    limit = max(0, limit_mb) * 1024 * 1024
    for _, size, path in files:
        if total <= limit:
            break
        try:
            path.unlink()
            total -= size
        except OSError:
            pass


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 9:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def cache_json(path: Path, value) -> None:
    if not AUTHENTICATED:
        write_json_atomic(path, value)


def read_json_retry(path: Path, default):
    for _ in range(10):
        try:
            return json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            time.sleep(0.05)
    return default


LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def is_internal_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES) or "." not in host and ":" not in host:
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


CHROMIUM_CLIENT = any(arg.startswith("chrome-extension://") for arg in sys.argv[1:])
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "ytimg.com")


def is_youtube_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in YOUTUBE_HOSTS)


def validate_url(url: str, *, check_dns: bool = True) -> str:
    if not isinstance(url, str) or len(url) > 8192 or re.search(r"[\x00-\x20\x7f]", url):
        raise ValueError(T("Invalid or too long URL."))
    value = urlsplit(url)
    if value.scheme not in ("http", "https") or not value.hostname or value.username:
        raise ValueError(T("Only HTTP(S) videos are accepted."))
    if CHROMIUM_CLIENT and is_youtube_host(value.hostname):
        raise ValueError(T("This version of Tomo does not support YouTube. Use the Firefox version."))
    addresses = resolve_addresses(value.hostname) if check_dns else []
    if (is_internal_host(value.hostname)
            or check_dns and (not addresses or any(is_internal_host(a) for a in addresses))):
        raise ValueError(T("Local network addresses are not accepted."))
    vimeo = re.fullmatch(r"https?://(?:www\.)?vimeo\.com/(\d+)(?:/([0-9a-f]{10}))?/?(?:[?#].*)?", url)
    if vimeo:
        return f"https://player.vimeo.com/video/{vimeo[1]}" + (f"?h={vimeo[2]}" if vimeo[2] else "")
    return url


def resolve_addresses(host: str) -> list[str]:
    try:
        return list({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    except (OSError, UnicodeError):
        return []


def optional_url(value) -> str:
    return validate_url(value) if value else ""


def choice(value, allowed, default):
    if value is None or value == "":
        return default
    if value not in allowed:
        raise ValueError(T("Parameter not allowed."))
    return value


def format_id(value) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not FORMAT_ID.match(value):
        raise ValueError(T("Invalid format ID."))
    return value


def lang_code(value) -> str:
    if value in (None, "", "auto"):
        return ""
    if not isinstance(value, str) or not LANG.match(value):
        raise ValueError(T("Invalid language code."))
    return value


def lang_list(value) -> list[str]:
    if not isinstance(value, list):
        return ["es", "en"]
    return [code for code in (lang_code(v) for v in value[:8]) if code]


def redact(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(text))
    text = re.sub(r"https?://\S+", "[URL ocultada]", text)
    text = re.sub(r"^(ERROR|WARNING):\s*(\[[^\]]+\]\s*)?(\S+:\s)?", "", text.strip())
    return text[:300]


LOGIN_ERROR = (r"[Ss]ign in|log ?in|cookies|age-restricted|members-only|[Pp]rivate|[Qq]uarantined|"
               r"[Aa]uthentication is required|account .{0,60}is required")


def error_kind(exc: BaseException) -> str:
    text = str(exc)
    if type(exc) in (RuntimeError, ValueError):
        return "user"
    if re.search(r"HTTP Error 403|Forbidden|Requested format is not available|[Uu]nable to extract|"
                 r"[Ss]ignature|nsig|player|[Pp]age needs to be reloaded", text):
        return "extractor"
    if re.search(r"429|DRM|404|Not Found|does not exist|deleted|unavailable|Unsupported URL|getaddrinfo|"
                 r"timed out|Connection|Network|No media found|is processing|unable to access|" + LOGIN_ERROR, text):
        return "site"
    return "extractor"


def friendly(exc: BaseException) -> str:
    text = str(exc)
    if type(exc) in (RuntimeError, ValueError):
        return redact(text) or T("Error in the local process.")
    rules = [
        (r"Requested format is not available", T("That format isn't available for this video.")),
        (r"Unsupported URL|No media found", T("There's no video on this page.")),
        (r"HTTP Error 429|Too Many Requests", T("The site is rate-limiting requests (HTTP 429). Wait a few minutes.")),
        (r"DRM", T("DRM-protected content: it can't be downloaded.")),
        (LOGIN_ERROR,
         T("The site requires signing in. Sign in in your browser and turn on “Use my session” in Settings.")),
        (r"HTTP Error 404|Not Found|does not exist|been deleted|unavailable|is processing",
         T("The video doesn't exist, was removed or isn't publicly available.")),
        (r"HTTP Error 403|Forbidden|unable to access", T("The site denied access (HTTP 403). It may require signing in or block downloads.")),
        (r"Unable to download webpage|getaddrinfo|timed out|Connection|Network",
         T("Network error while contacting the site. Check your connection and retry.")),
        (r"ffmpeg.*not found|ffprobe.*not found|Falta una herramienta|A tool is missing",
         T("FFmpeg is missing. Run the installer again.")),
    ]
    for pattern, message in rules:
        if re.search(pattern, text):
            return message
    return redact(text) or T("Error in the local process.")


def run(argv: list[str], cwd: Path | None = None, timeout: int = 7200) -> str:
    if Path(argv[0]).stem.lower() in {"ffmpeg", "ffprobe"}:
        argv = [argv[0], "-protocol_whitelist", "file,pipe", *argv[1:]]
    try:
        process = subprocess.run(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, shell=False, check=False, creationflags=NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(T("A tool is missing: {name}.", name=Path(argv[0]).name)) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(T("Timed out.")) from exc
    if process.returncode != 0:
        text = (process.stderr or process.stdout).splitlines()
        relevant = [line.strip() for line in text if line.strip() and
                    ("ERROR:" in line or "error" in line.lower())]
        raise RuntimeError(redact(relevant[-1] if relevant else T("Code {code}.", code=process.returncode)))
    return process.stdout


def run_ffmpeg(args: list[str], duration: float | None, progress: Progress, stage: str, label: str) -> None:
    argv = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats",
            "-protocol_whitelist", "file,pipe", "-progress", "pipe:1", *args]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    except FileNotFoundError as exc:
        raise RuntimeError(T("A tool is missing: {name}.", name="ffmpeg")) from exc
    errors: list[str] = []
    reader = threading.Thread(target=lambda: errors.extend(proc.stderr), daemon=True)
    reader.start()
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and duration:
                try:
                    progress(stage, int(line.split("=", 1)[1]) / 1e6 / duration * 100, label)
                except ValueError:
                    pass
            check_cancel()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(2)
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0:
        lines = [e.strip() for e in errors if e.strip()]
        raise RuntimeError(redact(lines[-1] if lines else T("FFmpeg exited with code {code}.", code=proc.returncode)))


downloads_folder = sysdep.downloads_folder


def unique_destination(path: Path) -> Path:
    destination = path
    i = 1
    while destination.exists():
        destination = path.with_name(f"{path.stem} ({i}){path.suffix}")
        i += 1
    return destination


def safe_name(text: str, limit: int = 150) -> str:
    text = re.sub(r'[​-‏‪-‮⁦-⁩﻿]', "", text or "")
    text = re.sub(r":\s+|[/\\|]", " - ", text).replace('"', "'")
    text = re.sub(r'[<>?*\x00-\x1f]', "", text).replace(":", " ")
    text = re.sub(r"\s+", " ", text).strip(" .-")
    if re.fullmatch(r"(?i:CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", text.split(".", 1)[0].rstrip(" .")):
        text = "_" + text
    size = max(1, min(limit, 240))
    if len(text.encode("utf-8")) > size:
        cut = text.encode("utf-8")[:size].decode("utf-8", "ignore")
        words = cut.rsplit(" ", 1)
        text = words[0] if len(words) > 1 and words[0] else cut
    return text.rstrip(" .-") or "Video"


def safe_subfolder(value) -> Path:
    if not isinstance(value, str):
        return Path()
    parts = [safe_name(p.replace(":", "_"), 60) for p in re.split(r"[\\/]+", value[:300])
             if p.strip(" .") not in ("", ".", "..")]
    return Path(*parts[:3]) if parts else Path()


def move_to_downloads(source: Path, name: str | None = None, subfolder=None) -> Path:
    folder = downloads_folder() / safe_subfolder(subfolder)
    folder.mkdir(parents=True, exist_ok=True)
    original = Path(name or source.name)
    suffix = safe_name(original.suffix, 20) if original.suffix else ""
    suffix = "." + suffix.lstrip(".") if suffix else ""
    base = safe_name(original.stem, 200)
    i = 0
    linked = False
    while True:
        destination = folder / f"{base}{f' ({i})' if i else ''}{suffix}"
        try:
            os.link(source, destination)
            linked = True
            break
        except FileExistsError:
            i += 1
        except OSError:
            break
    if linked:
        try:
            source.unlink()
        except OSError:
            pass
        record_history(destination)
        return destination
    i = 0
    while True:
        destination = folder / f"{base}{f' ({i})' if i else ''}{suffix}"
        try:
            output = destination.open("xb")
            break
        except FileExistsError:
            i += 1
    try:
        with output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        source.unlink()
    except OSError:
        pass
    record_history(destination)
    return destination


@contextmanager
def history_lock():
    lock = HISTORY.with_suffix(".lock")
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    held = False
    for _ in range(100):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
            break
        except (FileExistsError, PermissionError):
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.05)
        except OSError:
            break
    if not held:
        raise OSError(T("Could not lock the history."))
    try:
        yield
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def record_history(path: Path) -> None:
    if not RECORD_HISTORY:
        return
    try:
        with history_lock():
            items = read_json_retry(HISTORY, [])
            items = items if isinstance(items, list) else []
            items = [i for i in items if isinstance(i, str) and i != str(path)][-299:] + [str(path)]
            write_json_atomic(HISTORY, items)
    except OSError:
        pass


def allowed_output(value) -> Path:
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError(T("Invalid path."))
    items = read_json_retry(HISTORY, [])
    path = Path(value)
    if not isinstance(items, list) or value not in items or not path.is_file():
        raise ValueError(T("File not found or not created by the extension."))
    if not path.resolve().is_relative_to(downloads_folder().resolve()):
        raise ValueError(T("Path outside Downloads."))
    return path


def get_media_file(folder: Path) -> Path:
    excludes = {".part", ".ytdl", ".vtt", ".srt", ".json", ".txt", ".tmp"}
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() not in excludes]
    if len(files) != 1:
        raise RuntimeError(T("Couldn't identify the downloaded file (possible DRM or unsupported link)."))
    return files[0]


def ffprobe_codecs(source: Path) -> tuple[str, str, str]:
    result = run([tool("ffprobe"), "-v", "error", "-show_entries",
                  "stream=codec_type,codec_name,pix_fmt", "-of", "json", str(source)])
    streams = json.loads(result).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return video.get("codec_name", ""), audio.get("codec_name", ""), video.get("pix_fmt", "")


def ffprobe_duration(source: Path) -> float | None:
    try:
        value = run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(source)]).strip()
        return float(value) if value and value != "N/A" else None
    except (RuntimeError, ValueError):
        return None


def verify_output(source: Path, audio_only: bool = False) -> None:
    bad = T("The downloaded file is empty or damaged and was not saved. Try again or pick another format.")
    try:
        if source.stat().st_size == 0:
            raise RuntimeError(bad)
    except OSError:
        raise RuntimeError(bad) from None
    try:
        kinds = {s.get("codec_type") for s in json.loads(run([
            tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(source)
        ])).get("streams", [])}
    except (RuntimeError, ValueError):
        raise RuntimeError(bad) from None
    if not kinds & ({"audio"} if audio_only else {"video", "audio"}):
        raise RuntimeError(bad)
    duration = ffprobe_duration(source)
    if duration is not None and duration <= 0:
        raise RuntimeError(bad)


def human_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def fmt_clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class YdlLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.errors.append(msg)


def referer_args(url: str, page_url: str) -> list[str]:
    return ["--add-headers", f"Referer:{page_url}"] if page_url and page_url != url else []


MAX_DURATION = 12 * 3600
MAX_FILESIZE = "16G"


def check_limits(info: dict) -> None:
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > MAX_DURATION:
        raise RuntimeError(T("The video is longer than {hours} hours.", hours=MAX_DURATION // 3600))


def guard_redirects(ydl):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), SafeHTTPHandler(), SafeHTTPSHandler(),
        SafeRedirectHandler(), urllib.request.HTTPCookieProcessor(ydl.cookiejar))

    def urlopen(request):
        from yt_dlp.networking import Response
        from yt_dlp.networking.exceptions import HTTPError, TransportError
        url = request if isinstance(request, str) else request.url
        validate_url(url, check_dns=False)
        headers = {**(ydl.params.get("http_headers") or {}), **dict(getattr(request, "headers", {}) or {})}
        headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}
        headers["Accept-Encoding"] = "identity"
        req = urllib.request.Request(url, data=getattr(request, "data", None),
                                     headers=headers,
                                     method=getattr(request, "method", None))
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            raise HTTPError(Response(exc, exc.url, exc.headers, exc.code)) from exc
        except urllib.error.URLError as exc:
            raise TransportError(str(exc.reason)) from exc
        return Response(response, response.url, response.headers, response.status)

    ydl.urlopen = urlopen
    download = ydl.dl

    def guarded_download(name, info, *args, **kwargs):
        from yt_dlp.downloader import get_suitable_downloader
        downloader = get_suitable_downloader(info, ydl.params)
        if downloader is None or downloader.__name__ not in {"HttpFD", "DashSegmentsFD", "HlsFD"}:
            raise ValueError(T("This format requires a network transport that is not allowed."))
        return download(name, info, *args, **kwargs)

    def refuse_ffmpeg_download(*args, **kwargs):
        raise ValueError(T("This format requires a network transport that is not allowed."))

    from yt_dlp.downloader.external import FFmpegFD
    FFmpegFD.real_download = refuse_ffmpeg_download
    ydl.dl = guarded_download
    return ydl


def approved_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    if is_internal_host(host):
        raise ValueError(T("Local network addresses are not accepted."))
    endpoints = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not endpoints or any(is_internal_host(item[4][0]) for item in endpoints):
        raise ValueError(T("Local network addresses are not accepted."))
    last = None
    for family, kind, proto, _, endpoint in endpoints:
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(endpoint)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise last


class SafeHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = approved_connection


class SafeHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = approved_connection


class SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        validate_url(req.full_url, check_dns=False)
        return self.do_open(SafeHTTPConnection, req)


class SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        validate_url(req.full_url, check_dns=False)
        return self.do_open(SafeHTTPSConnection, req, context=self._context)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, check_dns=False)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        before, after = urlsplit(req.full_url), urlsplit(newurl)
        if redirected and (before.scheme, before.netloc) != (after.scheme, after.netloc):
            redirected.remove_header("Authorization")
            redirected.remove_header("Cookie")
        return redirected


def make_ydl(args: list[str], hooks=(), pp_hooks=()):
    import yt_dlp
    argv = ["--ignore-config", "--no-plugin-dirs", "--no-playlist", "--no-warnings", "--no-progress",
            "--no-mtime", "--windows-filenames", "--concurrent-fragments", "4", "--max-filesize", MAX_FILESIZE,
            "--extractor-args", "youtube:player_client=default,mweb,web_embedded",
            *js_runtime_args(), *(["--cookies", str(SESSION_FILE)] if SESSION_FILE else []),
            *args]
    opts = yt_dlp.parse_options(argv).ydl_opts
    ffmpeg = tool("ffmpeg")
    if os.path.isabs(ffmpeg):
        opts["ffmpeg_location"] = ffmpeg

    opts.update(logger=YdlLogger(), quiet=True, noprogress=True, ignoreerrors=False,
                postprocessor_args={**opts.get("postprocessor_args", {}),
                                    "ffmpeg_i": ["-protocol_whitelist", "file,pipe"],
                                    "ffprobe": ["-protocol_whitelist", "file,pipe"]},
                progress_hooks=list(hooks), postprocessor_hooks=list(pp_hooks))
    return guard_redirects(yt_dlp.YoutubeDL(opts))


def info_cache_file(url: str) -> Path:
    return CACHE / "info" / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + ".json")


BLOCKED_ERROR = re.compile(r"HTTP Error 40[13]|Forbidden|Just a moment|Cloudflare", re.I)
ACCESS_ERROR = re.compile(rf"{LOGIN_ERROR}|DRM|geo[- ]?(?:blocked|restricted)|not available in your (?:country|region)", re.I)


def can_impersonate() -> bool:
    return importlib.util.find_spec("curl_cffi") is not None


def resolve_info(url: str, page_url: str = "", fresh: bool = False) -> dict:
    path = info_cache_file(url)
    if not fresh and not AUTHENTICATED:
        try:
            if time.time() - path.stat().st_mtime < INFO_TTL:
                STATS["info_cache"] = True
                return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            pass
    base = [*referer_args(url, page_url), "--flat-playlist"]
    try:
        with make_ydl(base) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    except Exception as exc:
        if not (BLOCKED_ERROR.search(str(exc)) and not ACCESS_ERROR.search(str(exc)) and can_impersonate()):
            raise
        check_cancel()
        try:
            with make_ydl([*base, "--impersonate", "chrome"]) as ydl:
                info = ydl.sanitize_info(ydl.extract_info(url, download=False))
        except Exception as retry_exc:
            if not BLOCKED_ERROR.search(str(retry_exc)) or ACCESS_ERROR.search(str(retry_exc)):
                raise
            check_cancel()
            with make_ydl([*base, "--force-generic-extractor", "--impersonate", "chrome"]) as ydl:
                info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    STATS["info_cache"] = False
    cache_json(path, info)
    return info


def is_playlist(info: dict) -> bool:
    return info.get("_type") in ("playlist", "multi_video")


GENERIC_EXTRACTORS = {"generic", "html5mediaembed"}
MEDIA_EXT = r"(?:mp4|webm|mkv|mov|m4v)"
ABS_MEDIA = re.compile(rf"""https?://[^\s"'<>()\\]+?\.{MEDIA_EXT}(?:\?[^\s"'<>()\\]*)?(?=["'\s<>()\\]|$)""", re.I)
REL_MEDIA = re.compile(rf"""(?:href|src)\s*=\s*["']([^"']+?\.{MEDIA_EXT}(?:\?[^"']*)?)["']""", re.I)
QUALITY_SUFFIX = re.compile(r"(?:[_\-. ](?:\d{3,4}p?|hd|sd|uhd|sample|preview|low|high|med|mobile))+$", re.I)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


def is_generic(info: dict | None) -> bool:
    return bool(info) and str(info.get("extractor_key", "")).lower() in GENERIC_EXTRACTORS


def is_manifest(info: dict) -> bool:
    return any(str(f.get("protocol", "")).startswith(("m3u8", "http_dash")) for f in info.get("formats") or [])


def media_token(url: str) -> str:
    stem = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).lower().rsplit(".", 1)[0]
    tokens = re.findall(r"[a-z0-9]{12,}", stem)
    return max(tokens, key=len) if tokens else (QUALITY_SUFFIX.sub("", stem) or stem)


def media_links(text: str, base: str) -> list[str]:
    text = text.replace("\\/", "/")
    found = ABS_MEDIA.findall(text)
    found += [urljoin(base, html.unescape(link)) for link in REL_MEDIA.findall(text)]
    return [html.unescape(link) for link in found]


def fetch_text(url: str, referer: str) -> str:
    from yt_dlp.networking import Request
    try:
        with make_ydl([]) as ydl:
            response = ydl.urlopen(Request(url, headers={"Referer": referer} if referer and referer != url else {}))
            return response.read(4_000_000).decode("utf-8", "replace")
    except Exception:
        return ""


def probe_media(url: str, referer: str) -> dict | None:
    return None

def best_variants(info: dict | None, url: str, page: str, src: str, fresh: bool) -> dict | None:
    cache = info_cache_file(f"variants|{url}|{src}")
    if not fresh and not AUTHENTICATED:
        try:
            if time.time() - cache.stat().st_mtime < INFO_TTL:
                STATS["info_cache"] = True
                return json.loads(cache.read_text("utf-8"))
        except (OSError, ValueError):
            pass
    referer = page or url
    candidates = [src] if src else []
    for f in (info or {}).get("formats") or ([info] if info and info.get("url") else []):
        candidates.append(f.get("url") or "")
    if not re.search(rf"\.{MEDIA_EXT}$", urlsplit(url).path, re.I):
        candidates += media_links(fetch_text(url, page), url)
    unique = []
    for link in candidates:
        try:
            link = validate_url(link)
        except ValueError:
            continue
        if link not in unique:
            unique.append(link)
    if src:
        anchor = media_token(src)
        unique = [u for u in unique if media_token(u) == anchor] or [src]
    elif info and not is_playlist(info):
        own = {media_token(f.get("url") or "") for f in info.get("formats") or [info] if f.get("url")}
        unique = [u for u in unique if media_token(u) in own] or unique
    unique = unique[:16]
    with ThreadPoolExecutor(max_workers=6) as pool:
        probes = list(pool.map(lambda u: probe_media(u, referer), unique))
    groups: dict[str, list[dict]] = {}
    for i, (link, probe) in enumerate(zip(unique, probes)):
        check_cancel()
        if not probe:
            continue
        ext = urlsplit(link).path.rsplit(".", 1)[-1].lower()
        groups.setdefault(media_token(link), []).append({
            "format_id": f"{ext}-{probe['height'] or 0}p-{i}", "url": link, "ext": ext,
            "protocol": "https" if link.startswith("https") else "http",
            "http_headers": {"Referer": referer, "User-Agent": BROWSER_UA}, **probe})
    if not groups:
        return None
    title = (info or {}).get("title") if info and not is_playlist(info) else None
    if len(groups) > 1:
        entries = []
        for formats in groups.values():
            best = max(formats, key=lambda f: ((f["height"] or 0), f["filesize"] or 0))
            entries.append({"_type": "url", "url": best["url"], "title": unquote(best["url"].rsplit("/", 1)[-1]),
                            "duration": best.get("duration")})
        result = {"_type": "playlist", "id": "page", "title": (info or {}).get("title") or url,
                  "webpage_url": url, "extractor_key": "Generic", "extractor": "generic", "entries": entries}
    else:
        token, formats = next(iter(groups.items()))
        name = unquote(formats[0]["url"].rsplit("/", 1)[-1]).rsplit(".", 1)[0]
        result = {"_type": "video", "id": re.sub(r"[^\w-]", "", token)[:20] or "video", "title": title or name,
                  "webpage_url": url, "extractor_key": "Generic", "extractor": "generic", "formats": formats,
                  "duration": max((f.get("duration") or 0) for f in formats) or None}
    STATS["info_cache"] = False
    cache_json(cache, result)
    return result


def resolve_target(req: dict, progress: Progress) -> tuple[dict, str, str]:
    url, page, src = req["url"], req["page_url"], req["src_url"]
    fresh = bool(req.get("fresh"))
    progress("resolve", None, T("Analyzing the video"), force=True)
    try:
        info = resolve_info(url, page, fresh=fresh)
    except Cancelled:
        raise
    except Exception:
        if not src or src == url:
            raise
        info = None
    if info is not None and is_playlist(info) and not src:
        return info, url, page
    if info is not None and not is_playlist(info) and (not is_generic(info) or is_manifest(info)):
        return info, url, page
    progress("resolve", None, T("Looking for the original version"), force=True)
    variants = best_variants(info, url, page, src if src != url else "", fresh)
    if variants:
        return variants, url, page
    if info is None:
        return resolve_info(src, url), src, url
    return info, url, page


def run_with_info(ydl, info: dict, url: str):
    try:
        return ydl.process_ie_result(copy.deepcopy(info), download=True)
    except Cancelled:
        raise
    except Exception:
        check_cancel()
        home = (ydl.params.get("paths") or {}).get("home")
        for child in Path(home).iterdir() if home else ():
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        return ydl.extract_info(info.get("webpage_url") or url, download=True)


def downloaded_file(result, folder: Path) -> Path:
    for item in (result or {}).get("requested_downloads") or []:
        path = Path(item.get("filepath") or "")
        if path.is_file():
            return path
    return get_media_file(folder)


def download_hooks(progress: Progress):
    def hook(d: dict) -> None:
        check_cancel()
        if d.get("status") != "downloading":
            return
        info = d.get("info_dict") or {}
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        percent = d.get("downloaded_bytes", 0) * 100 / total if total else None
        detail = T("Downloading audio") if info.get("vcodec") == "none" else T("Downloading video")
        if d.get("speed"):
            detail += f" · {human_bytes(d['speed'])}/s"
        if d.get("eta") is not None:
            detail += f" · {fmt_clock(d['eta'])}"
        progress("download", percent, detail)


    names = {"Merger": T("Merging video and audio"), "ExtractAudio": T("Extracting audio"),
             "VideoRemuxer": T("Changing container"), "Metadata": T("Adding metadata"),
             "EmbedSubtitle": T("Embedding subtitles")}

    def pp_hook(d: dict) -> None:
        check_cancel()
        name = names.get(d.get("postprocessor"))
        if d.get("status") == "started" and name:
            progress("postprocess", None, name, force=True)

    return [hook], [pp_hook]


CODECS = {"avc1": "H.264", "h264": "H.264", "vp09": "VP9", "vp9": "VP9", "av01": "AV1", "hev1": "H.265",
          "hvc1": "H.265", "h265": "H.265", "vp8": "VP8", "mp4a": "AAC", "aac": "AAC", "opus": "Opus",
          "vorbis": "Vorbis", "mp3": "MP3", "flac": "FLAC", "ac-3": "AC-3", "ec-3": "E-AC-3"}


def codec_name(value) -> str:
    if not value or value == "none":
        return ""
    return CODECS.get(str(value).split(".")[0].lower(), str(value).split(".")[0])


def size_of(f: dict) -> tuple[int | None, bool]:
    if f.get("filesize"):
        return int(f["filesize"]), False
    if f.get("filesize_approx"):
        return int(f["filesize_approx"]), True
    return None, False


LANG_TAG = re.compile(r"^[a-z]{2,3}(?:-(?:[A-Z]{2}|\d{3}|[A-Z][a-z]{3}))?(?=-|$)")


def lang_tag(key: str) -> str:
    match = LANG_TAG.match(key or "")
    return match.group(0) if match else key


def subtitle_tracks(info: dict) -> tuple[dict, dict, str]:
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k not in SKIP_SUBS and v}
    every = info.get("automatic_captions") or {}
    base = {k for k in every if "-" not in k}

    auto = {k: v for k, v in every.items() if v and not ("-" in k and k.rsplit("-", 1)[1] in base)}

    orig_tags = {lang_tag(k[:-5]) for k in every if k.endswith("-orig")}
    if orig_tags:
        auto = {k: v for k, v in auto.items() if lang_tag(k) in orig_tags}
    original = str(info.get("language") or "")
    if not original:
        origs = [k[:-5] for k in auto if k.endswith("-orig")]
        plain = [k for k in auto if "-" not in k]

        sources = [k.rsplit("-", 1)[1] for k in every if "-" in k and k.rsplit("-", 1)[1] in plain]
        original = (origs[0] if origs else max(set(sources), key=sources.count) if sources
                    else plain[0] if len(plain) == 1 else "")
    return manual, auto, original


def lang_label(tracks: dict, code: str) -> str:
    name = next((t.get("name") for t in tracks.get(code) or [] if t.get("name")), "")
    return name or code


def summarize(info: dict) -> dict:
    if is_playlist(info):
        entries = []
        for e in info.get("entries") or []:
            link = e.get("url") or e.get("webpage_url") or ""
            if not str(link).startswith(("http://", "https://")):
                continue
            thumbs = e.get("thumbnails") or []
            if len(link) > MAX_URL:
                continue
            entries.append({"title": clip(e.get("title") or link, 300), "url": link, "duration": e.get("duration"),
                            "thumbnail": short_url(e.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None))})
        return {"kind": "playlist", "title": clip(info.get("title"), 300), "entries": entries[:60]}
    formats = info.get("formats") or [info]
    groups: dict[tuple, dict] = {}
    audios: dict[tuple, dict] = {}
    for f in formats:
        vcodec, acodec = f.get("vcodec"), f.get("acodec")
        if vcodec == "none" and acodec not in (None, "none"):
            key = (f.get("language") or "", codec_name(acodec), round(f.get("abr") or 0))
            if key not in audios or (f.get("abr") or 0) > (audios[key].get("abr") or 0):
                audios[key] = f
            continue
        if vcodec == "none" or f.get("ext") in ("mhtml", "jpg", "png", "webp"):
            continue
        hdr = f.get("dynamic_range") not in (None, "SDR")
        key = (f.get("height") or 0, round(f.get("fps") or 0), hdr, codec_name(vcodec))
        if key not in groups or (f.get("tbr") or 0) > (groups[key].get("tbr") or 0):
            groups[key] = f
    videos = []
    for (height, fps, hdr, vc), f in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2],
                                                                            kv[1].get("tbr") or 0), reverse=True):
        label = f"{height}p" if height else (f.get("format_note") or f.get("resolution") or "Original")
        if fps > 30:
            label += f"{fps}"
        size, approx = size_of(f)
        videos.append({"id": f.get("format_id"), "label": label + (" HDR" if hdr else ""), "height": height,
                       "fps": fps or None, "hdr": hdr, "vcodec": vc, "ext": f.get("ext"), "size": size,
                       "approx": approx, "has_audio": f.get("acodec") not in (None, "none") or
                       (f.get("acodec") is None and len(groups) == 1 and not audios)})
    langs = {k[0] for k in audios}
    audio_list = []
    for (lang, ac, abr), f in sorted(audios.items(), key=lambda kv: (kv[1].get("language_preference") or 0,
                                                                     kv[0][2]), reverse=True):
        size, approx = size_of(f)
        label = " · ".join(x for x in (lang if len(langs) > 1 else "", ac, f"{abr} kbps" if abr else "") if x)
        audio_list.append({"id": f.get("format_id"), "label": label or "Audio", "lang": lang or None,
                           "acodec": ac, "abr": abr or None, "ext": f.get("ext"), "size": size, "approx": approx})

    qualities: dict[tuple, dict] = {}
    for v in videos:
        key = (v["height"] or 0, bool(v["fps"] and v["fps"] > 30), v["hdr"])
        q = qualities.setdefault(key, {"height": key[0], "fps60": key[1], "hdr": key[2], "fps": v["fps"],
                                       "codecs": [], "size": None, "approx": False, "has_audio": False})
        if v["vcodec"] and v["vcodec"] not in q["codecs"]:
            q["codecs"].append(v["vcodec"])
        if v["size"] and (q["size"] is None or v["size"] > q["size"]):
            q["size"], q["approx"] = v["size"], v["approx"]
        q["has_audio"] = q["has_audio"] or v["has_audio"]
    quality_list = []
    known = any(k[0] for k in qualities)
    for key in sorted(qualities, reverse=True):
        if known and not key[0]:
            continue
        q = qualities[key]
        label = f"{q['height']}p" if q["height"] else "Original"
        if q["fps60"]:
            label += f" {q['fps']} fps"
        quality_list.append(dict(q, label=label + (" HDR" if q["hdr"] else "")))
    manual, auto, original = subtitle_tracks(info)
    original = lang_tag(original) if original else original

    languages, seen = [], set()
    for kind, pool in (("manual", manual), ("auto", auto)):
        for key in pool:
            tag = lang_tag(key)
            if tag not in seen and len(languages) < MAX_LANGS:
                seen.add(tag)
                languages.append({"code": tag, "name": clip(lang_label(pool, key), 80), "kind": kind})
    return {
        "kind": "video", "id": clip(info.get("id"), 200), "title": clip(info.get("title"), 300),
        "uploader": clip(author_of(info), 200) or None,
        "duration": info.get("duration"),
        "thumbnail": short_url(info.get("thumbnail")), "extractor": info.get("extractor_key"),
        "webpage_url": short_url(info.get("webpage_url")), "live": bool(info.get("is_live")),
        "qualities": quality_list, "videos": videos[:30], "audios": audio_list[:12],
        "audio_langs": sorted(lang for lang in langs if lang) if len(langs) > 1 else [],
        "audio_original": next((a["lang"] for a in audio_list if a["lang"]), None) if len(langs) > 1 else None,
        "audio_codecs": sorted({k[1] for k in audios if k[1]}),
        "has_webm": any(v["ext"] == "webm" for v in videos),
        "subtitles": {"original": original, "languages": languages,
                      "manual": [{"code": c, "name": clip(lang_label(manual, c), 80)} for c in list(manual)[:MAX_LANGS]],
                      "auto": [{"code": c, "name": clip(lang_label(auto, c), 80)} for c in list(auto)[:MAX_LANGS]]},
    }


def preset_args(req: dict) -> tuple[list[str], bool]:
    preset = req["preset"]
    audio_id = format_id(req.get("audio_id")) if req.get("audio_id") != "none" else "none"
    lang = lang_code(req.get("audio_lang"))
    audio_filter = f"[language^={lang}]" if lang else ""
    if preset == "custom":
        container = choice(req.get("container"), CONTAINERS, "auto")
        codec = choice(req.get("codec"), CODEC_SORT, "auto")
        sort = CODEC_SORT[codec] or ("vcodec:h264,acodec:aac" if container == "mp4" else "")
        video_id = format_id(req.get("video_id"))
        if video_id:
            spec = video_id if audio_id == "none" else f"{video_id}+{audio_id}" if audio_id else f"{video_id}+ba/{video_id}"
        else:
            height = req.get("height")
            if not isinstance(height, int) or isinstance(height, bool) or not 1 <= height <= 8640:
                raise ValueError(T("Invalid video quality."))
            f = (f"[height={height}]" + ("[fps>30]" if req.get("fps60") is True else "[fps<=?30]") +
                 ("[dynamic_range!=SDR]" if req.get("hdr") is True else "[dynamic_range=?SDR]"))
            spec = f"bv*{f}+ba{audio_filter}/b{f}"
            if audio_filter:
                spec += f"/bv*{f}+ba"
            spec += f"/bv*[height<=?{height}]+ba/b[height<=?{height}]"
        args = ["-f", spec] + (["-S", sort] if sort else [])
        return [*args, *CONTAINERS[container]], container == "mp4"
    args = list(PRESETS[preset])
    if preset in AUDIO_PRESETS:
        if audio_id not in ("", "none"):
            args[1] = audio_id
        elif audio_filter:
            args[1] = f"ba{audio_filter}/{args[1]}"
    return args, preset == "mp4"


def ensure_compatible_mp4(source: Path, folder: Path, progress: Progress) -> tuple[Path, str]:
    vc, ac, pix = ffprobe_codecs(source)
    video_ok = vc == "h264" and pix in {"yuv420p", "yuvj420p"}
    audio_ok = ac in ("", "aac")
    if video_ok and audio_ok and source.suffix.lower() == ".mp4":
        return source, "no re-encoding"
    out = folder / "out"
    out.mkdir(exist_ok=True)
    destination = out / (source.stem + ".mp4")
    args = ["-n", "-i", str(source), "-map", "0:V:0", "-map", "0:a:0?", "-map", "0:s?",
            "-map_metadata", "0", "-c:s", "mov_text",
            "-c:v", "copy" if video_ok else "libx264"]
    if not video_ok:
        args += ["-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "copy" if audio_ok else "aac"]
    if not audio_ok:
        args += ["-b:a", "192k"]

    args += ["-movflags", "+faststart", "-write_tmcd", "0", str(destination)]
    label = T("Re-encoding to H.264 (CPU)") if not video_ok else \
        T("Converting audio to AAC") if not audio_ok else T("Changing container to MP4")
    run_ffmpeg(args, ffprobe_duration(source), progress, "convert", label)
    note = "video re-encoded to H.264 (slower, minimal loss)" if not video_ok else \
        "audio converted to AAC" if not audio_ok else "no re-encoding"
    return destination, note


def author_of(info: dict) -> str:
    for key in ("artist", "creator", "uploader", "channel"):
        value = info.get(key)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value[:3])
        if value and str(value).strip():
            return str(value).strip()
    return ""


NAME_FIELDS = ("title", "author", "id", "date", "site")

SOUNDTRACK_SITES = ("tiktok",)
SOUNDTRACK_FIELDS = ("track", "artist", "artists", "album", "album_artist", "album_artists")


def full_title(info: dict) -> str:
    title = str(info.get("title") or "")
    description = str(info.get("description") or "")
    head = re.sub(r"(?:\.\.\.|…)$", "", title).rstrip()
    if head != title and len(head) >= 8 and description.startswith(head):
        return description
    return title


def without_soundtrack(info: dict) -> dict:
    site = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if not site.startswith(SOUNDTRACK_SITES):
        return info
    return {k: v for k, v in info.items() if k not in SOUNDTRACK_FIELDS}


TITLE_SITES = ("YouTube", "TikTok", "Twitter", "Instagram", "Vimeo", "Facebook", "Reddit", "Tumblr", "Twitch",
               "Dailymotion", "SoundCloud", "Bandcamp", "Pinterest", "Bilibili", "Imgur", "Giphy", "Streamable",
               "Rumble", "LinkedIn", "Bluesky", "Odysee", "Mixcloud")
TITLE_SITE_TAIL = re.compile(
    r"\s*(?:[|–—·•/-]\s*(?:" + "|".join(TITLE_SITES) + r")(?:\s+(?:video|videos|clip|clips|photos?))*"
    r"|\bon\s+(?:X|" + "|".join(TITLE_SITES) + r"))\s*$", re.I)


def clean_title(value: str, site: str = "") -> str:
    title = value or ""
    title = "".join("\x00" if unicodedata.category(c) in ("So", "Cs", "Co") else c for c in title)
    title = re.sub(r"[\u200d\ufe0e\ufe0f\U0001f3fb-\U0001f3ff]", "", title)
    if site.lower().startswith(("tiktok", "twitter", "x", "instagram")):
        title = re.split(r"(?<=[.!?])\s+|\s*\x00[\x00\s]*", title.strip(" \x00"), maxsplit=1)[0]
    title = re.sub(r"https?://\S+|(?<!\w)[#@][\w.-]+|\x00", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    while True:
        trimmed = TITLE_SITE_TAIL.sub("", title).rstrip()
        if trimmed == title:
            break
        title = trimmed
    return title.strip(" .!?-–|")


def templated_name(info: dict, ext: str, template) -> str | None:
    if not isinstance(template, str) or not template.strip():
        return None
    date = str(info.get("upload_date") or "")
    values = {
        "title": clean_title(full_title(info) or "Video", str(info.get("extractor_key") or "")),
        "author": author_of(info), "id": str(info.get("id") or ""),
        "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if re.fullmatch(r"\d{8}", date) else "",
        "site": str(info.get("extractor_key") or ""),
    }
    text = re.sub(r"\{(\w+)\}", lambda m: re.sub(r"\s+", " ", values.get(m.group(1), "")).strip()
                  if m.group(1) in NAME_FIELDS else "", template[:200])
    text = re.sub(r"(\s*[-–|·]\s*)+$|^(\s*[-–|·]\s*)+", "", re.sub(r"\(\s*\)|\[\s*\]", "", text)).strip()
    if not text:
        return None
    return f"{safe_name(text, 180)}.{ext.lstrip('.')}"


def display_name(info: dict, ext: str, template=None, suffix: str = "") -> str:
    info = without_soundtrack(info)
    custom = templated_name(info, ext, template)
    if custom:
        name = custom.rsplit(".", 1)[0]
    else:
        site = str(info.get("extractor_key") or info.get("extractor") or "").strip()
        title = clean_title(full_title(info), site)
        author = author_of(info).strip()
        if info.get("artist") and info.get("track"):
            name = f"{safe_name(str(info['artist']), 40)} - {safe_name(str(info['track']), 130)}"
        elif not title:
            raw_date = str(info.get("upload_date") or "")
            day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if re.fullmatch(r"\d{8}", raw_date) else date.today().isoformat()
            name = f"{safe_name(author or 'Autor', 40)} - {safe_name(site or 'Sitio', 40)} {day}"
        else:
            if title.lower().startswith(author.lower() + " - "):
                title = title[len(author) + 3:].strip() or title
            if author.lower() in title.lower():
                author = ""
            name = safe_name(title, 130) + (f" - {safe_name(author, 40)}" if author else "")
    name = name.rstrip(" .")
    if suffix:
        name += f" ({safe_name(suffix, 30)})"
    extension = re.sub(r"[^a-z0-9]", "", ext.lstrip(".").lower())[:12] or "bin"
    return f"{safe_name(name, 180)}.{extension}"


def embed_cover(media: Path, cover: Path | None) -> None:
    if not cover or not cover.is_file():
        return
    ext = media.suffix.lower()
    out = media.with_name("cover-" + media.name)
    base = [tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(media)]
    if ext in (".mp4", ".m4a", ".m4v", ".mov"):
        videos = run([tool("ffprobe"), "-v", "error", "-select_streams", "V", "-show_entries", "stream=index",
                      "-of", "csv=p=0", str(media)]).split()
        args = [*base, "-i", str(cover), "-map", "0:V?", "-map", "0:a?", "-map", "1", "-c", "copy",
                f"-disposition:v:{len(videos)}", "attached_pic", "-write_tmcd", "0", "-movflags", "+faststart"]
    elif ext == ".mkv":
        args = [*base, "-map", "0", "-c", "copy", "-attach", str(cover),
                "-metadata:s:t", "mimetype=image/jpeg", "-metadata:s:t", "filename=cover.jpg"]
    elif ext == ".mp3":
        args = [*base, "-i", str(cover), "-map", "0:a", "-map", "1", "-c", "copy", "-id3v2_version", "3",
                "-metadata:s:v", "comment=Cover (front)"]
    else:
        return
    try:
        run([*args, str(out)])
        out.replace(media)
    except (RuntimeError, OSError):
        out.unlink(missing_ok=True)


def job_download(req: dict, progress: Progress) -> dict:
    req["preset"] = choice(req.get("preset"), set(PRESETS) | {"custom"}, "best")
    args, compatible = preset_args(req)
    info, url, page = resolve_target(req, progress)
    if is_playlist(info):
        count = len(info.get("entries") or [])
        raise RuntimeError(T("The page has {count} videos: pick one in the extension panel.", count=count))
    if info.get("is_live"):
        raise RuntimeError(T("Live streams aren't supported yet."))
    check_limits(info)
    info = without_soundtrack(info)
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("https://"):
        info = {**info, "thumbnails": [{"url": thumb, "id": "cover"}]}
    progress("resolve", None, T("Preparing download"), force=True, meta={
        "title": str(info.get("title") or "")[:200], "author": author_of(info)[:100],
        "thumbnail": thumb if isinstance(thumb, str) and thumb.startswith("https://") else None})
    hooks, pp_hooks = download_hooks(progress)
    with job_tmp() as tmp:
        folder = Path(tmp)

        covers = ["--write-thumbnail", "--convert-thumbnails", "jpg", "-o", "thumbnail:cover/cover.%(ext)s"]
        argv = [*referer_args(url, page), "-P", str(folder), "-o", OUT_TEMPLATE, "--embed-metadata",
                "--embed-thumbnail", "--parse-metadata", "%(webpage_url)s:%(meta_comment)s",
                "--postprocessor-args", "FFmpegMetadata+ffmpeg:-id3v2_version 3", "--embed-chapters", *args]
        langs = lang_list(req.get("langs"))
        if req.get("embed_subs") is True and langs and req["preset"] not in AUDIO_PRESETS | {"webm"}:

            argv += ["--write-subs", "--sub-langs", ",".join(f"{lang}.*" for lang in langs), "--embed-subs"]
        try:
            with make_ydl([*argv, *covers], hooks, pp_hooks) as ydl:
                result = run_with_info(ydl, info, url)
        except Exception as exc:
            if "Conversion failed" not in str(exc):
                raise
            for child in folder.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
            with make_ydl(argv, hooks, pp_hooks) as ydl:
                result = run_with_info(ydl, info, url)
        source = downloaded_file(result, folder)
        note = ""
        if compatible:
            source, note = ensure_compatible_mp4(source, folder, progress)
        if req["preset"] == "webm" and source.suffix.lower() != ".webm":
            raise RuntimeError(T("There's no WebM version of this video."))
        if compatible and note != "no re-encoding":
            progress("finish", None, T("Adding cover art"))
            embed_cover(source, folder / "cover" / "cover.jpg")
        verify_output(source, req["preset"] in AUDIO_PRESETS)
        variant = f"{req['height']}p" if req["preset"] == "custom" and isinstance(req.get("height"), int) else ""
        if req.get("audio_lang"):
            variant = f"audio {lang_code(req['audio_lang'])}"
        destination = move_to_downloads(source, display_name(info, source.suffix, req.get("name_template"), variant),
                                        req.get("subfolder"))
    message = T("Saved to Downloads: {name}", name=destination.name) + (f" ({T(note)})" if note and note != "no re-encoding" else "")
    return {"message": message, "path": str(destination), "title": info.get("title"),
            "extractor": info.get("extractor_key"), "duration": info.get("duration")}


TIMESTAMP = re.compile(r"((?:\d+:)?\d\d:\d\d[.,]\d{3})\s+-->\s+((?:\d+:)?\d\d:\d\d[.,]\d{3})")
TAG = re.compile(r"<[^>]*>")


def to_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def clean_text(value: str) -> str:
    value = TAG.sub("", value)
    value = html.unescape(value).replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", value).strip()


def parse_cues(raw: str) -> list[tuple[float, float, str]]:
    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.splitlines()
        where = next((i for i, s in enumerate(lines) if TIMESTAMP.search(s)), None)
        if where is None:
            continue
        match = TIMESTAMP.search(lines[where])
        text = clean_text(" ".join(lines[where + 1:]))
        if text:
            cues.append((to_seconds(match.group(1)), to_seconds(match.group(2)), text))
    return cues


def words_merge(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    output: list[tuple[float, float, str]] = []
    tail: list[str] = []
    for start, end, item in cues:
        words = item.split()
        if not words:
            continue
        if len(words) <= len(tail) and words == tail[-len(words):]:
            continue
        overlap = 0
        for n in range(min(len(words), len(tail)), 0, -1):
            if tail[-n:] == words[:n]:
                overlap = n
                break
        new = words[overlap:]
        if not new:
            continue
        tail = (tail + new)[-120:]
        output.append((start, end, " ".join(new)))
    return output


def render_text(cues) -> str:
    paragraphs, current = [], ""
    for _, _, phrase in cues:
        current = f"{current} {phrase}".strip()
        if (len(current) >= 500 and re.search(r"[.!?…]$", current)) or len(current) >= 1500:
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


def render_timed(cues) -> str:
    return "\n".join(f"[{fmt_clock(start)}] {phrase}" for start, _, phrase in cues)


def _stamp(seconds: float, sep: str) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{sep}{ms % 1000:03d}"


def _timed_cues(cues):
    for i, (start, end, phrase) in enumerate(cues):
        nxt = cues[i + 1][0] if i + 1 < len(cues) else None
        stop = min(end, nxt) if nxt is not None and nxt > start else end
        yield start, max(stop, start + 0.5), phrase


def render_srt(cues) -> str:
    return "\n".join(f"{i}\n{_stamp(s, ',')} --> {_stamp(e, ',')}\n{p}\n"
                     for i, (s, e, p) in enumerate(_timed_cues(cues), 1))


def render_vtt(cues) -> str:
    return "WEBVTT\n\n" + "\n".join(f"{_stamp(s, '.')} --> {_stamp(e, '.')}\n{p}\n" for s, e, p in _timed_cues(cues))


def match_lang(code: str, pool: dict) -> str | None:
    if not code:
        return None
    for candidate in (f"{code}-orig", code):
        if candidate in pool:
            return candidate
    tagged = next((k for k in pool if lang_tag(k).lower() == code.lower()), None)
    if tagged:
        return tagged
    primary = code.split("-")[0].lower()
    return next((k for k in pool if k.split("-")[0].lower() == primary), None)


def pick_track(info: dict, requested: str, prefs: list[str]) -> tuple[str, str] | None:
    manual, auto, original = subtitle_tracks(info)
    langs = [requested] if requested else list(dict.fromkeys([original, *prefs]))
    for kind, pool in (("manual", manual), ("auto", auto)):
        for lang in langs:
            found = match_lang(lang, pool)
            if found:
                return found, kind
    if requested:
        return None
    if manual:
        return next(iter(manual)), "manual"
    if auto:
        return next(iter(auto)), "auto"
    return None


def best_subtitle(path: Path) -> Path:
    subs = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".vtt", ".srt")]
    if not subs:
        raise RuntimeError(T("This video has no downloadable subtitles."))
    def key(p: Path) -> tuple[int, int]:
        name = p.name.lower()
        lang = re.search(r"\.([a-z]{2}(?:[-_][a-z0-9]+)?)\.(vtt|srt)$", name)
        code = lang.group(1) if lang else ""
        preference = 0 if code == "es" else 1 if code.startswith("es-") else 2 if code == "en" else 3 if code.startswith("en-") else 4
        return (preference, 0 if p.suffix == ".vtt" else 1)
    return sorted(subs, key=key)[0]


def tx_cache_file(*parts: str) -> Path:
    return CACHE / "tx" / (hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32] + ".json")


def cached_cues(path: Path):
    if AUTHENTICATED:
        return None
    try:
        return [tuple(c) for c in json.loads(path.read_text("utf-8"))]
    except (OSError, ValueError):
        return None


def subtitle_cues(info: dict, url: str, page: str, code: str, kind: str, progress: Progress):
    key = tx_cache_file(info.get("webpage_url") or url, "sub", kind, code)
    STATS["source"] = f"{kind}:{code}"
    cues = cached_cues(key)
    STATS["tx_cache"] = cues is not None
    if cues is not None:
        return cues
    progress("subtitles", None, T("Downloading subtitles ({code})", code=code), force=True)
    with job_tmp() as tmp:
        folder = Path(tmp)
        argv = [*referer_args(url, page), "--skip-download",
                "--write-subs" if kind == "manual" else "--write-auto-subs",
                "--sub-langs", re.escape(code), "--sub-format", "vtt/srt/best",
                *(["--convert-subs", "vtt"] if shutil.which(tool("ffmpeg")) else []),
                "-P", str(folder), "-o", "sub.%(ext)s"]
        with make_ydl(argv) as ydl:
            run_with_info(ydl, info, url)
        source = best_subtitle(folder)
        cues = words_merge(parse_cues(source.read_text(encoding="utf-8-sig", errors="replace")))
    if not cues:
        raise RuntimeError(T("Couldn't extract readable text from the subtitles."))
    cache_json(key, cues)
    return cues


def asr_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def asr_cues(info: dict, url: str, page: str, lang: str, model_name: str, progress: Progress):
    key = tx_cache_file(info.get("webpage_url") or url, "asr", model_name, lang or "auto")
    STATS["source"] = f"asr:{model_name}"
    cues = cached_cues(key)
    STATS["tx_cache"] = cues is not None
    if cues is not None:
        return cues
    if not asr_available():
        raise RuntimeError(T("This video has no subtitles and offline transcription isn't installed. It's optional: the install guide explains how to add it."))
    from faster_whisper import WhisperModel
    import numpy as np
    import wave
    hooks, pp_hooks = download_hooks(progress)
    with job_tmp() as tmp:
        folder = Path(tmp)
        argv = [*referer_args(url, page), "-f", "ba[abr<=?160]/ba/b", "-P", str(folder), "-o", "audio.%(ext)s"]
        with make_ydl(argv, hooks, pp_hooks) as ydl:
            run_with_info(ydl, info, url)
        media = get_media_file(folder)
        audio = folder / "speech.wav"
        run_ffmpeg(["-n", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)],
                   ffprobe_duration(media), progress, "asr", T("Preparing audio"))


        with wave.open(str(audio), "rb") as wav:
            samples = np.frombuffer(wav.readframes(wav.getnframes()), np.int16).astype(np.float32) / 32768.0
    total = max(len(samples) / 16000, 1.0)
    progress("asr", 0, T("Loading model “{model}” (downloaded the first time)", model=model_name), force=True)
    threads = max(1, min(8, (os.cpu_count() or 4) // 2))
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
    segments, _ = model.transcribe(samples, language=lang.split("-")[0].lower() if lang else None,
                                   beam_size=5, vad_filter=True)
    cues = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            cues.append((round(segment.start, 2), round(segment.end, 2), text))
        progress("asr", segment.end / total * 100, T("Transcribing locally · {done} of {total}", done=fmt_clock(segment.end), total=fmt_clock(total)))
    if not cues:
        raise RuntimeError(T("No recognizable speech was detected."))
    cache_json(key, cues)
    return cues


def job_transcript(req: dict, progress: Progress) -> dict:
    mode = choice(req.get("mode"), {"copy", "export", "summary", "translate"}, "copy")
    export = choice(req.get("format"), EXPORT_FORMATS, "txt")
    source = choice(req.get("source"), SOURCES, "auto")
    model = choice(req.get("model"), ASR_MODELS, "small")
    lang = lang_code(req.get("lang"))
    prefs = lang_list(req.get("langs"))
    timed = bool(req.get("timed"))
    asr_fallback = req.get("asr_fallback", True) is not False
    info, url, page = resolve_target(req, progress)
    if is_playlist(info):
        raise RuntimeError(T("The page has several videos: pick one in the extension panel."))
    track = None if source == "asr" else pick_track(info, lang, prefs)
    if track:
        code, kind = track
        cues = subtitle_cues(info, url, page, code, kind, progress)
        origin = T("subtitles ({code})" if kind == "manual" else "automatic subtitles ({code})", code=code)
    elif source == "subs" or not asr_fallback:
        raise RuntimeError(T("This video has no subtitles in “{lang}”.", lang=lang) if lang
                           else T("This video has no subtitles."))
    else:
        check_limits(info)
        cues = asr_cues(info, url, page, lang, model, progress)
        origin = T("local transcription ({model}, may contain errors)", model=model)
    if mode in ("summary", "translate"):
        return ai_result(mode, req, info, cues, progress)
    if mode == "copy":
        text = render_timed(cues) if timed else render_text(cues)
        try:
            clipboard_set(text)
        except ClipboardBusy:
            path = save_transcript(info, text, "txt", T("transcript"), req.get("subfolder"))
            return {"message": T("Another app is locking the clipboard; transcript saved to Downloads: {name}.",
                                 name=path.name), "path": str(path), "title": info.get("title"),
                    "extractor": info.get("extractor_key"), "duration": info.get("duration")}
        return {"message": T("Transcript copied: {chars} characters from {origin}. Paste with Ctrl+V.", chars=f"{len(text):,}",
                               origin=origin),
                "title": info.get("title"), "chars": len(text), "extractor": info.get("extractor_key"),
                "duration": info.get("duration")}
    text = {"txt": render_timed(cues) if timed else render_text(cues), "srt": render_srt(cues),
            "vtt": render_vtt(cues)}[export]
    label = T("subtitles") + (f" {track[0]}" if track else "") if export in ("srt", "vtt") else T("transcript")
    destination = save_transcript(info, text, export, label, req.get("subfolder"))
    return {"message": T("Transcript saved to Downloads: {name} ({origin}).", name=destination.name, origin=origin),
            "path": str(destination), "title": info.get("title"), "extractor": info.get("extractor_key"),
            "duration": info.get("duration")}


def load_ai():
    import importlib.util
    if "tomo_ai" not in sys.modules:
        spec = importlib.util.spec_from_file_location("tomo_ai", Path(__file__).with_name("ai.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["tomo_ai"] = module
    return sys.modules["tomo_ai"]


def ai_result(mode: str, req: dict, info: dict, cues, progress: Progress) -> dict:
    ai = load_ai()
    cfg = ai.config(req.get("ai"), T)
    target = lang_code(req.get("target")) or "en"
    name = re.sub(r"[\x00-\x1f<>]", "", str(req.get("target_name") or ""))[:40].strip()
    language = f"{name} ({target})" if name else target
    base = {"title": info.get("title"), "extractor": info.get("extractor_key"), "duration": info.get("duration")}
    if mode == "summary":
        progress("ai", None, T("Summarizing with AI ({model})", model=cfg["model"]), force=True)
        text = ai.summarize(cfg, render_timed(cues), str(info.get("title") or ""), language, T)
        destination = save_transcript(info, text, "md", T("summary"), req.get("subfolder"))
        try:
            clipboard_set(text)
            message = T("Summary copied and saved to Downloads: {name}", name=destination.name)
        except (ClipboardBusy, RuntimeError):
            message = T("Summary saved to Downloads: {name}", name=destination.name)
        return {"message": message, "path": str(destination), **base}
    progress("ai", 0, T("Translating with AI ({model})", model=cfg["model"]), force=True)
    lines = ai.translate_lines(cfg, [text for _, _, text in cues], language, T,
                               lambda pct: progress("ai", pct, T("Translating with AI ({model})", model=cfg["model"])))
    translated = [(start, end, text) for (start, end, _), text in zip(cues, lines)]
    destination = save_transcript(info, render_srt(translated), "srt", f"{T('subtitles')} {target}", req.get("subfolder"))
    return {"message": T("Translated subtitles saved to Downloads: {name}", name=destination.name),
            "path": str(destination), **base}


class ClipboardBusy(RuntimeError):
    pass


class _JobTmp(tempfile.TemporaryDirectory):

    def __init__(self) -> None:
        super().__init__(prefix="job-", dir=TMP_ROOT)
        _JOB_DIRS.add(self.name)

    def cleanup(self) -> None:
        _JOB_DIRS.discard(self.name)
        super().cleanup()


def job_tmp():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _JobTmp()


def save_transcript(info: dict, text: str, ext: str, suffix: str = "", subfolder=None) -> Path:
    with job_tmp() as tmp:
        name = display_name(info, ext, suffix=suffix)
        temp = Path(tmp) / name
        temp.write_text(text + "\n", encoding="utf-8")
        return move_to_downloads(temp, subfolder=subfolder)


def clipboard_set(text: str) -> None:
    if not sysdep.IS_WINDOWS:
        try:
            sysdep.clipboard_set_posix(text)
        except Exception as exc:
            raise RuntimeError(T("Couldn't copy the transcript.")) from exc
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    user = ctypes.windll.user32
    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    raw = (text + "\x00").encode("utf-16-le")
    memory = k32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
    if not memory:
        raise RuntimeError(T("Couldn't allocate memory for the clipboard."))
    ptr = k32.GlobalLock(memory)
    if not ptr:
        k32.GlobalFree(memory)
        raise RuntimeError(T("Couldn't prepare the clipboard."))
    ctypes.memmove(ptr, raw, len(raw))
    k32.GlobalUnlock(memory)
    opened = False
    try:

        for attempt in range(20):
            if user.OpenClipboard(None):
                opened = True
                break
            time.sleep(min(0.05 * (attempt + 1), 0.3))
        if not opened:
            raise ClipboardBusy(T("Another app is using the clipboard."))
        if not user.EmptyClipboard() or not user.SetClipboardData(CF_UNICODETEXT, memory):
            raise RuntimeError(T("Couldn't copy the transcript."))
        memory = None
    finally:
        if opened:
            user.CloseClipboard()
        if memory:
            k32.GlobalFree(memory)


def job_resolve(req: dict, progress: Progress) -> dict:
    info, url, _ = resolve_target(req, progress)
    return {"message": "ok", "info": summarize(info), "target": url}


def job_ping(req: dict, progress: Progress) -> dict:
    from importlib import metadata
    def version(name: str):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None
    return {"message": "ok", "version": BACKEND_VERSION, "protocol": PROTOCOL,
            "ytdlp": version("yt-dlp"), "asr": asr_available(), "faster_whisper": version("faster-whisper"),
            "ffmpeg": os.path.isabs(tool("ffmpeg")), "js_runtime": bool(js_runtime_args()),
            "downloads": str(downloads_folder())}


def job_reveal(req: dict, progress: Progress) -> dict:
    path = allowed_output(req.get("path"))
    sysdep.reveal(path)
    return {"message": "ok"}


def job_open(req: dict, progress: Progress) -> dict:
    sysdep.open_path(allowed_output(req.get("path")))
    return {"message": "ok"}


def job_open_downloads(req: dict, progress: Progress) -> dict:
    sysdep.open_path(downloads_folder())
    return {"message": "ok"}


def job_clear_cache(req: dict, progress: Progress) -> dict:
    shutil.rmtree(CACHE, ignore_errors=True)
    return {"message": T("Local cache cleared.")}


def job_clear_history(req: dict, progress: Progress) -> dict:
    with history_lock():
        write_json_atomic(HISTORY, [])
        METRICS.unlink(missing_ok=True)
        METRICS.with_suffix(".1.jsonl").unlink(missing_ok=True)
    return {"ok": True}


IMAGE_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MAX_PIXELS = 40_000_000
IMAGE_MAX_SIDE = 16_384


def image_dimensions(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 4 < len(data):
            if data[pos] != 0xff:
                raise ValueError("Invalid JPEG")
            marker = data[pos + 1]
            pos += 2
            if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
                continue
            size = int.from_bytes(data[pos:pos + 2], "big")
            if size < 2 or pos + size > len(data):
                break
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                return struct.unpack(">HH", data[pos + 3:pos + 7])[::-1]
            pos += size
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X" and len(data) >= 30:
            return (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
        if data[12:16] == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            return ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
        if data[12:16] == b"VP8 " and len(data) >= 30:
            return (int.from_bytes(data[26:28], "little") & 0x3fff,
                    int.from_bytes(data[28:30], "little") & 0x3fff)
    raise ValueError("Unsupported image dimensions")


def job_save_image(req: dict, progress: Progress) -> dict:
    url = req["url"]
    filename = safe_name(req.get("filename") or "Image", 150)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        SafeHTTPHandler(), SafeHTTPSHandler(), SafeRedirectHandler())
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity", "User-Agent": BROWSER_UA})
    with opener.open(request, timeout=30) as response:
        mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(mime)
        if response.status != 200 or not extension:
            raise ValueError("The URL is not an image")
        length = response.headers.get("Content-Length")
        if length and int(length) > IMAGE_MAX_BYTES:
            raise ValueError("Image is too large")
        with tempfile.TemporaryFile() as staged:
            total = 0
            header = bytearray()
            while chunk := response.read(64 * 1024):
                total += len(chunk)
                if total > IMAGE_MAX_BYTES:
                    raise ValueError("Image is too large")
                if len(header) < 256 * 1024:
                    header.extend(chunk[:256 * 1024 - len(header)])
                staged.write(chunk)
            width, height = image_dimensions(header)
            if not (0 < width <= IMAGE_MAX_SIDE and 0 < height <= IMAGE_MAX_SIDE
                    and width * height <= IMAGE_MAX_PIXELS):
                raise ValueError("Image dimensions are too large")
            stem = Path(filename).stem if Path(filename).suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp") else filename
            folder = downloads_folder() / safe_subfolder(req.get("subfolder"))
            folder.mkdir(parents=True, exist_ok=True)
            destination = unique_destination(folder / (stem + extension))
            staged.seek(0)
            with destination.open("xb") as output:
                shutil.copyfileobj(staged, output)
    return {"message": "ok", "path": str(destination)}


def job_update_ytdlp(req: dict, progress: Progress) -> dict:
    from importlib import metadata
    before = metadata.version("yt-dlp")
    pinned = "2026.10.26"
    try:
        newer = tuple(map(int, before.split("."))) > tuple(map(int, pinned.split(".")))
    except ValueError:
        raise RuntimeError("Unrecognized yt-dlp version") from None
    if newer:
        raise RuntimeError("Refusing to downgrade yt-dlp")
    lock = Path(__file__).with_name("requirements.lock")
    if not lock.is_file():
        raise RuntimeError("The verified dependency lock is missing")
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
           "--require-hashes", "--only-binary=:all:", "--upgrade", "-r", str(lock)]
    progress("update", None, T("Updating yt-dlp"), force=True)
    try:
        run(pip, timeout=900)
        after = run([sys.executable, "-I", "-m", "yt_dlp", "--version"], timeout=120).strip()
    except RuntimeError as exc:
        raise RuntimeError(T("The update failed ({error}).", error=exc)) from exc
    message = (T("yt-dlp was already up to date ({version}).", version=after) if after == before
               else T("yt-dlp updated: {old} → {new}.", old=before, new=after))
    return {"message": message, "ytdlp": after}


def job_ai_test(req: dict, progress: Progress) -> dict:
    ai = load_ai()
    cfg = ai.config(req.get("ai"), T)
    ai.complete(cfg, "Reply with the single word OK.", "OK?", T, max_tokens=16)
    return {"message": T("Connected to {model}.", model=cfg["model"])}


HANDLERS = {"ping": job_ping, "resolve": job_resolve, "download": job_download,
            "save-image": job_save_image,
            "transcript": job_transcript, "reveal": job_reveal, "open": job_open,
            "open-downloads": job_open_downloads, "clear-cache": job_clear_cache,
            "clear-history": job_clear_history,
            "update-ytdlp": job_update_ytdlp, "ai-test": job_ai_test}
NEEDS_URL = {"resolve", "download", "transcript", "save-image"}
HEAVY = {"download", "transcript", "update-ytdlp", "save-image"}


def normalize(request: dict) -> tuple[str, dict]:
    action = request.get("action")
    if not isinstance(action, str):
        raise ValueError(T("Unknown action."))
    req = dict(request)
    if action in LEGACY:
        action, extra = LEGACY[action]
        req.update(extra)
    if action not in HANDLERS:
        raise ValueError(T("Unknown action."))
    if "record" in req and not isinstance(req["record"], bool):
        raise ValueError(T("The record parameter must be a boolean."))
    if action in NEEDS_URL:
        req["url"] = validate_url(req.get("url", ""))
        req["page_url"] = optional_url(req.get("page_url"))
        req["src_url"] = optional_url(req.get("src_url"))
    return action, req


COOKIE_LINE = re.compile(r"(?:#HttpOnly_)?(\.?[\w.-]+)\t(?:TRUE|FALSE)\t/[^\t\r\n]*\t(?:TRUE|FALSE)\t\d+\t[^\t\r\n]+\t[^\t\r\n]*")
SESSION_FILE: Path | None = None


def scoped_cookies(raw, urls: list[str]) -> str:
    hosts = {urlsplit(u).hostname for u in urls if u}
    lines = []
    for line in (raw if isinstance(raw, str) and len(raw) <= 262144 else "").splitlines()[:600]:
        match = COOKIE_LINE.fullmatch(line)
        domain = match[1].lstrip(".").lower() if match else ""
        if domain and any(h == domain or h.endswith("." + domain) for h in hosts if h):
            lines.append(line)
    return "\n".join(["# Netscape HTTP Cookie File", *lines]) + "\n" if lines else ""


def process(action: str, req: dict, progress: Progress) -> dict:
    global SESSION_FILE, AUTHENTICATED, RECORD_HISTORY
    AUTHENTICATED = "cookies" in req
    RECORD_HISTORY = req.get("record", True) is not False
    session_dir = None
    SESSION_FILE = None
    try:
        contain_children(low_priority=action in HEAVY)
        sweep_stale_tmp()
        cookies = scoped_cookies(req.pop("cookies", ""), [req.get("url"), req.get("page_url"), req.get("src_url")]) \
            if action in NEEDS_URL else ""
        if cookies:
            private_root = Path.home() / "AppData" / "Local" / "Tomo" / "sessions" if os.name == "nt" else TMP_ROOT
            private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            session_dir = Path(tempfile.mkdtemp(prefix="session-", dir=private_root))
            fd, name = tempfile.mkstemp(prefix="cookies-", suffix=".txt", dir=session_dir)
            SESSION_FILE = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(cookies)
        return HANDLERS[action](req, progress)
    finally:
        if SESSION_FILE:
            try:
                SESSION_FILE.unlink(missing_ok=True)
            except OSError:
                pass
        SESSION_FILE = None
        if session_dir:
            shutil.rmtree(session_dir, ignore_errors=True)
        if action in NEEDS_URL:
            limit = req.get("cache_mb")
            try:
                prune_cache(limit if isinstance(limit, int) and 0 <= limit <= 10000 else 200)
            except OSError:
                pass
        AUTHENTICATED = False
        RECORD_HISTORY = True


def main() -> None:
    global _IN
    sys.stdout = sys.stderr


    _IN = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    sys.stdin = open(os.devnull)
    sysdep.release_stdin()
    try:
        request = receive()
    except Exception as exc:
        send({"status": "error", "message": str(exc)[:300]})
        return
    set_lang(request.get("ui_lang") if isinstance(request, dict) else None)
    send({"status": "started", "version": BACKEND_VERSION, "protocol": PROTOCOL})
    try:
        action, req = normalize(request)
    except Exception as exc:
        send({"status": "error", "message": friendly(exc)})
        return
    threading.Thread(target=watch_input, daemon=True).start()
    progress = Progress()
    try:
        result = process(action, req, progress)
        FINISHED.set()
        send({"status": "done", **result})
        record_metrics(action, req, progress, "done", result)
    except Cancelled:
        FINISHED.set()
        send({"status": "cancelled", "message": T("Cancelled.")})
        record_metrics(action, req, progress, "cancelled", {})
    except Exception as exc:
        FINISHED.set()
        if CANCEL.is_set():
            send({"status": "cancelled", "message": T("Cancelled.")})
            record_metrics(action, req, progress, "cancelled", {})
        else:
            message = friendly(exc)
            send({"status": "error", "message": message, "kind": error_kind(exc)})
            record_metrics(action, req, progress, "error", {}, message)


if __name__ == "__main__":
    main()


    sys.stderr.flush()
    os._exit(0)
