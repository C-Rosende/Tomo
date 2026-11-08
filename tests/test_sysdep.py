import importlib.util
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent.parent / "backend" / "sysdep.py"
SPEC = importlib.util.spec_from_file_location("sysdep", MODULE_PATH)
sysdep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sysdep)


class PosixSysdepTests(unittest.TestCase):
    def test_downloads_folder_linux_uses_xdg_directory(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as root:
            home = Path(root)
            config = home / ".config"
            config.mkdir()
            downloads = home / "Descargas"
            downloads.mkdir()
            (config / "user-dirs.dirs").write_text(
                'XDG_DOWNLOAD_DIR="$HOME/Descargas"\n', encoding="utf-8"
            )
            with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                    mock.patch.object(sysdep, "IS_MAC", False), \
                    mock.patch.object(Path, "home", return_value=home), \
                    mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}):
                self.assertEqual(sysdep.downloads_folder(), downloads)

    def test_downloads_folder_linux_without_user_dirs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as root:
            home = Path(root)
            with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                    mock.patch.object(sysdep, "IS_MAC", False), \
                    mock.patch.object(Path, "home", return_value=home), \
                    mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / "missing")}):
                self.assertEqual(sysdep.downloads_folder(), home / "Downloads")

    def test_downloads_folder_mac_ignores_xdg(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as root:
            home = Path(root)
            with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                    mock.patch.object(sysdep, "IS_MAC", True), \
                    mock.patch.object(Path, "home", return_value=home), \
                    mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home / "missing")}):
                self.assertEqual(sysdep.downloads_folder(), home / "Downloads")

    def test_reveal_and_open_path_mac(self):
        path = Path("somewhere") / "file.txt"
        with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                mock.patch.object(sysdep, "IS_MAC", True), \
                mock.patch.object(sysdep.subprocess, "Popen") as popen:
            sysdep.reveal(path)
            sysdep.open_path(path)
        self.assertEqual([call.args[0] for call in popen.call_args_list],
                         [["open", "-R", str(path)], ["open", str(path)]])
        for call in popen.call_args_list:
            self.assertTrue(call.kwargs["start_new_session"])
            self.assertEqual(call.kwargs["stdin"], sysdep.subprocess.DEVNULL)

    def test_windows_open_actions_break_away_from_host_job(self):
        path = Path(r"C:\Users\Test User\Downloads\vídeo final.mp4")
        with mock.patch.object(sysdep, "IS_WINDOWS", True), \
                mock.patch.object(sysdep.subprocess, "Popen") as popen, \
                mock.patch.object(sysdep.subprocess, "run") as run:
            sysdep.reveal(path)
            sysdep.open_path(path)
        self.assertEqual(len(popen.call_args_list), 1)
        self.assertEqual(len(run.call_args_list), 1)
        for call in [popen.call_args, run.call_args]:
            self.assertTrue(call.kwargs["creationflags"] & sysdep.BREAKAWAY_FROM_JOB)
        self.assertEqual(popen.call_args.args[0], ["explorer.exe", "/select,", str(path)])
        self.assertEqual(run.call_args.args[0][-1], str(path))
        self.assertTrue(run.call_args.kwargs["check"])

    def test_reveal_and_open_path_linux(self):
        path = Path("somewhere") / "file.txt"
        with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                mock.patch.object(sysdep, "IS_MAC", False), \
                mock.patch.object(sysdep.subprocess, "Popen") as popen:
            sysdep.reveal(path)
            sysdep.open_path(path)
        self.assertEqual([call.args[0] for call in popen.call_args_list],
                         [["xdg-open", str(path.parent)], ["xdg-open", str(path)]])
        self.assertTrue(all(call.kwargs["start_new_session"]
                            for call in popen.call_args_list))

    def test_clipboard_mac_uses_pbcopy(self):
        with mock.patch.object(sysdep, "IS_MAC", True), \
                mock.patch.object(sysdep.shutil, "which", return_value="/bin/pbcopy") as which, \
                mock.patch.object(sysdep.subprocess, "run") as run:
            sysdep.clipboard_set_posix("á")
        which.assert_called_once_with("pbcopy")
        run.assert_called_once_with(
            ["/bin/pbcopy"], input="á".encode("utf-8"), check=True, timeout=5,
            stdout=sysdep.subprocess.DEVNULL, stderr=sysdep.subprocess.DEVNULL)

    def test_clipboard_linux_tool_order_and_failure(self):
        cases = (
            ("wl-copy", ["/bin/wl-copy"]),
            ("xclip", ["/bin/xclip", "-selection", "clipboard"]),
            ("xsel", ["/bin/xsel", "--clipboard", "--input"]),
        )
        for selected, command in cases:
            with self.subTest(selected=selected), \
                    mock.patch.object(sysdep, "IS_MAC", False), \
                    mock.patch.object(sysdep.shutil, "which",
                                      side_effect=lambda name: f"/bin/{name}" if name == selected else None) as which, \
                    mock.patch.object(sysdep.subprocess, "run") as run:
                sysdep.clipboard_set_posix("hello")
                run.assert_called_once_with(
                    command, input=b"hello", check=True, timeout=5,
                    stdout=sysdep.subprocess.DEVNULL, stderr=sysdep.subprocess.DEVNULL)
                expected = ["wl-copy", "xclip", "xsel"]
                self.assertEqual([call.args[0] for call in which.call_args_list],
                                 expected[:expected.index(selected) + 1])
        with mock.patch.object(sysdep, "IS_MAC", False), \
                mock.patch.object(sysdep.shutil, "which", return_value=None), \
                mock.patch.object(sysdep.subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                sysdep.clipboard_set_posix("hello")
            run.assert_not_called()

    def test_contain_children_registers_group_and_lowers_priority(self):
        with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                mock.patch.object(sysdep, "_OWN_GROUP", False), \
                mock.patch.object(os, "setpgrp", create=True) as setpgrp, \
                mock.patch.object(os, "getpgrp", return_value=42, create=True), \
                mock.patch.object(os, "getpid", return_value=42), \
                mock.patch.object(os, "nice", create=True) as nice, \
                mock.patch("atexit.register") as register:
            sysdep.contain_children(True)
            self.assertTrue(sysdep._OWN_GROUP)
        setpgrp.assert_called_once_with()
        register.assert_called_once_with(sysdep.kill_children)
        nice.assert_called_once_with(10)

    def test_contain_children_does_not_register_if_group_fails(self):
        with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                mock.patch.object(sysdep, "_OWN_GROUP", False), \
                mock.patch.object(os, "setpgrp", side_effect=OSError, create=True), \
                mock.patch.object(os, "nice", create=True) as nice, \
                mock.patch("atexit.register") as register:
            sysdep.contain_children(False)
            self.assertFalse(sysdep._OWN_GROUP)
        nice.assert_not_called()
        register.assert_not_called()

    def test_kill_children_signals_only_own_group(self):
        with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                mock.patch.object(sysdep, "_OWN_GROUP", True), \
                mock.patch.object(os, "getpgrp", return_value=42, create=True), \
                mock.patch.object(os, "killpg", create=True) as killpg, \
                mock.patch.object(signal, "signal") as set_signal:
            sysdep.kill_children()
        set_signal.assert_called_once_with(signal.SIGTERM, signal.SIG_IGN)
        killpg.assert_called_once_with(42, signal.SIGTERM)

        with mock.patch.object(sysdep, "IS_WINDOWS", False), \
                mock.patch.object(sysdep, "_OWN_GROUP", False), \
                mock.patch.object(os, "killpg", create=True) as killpg:
            sysdep.kill_children()
        killpg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
