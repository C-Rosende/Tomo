#!/usr/bin/env bash
set -euo pipefail
case $(uname -s) in
  Darwin)
    dst="$HOME/Library/Application Support/Tomo"
    registry="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts/local.tomo.json"
    chromium_base="$HOME/Library/Application Support"
    chromium_dirs=("Google/Chrome" "Chromium" "Microsoft Edge" "BraveSoftware/Brave-Browser") ;;
  Linux)
    data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
    case $data_home in /*) ;; *) printf 'XDG_DATA_HOME must be an absolute path.\n' >&2; exit 1 ;; esac
    dst="$data_home/Tomo"
    registry="$HOME/.mozilla/native-messaging-hosts/local.tomo.json"
    chromium_base="$HOME/.config"
    chromium_dirs=("google-chrome" "chromium" "microsoft-edge" "BraveSoftware/Brave-Browser") ;;
  *) printf 'This verifier only supports macOS and Linux.\n' >&2; exit 1 ;;
esac
py="$dst/.venv/bin/python"
[[ -x $py && -f $dst/install.json ]] || { printf '[FAIL] No complete installation in %s\n' "$dst" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
  hash_cmd=sha256sum
elif command -v shasum >/dev/null 2>&1; then
  hash_cmd=shasum
else
  printf '[FAIL] sha256sum or shasum is required.\n' >&2; exit 1
fi
chromium_copies=()
for dir in "${chromium_dirs[@]}"; do chromium_copies+=("$chromium_base/$dir/NativeMessagingHosts/local.tomo.json"); done
"$py" -I - "$dst" "$registry" "$hash_cmd" "${chromium_copies[@]}" <<'PY'
import json, pathlib, struct, subprocess, sys
root, registry, hash_cmd = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
chromium_copies = [pathlib.Path(p) for p in sys.argv[4:]]
ok = True
def check(label, passed, detail=""):
    global ok
    print(f"[{'OK' if passed else 'FAIL'}] {label} {detail}")
    ok &= passed
try:
    info = json.loads((root / "install.json").read_text(encoding="utf-8"))
    files = info["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("empty file list")
except (OSError, ValueError, KeyError, TypeError) as exc:
    print(f"[FAIL] invalid install.json: {exc}", file=sys.stderr)
    sys.exit(1)
required = {"host.py", "sysdep.py", "ai.py", "locales.json", "host.sh", "config.json", "native-host.json"}
chromium = "native-host-chromium.json" in files
allowed = required | {"native-host-chromium.json"}
check("Inventario de archivos", required <= set(files) <= allowed)
for name, expected in files.items():
    path = root / name
    if name not in allowed:
        check(f"Integrity {name}", False, "unexpected name")
        continue
    try:
        args = [hash_cmd, "--", str(path)] if hash_cmd == "sha256sum" else [hash_cmd, "-a", "256", "--", str(path)]
        actual = subprocess.run(args, capture_output=True, text=True, check=True).stdout.split()[0]
        check(f"Integrity {name}", actual.lower() == expected.lower())
    except (OSError, subprocess.CalledProcessError, IndexError):
        check(f"Integrity {name}", False)
try:
    manifest = json.loads((root / "native-host.json").read_text(encoding="utf-8"))
    registered = json.loads(registry.read_text(encoding="utf-8"))
    check("Native Messaging registration", registered == manifest)
    check("Manifiesto Native Messaging", manifest.get("name") == "local.tomo"
          and manifest.get("type") == "stdio" and manifest.get("path") == str(root / "host.sh")
          and "tomo@tomo.app" in manifest.get("allowed_extensions", []))
except (OSError, ValueError, TypeError) as exc:
    check("Native Messaging registration", False, str(exc))
if chromium:
    try:
        manifest = json.loads((root / "native-host-chromium.json").read_text(encoding="utf-8"))
        origins = manifest.get("allowed_origins", [])
        check("Allowed Chrome origins", bool(origins) and not any("*" in o for o in origins))
        for copy in chromium_copies:
            if copy.exists():
                check(f"Registration {copy.parent.parent.name}", json.loads(copy.read_text(encoding="utf-8")) == manifest)
    except (OSError, ValueError, TypeError) as exc:
        check("Chrome registration", False, str(exc))
try:
    message = json.dumps({"action": "ping"}).encode()
    proc = subprocess.run([str(root / "host.sh")], input=struct.pack("<I", len(message)) + message,
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
    check("Helper responds", proc.returncode == 0 and bool(done),
          f"v{done[-1].get('version')}" if done else "")
except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
    check("Helper responds", False, str(exc))
sys.exit(0 if ok else 1)
PY
