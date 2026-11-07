#!/usr/bin/env bash
set -euo pipefail
printf 'Experimental macOS/Linux installer; not supported for first-time users.\n' >&2

src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
case $(uname -s) in
  Darwin)
    dst="$HOME/Library/Application Support/Tomo"
    registry="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts/local.tomo.json"
    chromium_base="$HOME/Library/Application Support"
    chromium_dirs=("Google/Chrome" "Chromium" "Microsoft Edge" "BraveSoftware/Brave-Browser")
    ;;
  Linux)
    data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
    case $data_home in /*) ;; *) printf 'XDG_DATA_HOME must be an absolute path.\n' >&2; exit 1 ;; esac
    dst="$data_home/Tomo"
    registry="$HOME/.mozilla/native-messaging-hosts/local.tomo.json"
    chromium_base="$HOME/.config"
    chromium_dirs=("google-chrome" "chromium" "microsoft-edge" "BraveSoftware/Brave-Browser")
    ;;
  *) printf 'This installer only supports macOS and Linux.\n' >&2; exit 1 ;;
esac

asr=0
python_requested=
while (($#)); do
  case $1 in
    --asr) asr=1; shift ;;
    --python)
      (($# >= 2)) || { printf 'Missing path for --python.\n' >&2; exit 1; }
      python_requested=$2; shift 2 ;;
    *) printf 'Unknown option: %s\nUsage: %s [--asr] [--python PATH]\n' "$1" "$0" >&2; exit 1 ;;
  esac
done

python_cmd=
if [[ -n $python_requested ]]; then
  python_cmd=$(command -v -- "$python_requested" || true)
  [[ -n $python_cmd ]] || { printf 'The given Python was not found.\n' >&2; exit 1; }
else
  for candidate in python3 python; do
    path=$(command -v -- "$candidate" || true)
    if [[ -n $path ]] && "$path" -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      python_cmd=$path
      break
    fi
  done
fi
[[ -n $python_cmd ]] && "$python_cmd" -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || {
  printf 'Python 3.10 or later is required.\n' >&2; exit 1;
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | cut -d ' ' -f 1
  else
    shasum -a 256 -- "$1" | cut -d ' ' -f 1
  fi
}
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
  printf 'sha256sum or shasum is required.\n' >&2; exit 1
fi

managed=(host.py sysdep.py ai.py locales.json host.sh config.json native-host.json install.json)
for file in host.py sysdep.py ai.py locales.json; do
  [[ -f "$src/backend/$file" ]] || { printf 'Missing backend/%s.\n' "$file" >&2; exit 1; }
done
mkdir -p -- "$dst"
backup=$(mktemp -d "$dst/.backup.XXXXXXXX")
had_registry=0
had_venv=0
[[ -d $dst/.venv ]] && had_venv=1
if [[ -f $registry ]]; then
  cp -p -- "$registry" "$backup/registry.json"
  had_registry=1
fi
for file in "${managed[@]}"; do
  if [[ -e $dst/$file ]]; then cp -p -- "$dst/$file" "$backup/$file"; fi
done
committed=0
rollback() {
  if ((committed)); then rm -rf -- "$backup"; return; fi
  for file in "${managed[@]}"; do
    if [[ -e $backup/$file ]]; then
      cp -p -- "$backup/$file" "$dst/$file"
    else
      rm -f -- "$dst/$file"
    fi
  done
  if [[ -d $backup/venv ]]; then
    rm -rf -- "$dst/.venv"
    mv -- "$backup/venv" "$dst/.venv"
  elif [[ $had_venv == 0 ]]; then
    rm -rf -- "$dst/.venv"
  fi
  if ((had_registry)); then
    mkdir -p -- "$(dirname -- "$registry")"
    cp -p -- "$backup/registry.json" "$registry"
  else
    rm -f -- "$registry"
  fi
  rm -rf -- "$backup"
  printf 'Install failed; the previous files were restored.\n' >&2
}
trap rollback EXIT

for file in host.py sysdep.py ai.py locales.json; do
  cp -- "$src/backend/$file" "$dst/$file"
done
cat > "$dst/host.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec "$here/.venv/bin/python" -I "$here/host.py" "$@"
SH
chmod +x -- "$dst/host.sh"

venv_python="$dst/.venv/bin/python"
if [[ -d $dst/.venv ]]; then
  cp -a -- "$dst/.venv" "$backup/venv.partial"
  mv -- "$backup/venv.partial" "$backup/venv"
fi
if [[ ! -x $venv_python ]] || ! "$venv_python" -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  "$python_cmd" -m venv "$dst/.venv"
fi
printf 'Installing yt-dlp and its official dependencies...\n'
"$venv_python" -m pip install --disable-pip-version-check --quiet --upgrade 'yt-dlp[default]'
if ((asr)) || "$venv_python" -I -c 'import importlib.util; import sys; sys.exit(importlib.util.find_spec("faster_whisper") is None)'; then
  printf 'Installing local transcription (faster-whisper)...\n'
  "$venv_python" -m pip install --disable-pip-version-check --quiet --upgrade faster-whisper
fi

find_tool() {
  local found
  found=$(command -v -- "$1" || true)
  case $found in /*) printf '%s' "$found" ;; *) printf '' ;; esac
}
ffmpeg=$(find_tool ffmpeg)
ffprobe=$(find_tool ffprobe)
deno=$(find_tool deno)
node=$(find_tool node)
"$venv_python" -I - "$dst/config.json" "$ffmpeg" "$ffprobe" "$deno" "$node" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
config = {}
if path.exists():
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("metrics") is True:
            config["metrics"] = True
    except (ValueError, OSError):
        pass
for name, value in zip(("ffmpeg", "ffprobe", "deno", "node"), sys.argv[2:]):
    if value:
        config[name] = value
path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

version=$("$venv_python" -I - "$dst/host.sh" <<'PY'
import json, struct, subprocess, sys
message = json.dumps({"action": "ping"}).encode()
try:
    proc = subprocess.run([sys.argv[1]], input=struct.pack("<I", len(message)) + message,
                          capture_output=True, timeout=60)
    data, replies = proc.stdout, []
    while data:
        if len(data) < 4:
            raise ValueError("incomplete header")
        size = struct.unpack("<I", data[:4])[0]
        if size > 1024 * 1024 or len(data) < 4 + size:
            raise ValueError("incomplete response")
        replies.append(json.loads(data[4:4 + size]))
        data = data[4 + size:]
    done = [r for r in replies if r.get("status") == "done" and r.get("protocol") == 2]
    if proc.returncode != 0 or not done:
        raise ValueError(proc.stderr.decode(errors="replace")[-300:] or "no valid response")
    print(done[-1]["version"])
except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
    print(f"Self-test failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
)

"$venv_python" -I - "$dst/native-host.json" "$dst/host.sh" <<'PY'
import json, pathlib, sys
manifest = {"name": "local.tomo",
            "description": "Tomo local helper (downloads and transcription)",
            "path": sys.argv[2], "type": "stdio",
            "allowed_extensions": ["tomo@tomo.app"]}
pathlib.Path(sys.argv[1]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
mkdir -p -- "$(dirname -- "$registry")"
cp -- "$dst/native-host.json" "$registry"

chromium_ids=$("$venv_python" -I - "$src/chromium-ids.txt" "$dst/native-host-chromium.json" "$dst/host.sh" <<'PY'
import json, pathlib, re, sys
ids = []
source = pathlib.Path(sys.argv[1])
if source.is_file():
    for line in source.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if re.fullmatch(r"[a-p]{32}", value):
            ids.append(value)
target = pathlib.Path(sys.argv[2])
if ids:
    manifest = {"name": "local.tomo",
                "description": "Tomo local helper (downloads and transcription)",
                "path": sys.argv[3], "type": "stdio",
                "allowed_origins": [f"chrome-extension://{i}/" for i in ids]}
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
elif target.exists():
    target.unlink()
print(len(ids))
PY
)
for dir in "${chromium_dirs[@]}"; do
  copy="$chromium_base/$dir/NativeMessagingHosts/local.tomo.json"
  if ((chromium_ids > 0)); then
    mkdir -p -- "$(dirname -- "$copy")"
    cp -- "$dst/native-host-chromium.json" "$copy"
  else
    rm -f -- "$copy"
  fi
done

hash_args=()
hashed=(host.py sysdep.py ai.py locales.json host.sh config.json native-host.json)
if ((chromium_ids > 0)); then hashed+=(native-host-chromium.json); fi
for file in "${hashed[@]}"; do
  hash_args+=("$file" "$(hash_file "$dst/$file")")
done
"$venv_python" -I - "$dst/install.json" "$version" "$python_cmd" "${hash_args[@]}" <<'PY'
import datetime, json, pathlib, sys
files = dict(zip(sys.argv[4::2], sys.argv[5::2]))
info = {"version": sys.argv[2], "installed": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.argv[3], "files": files}
pathlib.Path(sys.argv[1]).write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
committed=1
printf 'Tomo helper %s installed in %s\n' "$version" "$dst"
[[ -n $ffmpeg && -n $ffprobe ]] || printf 'Warning: FFmpeg or FFprobe is missing; some features will be unavailable.\n' >&2
[[ -n $deno || -n $node ]] || printf 'Warning: Deno or Node is missing; YouTube may fail.\n' >&2
printf 'Install the signed extension (.xpi) from about:addons.\n'
if ((chromium_ids > 0)); then
  printf 'Chrome, Chromium, Edge and Brave: helper registered for %s extension(s).\n' "$chromium_ids"
fi
