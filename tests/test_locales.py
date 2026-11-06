import json
import re
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parents[1] / "extension"
UNTRANSLATED_TERMS = {
    "aiModel", "cardPro", "extName", "format", "kindAudio", "labelM4a",
    "labelMkv", "labelMp3", "labelWebm", "menuAudio", "menuAudioOriginal",
    "menuM4a", "menuMkv", "menuMp3", "original", "originalLabel",
    "proBadge", "stateError", "tabAudio", "tabText", "tabVideo",
    "universal", "imageOriginal", "imageWebp", "imageMedium", "imageSmall",
}


def used_keys() -> set[str]:
    keys = set(re.findall(r'data-i18n(?:-aria|-title|-placeholder)?="([^"]+)"', (EXT / "popup.html").read_text("utf-8")))
    for name in ("popup.js", "background.js"):
        src = (EXT / name).read_text("utf-8")
        keys |= set(re.findall(r'\bt\("([A-Za-z0-9_]+)"', src))
        keys |= set(re.findall(r'"((?:label|state|menu|image)[A-Z][A-Za-z0-9]*)"', src))
    keys |= set(re.findall(r"__MSG_(\w+)__", (EXT / "manifest.json").read_text("utf-8")))
    shim = (EXT.parent / "chrome" / "compat.js").read_text("utf-8")
    keys |= set(re.findall(r'getMessage\("([A-Za-z0-9_]+)"', shim))
    return keys


class TestLocales(unittest.TestCase):
    def test_ui_selector_covers_locales_and_translations(self):
        folders = {folder.name for folder in (EXT / "_locales").iterdir() if folder.is_dir()}
        source = (EXT / "i18n.js").read_text("utf-8")
        listed = re.search(r"const UI_LOCALES\s*=\s*\[([^\]]+)\]", source)
        self.assertIsNotNone(listed)
        self.assertEqual(set(re.findall(r'"([a-z]{2})"', listed.group(1))), folders)
        english = json.loads((EXT / "_locales" / "en" / "messages.json").read_text("utf-8"))
        for locale in folders - {"en"}:
            translated = json.loads((EXT / "_locales" / locale / "messages.json").read_text("utf-8"))
            identical = [key for key in english if key not in UNTRANSLATED_TERMS
                         and english[key]["message"] == translated[key]["message"]]
            self.assertLessEqual(len(identical) / (len(english) - len(UNTRANSLATED_TERMS)), .20,
                                 f"{locale}: {identical}")

    def test_every_locale_matches_code(self):
        keys = used_keys()
        locales = sorted((EXT / "_locales").iterdir())
        self.assertEqual({folder.name for folder in locales},
                         {"en", "es", "de", "fr", "it", "pt", "pl", "ru", "tr", "ja", "ro"})
        for folder in locales:
            data = json.loads((folder / "messages.json").read_text("utf-8"))
            self.assertEqual(set(data), keys, folder.name)
            for key, entry in data.items():
                english = json.loads((EXT / "_locales" / "en" / "messages.json").read_text("utf-8"))[key]["message"]
                self.assertEqual(sorted(re.findall(r"\$\d", entry["message"])), sorted(re.findall(r"\$\d", english)),
                                 f"{folder.name}: {key}")


if __name__ == "__main__":
    unittest.main()
