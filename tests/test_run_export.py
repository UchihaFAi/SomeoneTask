"""
Тесты оркестратора run_export.py.

Windows-специфику (Docker, .exe, startfile) подменяем моками: проверяется
логика выбора пути и обработка отказов, а не сам Windows.

Запуск:  python -m unittest discover -s tests -v
"""

import io
import socket
import subprocess
import sys
import threading
import unittest
import zipfile
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_export as rx  # noqa: E402


class TestAllyCodeNormalisation(unittest.TestCase):
    def test_accepts_dashed_and_spaced(self):
        self.assertEqual(rx.normalize_ally_code("198-869-519"), "198869519")
        self.assertEqual(rx.normalize_ally_code(" 198 869 519 \n"), "198869519")

    def test_rejects_wrong_length(self):
        self.assertIsNone(rx.normalize_ally_code("12345678"))
        self.assertIsNone(rx.normalize_ally_code("1234567890"))

    def test_rejects_garbage(self):
        self.assertIsNone(rx.normalize_ally_code(""))
        self.assertIsNone(rx.normalize_ally_code("not a code"))


class TestAllyCodeResolution(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ally_file = Path(tmp.name) / "ally_code.txt"
        patcher = mock.patch.object(rx, "ALLY_FILE", self.ally_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_argument_wins(self):
        with mock.patch.dict("os.environ", {"SWGOH_ALLY_CODE": "111111111"}):
            self.assertEqual(rx.resolve_ally_code(["run", "198-869-519"]), "198869519")

    def test_environment_is_next(self):
        with mock.patch.dict("os.environ", {"SWGOH_ALLY_CODE": "222222222"}):
            self.assertEqual(rx.resolve_ally_code(["run"]), "222222222")

    def test_saved_file_is_used(self):
        self.ally_file.write_text("333333333", encoding="utf-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(rx.resolve_ally_code(["run"]), "333333333")

    def test_prompts_and_retries(self):
        answers = iter(["oops", "198-869-519", "n"])
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("builtins.input", lambda *_: next(answers)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(rx.resolve_ally_code(["run"]), "198869519")
        self.assertFalse(self.ally_file.exists())

    def test_remembers_on_yes(self):
        answers = iter(["198869519", "y"])
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("builtins.input", lambda *_: next(answers)):
                with redirect_stdout(io.StringIO()):
                    rx.resolve_ally_code(["run"])
        self.assertEqual(self.ally_file.read_text(encoding="utf-8"), "198869519")

    def test_gives_up_after_three_tries(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("builtins.input", lambda *_: "nope"):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(rx.Failure):
                        rx.resolve_ally_code(["run"])


class _MetadataHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path != "/metadata":
            self.send_error(404)
            return
        body = b'{"latestGamedataVersion":"0.0.0:TEST"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestComlinkProbe(unittest.TestCase):
    """Проверяем детект живого сервиса на реальном сокете."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _MetadataHandler)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_detects_running_service(self):
        with mock.patch.object(rx, "COMLINK_URL", f"http://127.0.0.1:{self.port}"):
            self.assertTrue(rx.comlink_responds())

    def test_reports_closed_port(self):
        with mock.patch.object(rx, "COMLINK_URL", "http://127.0.0.1:1"):
            self.assertFalse(rx.comlink_responds())

    def test_port_busy_detection(self):
        with mock.patch.object(rx, "PORT", self.port):
            self.assertTrue(rx.port_busy())
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]
        with mock.patch.object(rx, "PORT", free_port):
            self.assertFalse(rx.port_busy())

    def test_wait_until_ready_raises_on_timeout(self):
        with mock.patch.object(rx, "comlink_responds", return_value=False):
            with mock.patch.object(rx.time, "sleep"):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(rx.Failure):
                        rx.wait_until_ready(timeout=0)


class TestStartupPathSelection(unittest.TestCase):
    def test_reuses_already_running_service(self):
        with mock.patch.object(rx, "comlink_responds", return_value=True):
            with mock.patch.object(rx, "docker_ready") as docker:
                with redirect_stdout(io.StringIO()):
                    self.assertIsNone(rx.start_comlink())
        docker.assert_not_called()

    def test_refuses_when_port_taken_by_something_else(self):
        with mock.patch.object(rx, "comlink_responds", return_value=False):
            with mock.patch.object(rx, "port_busy", return_value=True):
                with self.assertRaises(rx.Failure) as ctx:
                    rx.start_comlink()
        self.assertIn("занят", str(ctx.exception))

    def test_prefers_docker_when_available(self):
        with mock.patch.object(rx, "comlink_responds", return_value=False):
            with mock.patch.object(rx, "port_busy", return_value=False):
                with mock.patch.object(rx, "docker_ready", return_value=True):
                    with mock.patch.object(rx, "start_with_docker", return_value="docker") as run:
                        self.assertEqual(rx.start_comlink(), "docker")
        run.assert_called_once()

    def test_falls_back_to_binary_without_docker(self):
        with mock.patch.object(rx, "comlink_responds", return_value=False):
            with mock.patch.object(rx, "port_busy", return_value=False):
                with mock.patch.object(rx, "docker_ready", return_value=False):
                    with mock.patch.object(rx, "start_with_binary", return_value="bin") as run:
                        self.assertEqual(rx.start_comlink(), "bin")
        run.assert_called_once()

    def test_docker_failure_surfaces_stderr(self):
        failed = subprocess.CompletedProcess([], 1, "", "port is already allocated")
        with mock.patch.object(rx.subprocess, "run", return_value=failed):
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(rx.Failure) as ctx:
                    rx.start_with_docker()
        self.assertIn("port is already allocated", str(ctx.exception))

    def test_docker_absent_when_binary_missing(self):
        with mock.patch.object(rx.shutil, "which", return_value=None):
            self.assertFalse(rx.docker_ready())


class TestBinaryPath(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "comlink"
        patcher = mock.patch.object(rx, "BINARY_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_archive(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        archive = self.dir / rx.COMLINK_ASSET
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(f"swgoh-comlink-{rx.COMLINK_VERSION}.exe", b"stub")
        return archive

    def test_extracts_archive_and_launches(self):
        self._make_archive()
        alive = mock.Mock()
        alive.poll.return_value = None
        with mock.patch.object(rx.subprocess, "Popen", return_value=alive) as popen:
            with mock.patch.object(rx.time, "sleep"):
                with redirect_stdout(io.StringIO()):
                    self.assertIs(rx.start_with_binary(), alive)
        launched = popen.call_args[0][0]
        self.assertTrue(launched[0].endswith(".exe"))
        self.assertEqual(launched[1:], ["--name", rx.APP_NAME, "--port", str(rx.PORT)])

    def test_immediate_exit_mentions_bytecode_bug(self):
        self._make_archive()
        dead = mock.Mock()
        dead.poll.return_value = 1
        with mock.patch.object(rx.subprocess, "Popen", return_value=dead):
            with mock.patch.object(rx.time, "sleep"):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(rx.Failure) as ctx:
                        rx.start_with_binary()
        self.assertIn("bytecode", str(ctx.exception))
        self.assertIn("Docker", str(ctx.exception))

    def test_downloads_when_archive_absent(self):
        def fake_download(url, target):
            self.assertEqual(url, rx.COMLINK_URL_DOWNLOAD)
            with zipfile.ZipFile(target, "w") as zf:
                zf.writestr("swgoh-comlink.exe", b"stub")

        alive = mock.Mock()
        alive.poll.return_value = None
        with mock.patch.object(rx, "_download", side_effect=fake_download) as dl:
            with mock.patch.object(rx.subprocess, "Popen", return_value=alive):
                with mock.patch.object(rx.time, "sleep"):
                    with redirect_stdout(io.StringIO()):
                        rx.start_with_binary()
        dl.assert_called_once()

    def test_reuses_existing_executable_without_download(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "swgoh-comlink-4.4.2.exe").write_bytes(b"stub")
        alive = mock.Mock()
        alive.poll.return_value = None
        with mock.patch.object(rx, "_download") as dl:
            with mock.patch.object(rx.subprocess, "Popen", return_value=alive):
                with mock.patch.object(rx.time, "sleep"):
                    with redirect_stdout(io.StringIO()):
                        rx.start_with_binary()
        dl.assert_not_called()


class TestShutdown(unittest.TestCase):
    def test_noop_when_service_was_already_running(self):
        with mock.patch.object(rx.subprocess, "run") as run:
            rx.stop_comlink(None)
        run.assert_not_called()

    def test_removes_container(self):
        with mock.patch.object(rx.subprocess, "run") as run:
            with redirect_stdout(io.StringIO()):
                rx.stop_comlink("docker")
        self.assertIn(rx.CONTAINER_NAME, run.call_args[0][0])

    def test_kills_process_that_ignores_terminate(self):
        stubborn = mock.Mock()
        stubborn.wait.side_effect = subprocess.TimeoutExpired("cmd", 15)
        with redirect_stdout(io.StringIO()):
            rx.stop_comlink(stubborn)
        stubborn.terminate.assert_called_once()
        stubborn.kill.assert_called_once()


class TestMainFlow(unittest.TestCase):
    def test_stops_comlink_even_when_export_fails(self):
        with mock.patch.object(rx, "resolve_ally_code", return_value="198869519"), \
             mock.patch.object(rx, "ensure_script", return_value=Path("fetch_roster.py")), \
             mock.patch.object(rx, "ensure_requests"), \
             mock.patch.object(rx, "start_comlink", return_value="docker"), \
             mock.patch.object(rx, "wait_until_ready"), \
             mock.patch.object(rx, "run_export", return_value=2), \
             mock.patch.object(rx, "stop_comlink") as stop, \
             mock.patch.object(rx, "show_results"):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(rx.main(["run"]), 2)
        stop.assert_called_once_with("docker")

    def test_stops_comlink_when_startup_raises(self):
        with mock.patch.object(rx, "resolve_ally_code", return_value="198869519"), \
             mock.patch.object(rx, "ensure_script", return_value=Path("fetch_roster.py")), \
             mock.patch.object(rx, "ensure_requests"), \
             mock.patch.object(rx, "start_comlink", side_effect=rx.Failure("нет")), \
             mock.patch.object(rx, "stop_comlink") as stop:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(rx.main(["run"]), 1)
        stop.assert_called_once_with(None)

    def test_skips_wait_when_service_was_already_up(self):
        with mock.patch.object(rx, "resolve_ally_code", return_value="198869519"), \
             mock.patch.object(rx, "ensure_script", return_value=Path("fetch_roster.py")), \
             mock.patch.object(rx, "ensure_requests"), \
             mock.patch.object(rx, "start_comlink", return_value=None), \
             mock.patch.object(rx, "wait_until_ready") as wait, \
             mock.patch.object(rx, "run_export", return_value=0), \
             mock.patch.object(rx, "stop_comlink"), \
             mock.patch.object(rx, "show_results"):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(rx.main(["run"]), 0)
        wait.assert_not_called()


class TestRunBat(unittest.TestCase):
    """Батник должен остаться ASCII — иначе ломается на кодовых страницах."""

    def test_is_ascii_only(self):
        content = (Path(__file__).resolve().parent.parent / "run.bat").read_bytes()
        try:
            content.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail(f"run.bat содержит не-ASCII байты: {exc}")

    def test_uses_crlf_line_endings(self):
        """С LF-переводами cmd.exe ломается на метках и goto."""
        content = (Path(__file__).resolve().parent.parent / "run.bat").read_bytes()
        self.assertEqual(
            content.count(b"\r\n"), content.count(b"\n"),
            "в run.bat есть строки без CR — нужен CRLF",
        )

    def test_invokes_orchestrator_and_pauses(self):
        text = (Path(__file__).resolve().parent.parent / "run.bat").read_text(encoding="ascii")
        self.assertIn("run_export.py", text)
        self.assertIn("pause", text)
        # Запуск из своей папки, а не из текущей директории проводника.
        self.assertIn('cd /d "%~dp0"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
