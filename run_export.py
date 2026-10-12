#!/usr/bin/env python3
"""
Запуск «в один клик»: поднимает Comlink, выгружает ростер, открывает папку.

Вызывается из run.bat, но работает и сам по себе:
    python run_export.py [ally_code]

Зависимостей нет — всё на стандартной библиотеке. requests нужен только
самому fetch_roster.py и ставится автоматически.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

PORT = 3000
COMLINK_URL = f"http://127.0.0.1:{PORT}"
APP_NAME = "my-roster-export"

CONTAINER_NAME = "swgoh-comlink-oneclick"
DOCKER_IMAGE = "ghcr.io/swgoh-utils/swgoh-comlink:latest"

# v4.5.0 под Windows не запускается (pkg/V8 bytecode), поэтому берём предыдущий.
COMLINK_VERSION = "4.4.2"
COMLINK_ASSET = f"swgoh-comlink-win-{COMLINK_VERSION}.zip"
COMLINK_URL_DOWNLOAD = (
    "https://github.com/swgoh-utils/swgoh-comlink/releases/download/"
    f"v{COMLINK_VERSION}/{COMLINK_ASSET}"
)
BINARY_DIR = HERE / "comlink"

SCRIPT_NAME = "fetch_roster.py"
SCRIPT_URL = (
    "https://raw.githubusercontent.com/UchihaFAi/SomeoneTask/"
    "claude/md-file-actions-6eixrc/fetch_roster.py"
)

ALLY_FILE = HERE / "ally_code.txt"
READY_TIMEOUT = 90


def say(message: str = "") -> None:
    print(message, flush=True)


def step(message: str) -> None:
    say(f"\n>>> {message}")


class Failure(RuntimeError):
    """Ошибка, которую показываем пользователю без простыни трейсбека."""


# --------------------------------------------------------------- ally code


def normalize_ally_code(raw: str) -> str | None:
    """Оставляет только цифры; валиден код из девяти цифр."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits if len(digits) == 9 else None


def resolve_ally_code(argv: list[str]) -> str:
    """Порядок: аргумент → переменная окружения → ally_code.txt → спросить."""
    for candidate in (
        argv[1] if len(argv) > 1 else "",
        os.environ.get("SWGOH_ALLY_CODE", ""),
        ALLY_FILE.read_text(encoding="utf-8") if ALLY_FILE.exists() else "",
    ):
        code = normalize_ally_code(candidate)
        if code:
            return code

    say("Введите Ally Code (девять цифр, дефисы можно).")
    say("Посмотреть в игре: Профиль -> Ally Code.")
    for _ in range(3):
        code = normalize_ally_code(input("Ally Code: "))
        if code:
            _remember(code)
            return code
        say("Нужно ровно девять цифр. Попробуйте ещё раз.")
    raise Failure("Ally Code так и не введён.")


def _remember(code: str) -> None:
    answer = input("Запомнить код для следующих запусков? [y/N]: ").strip().lower()
    if answer in ("y", "yes", "д", "да"):
        try:
            ALLY_FILE.write_text(code, encoding="utf-8")
            say(f"Сохранено в {ALLY_FILE.name} (в репозиторий этот файл не попадёт).")
        except OSError as exc:
            say(f"Не удалось сохранить: {exc}")


# ----------------------------------------------------------- подготовка


def ensure_script() -> Path:
    """fetch_roster.py должен лежать рядом; если нет — качаем."""
    target = HERE / SCRIPT_NAME
    if target.exists():
        return target
    step(f"{SCRIPT_NAME} рядом не найден — скачиваю")
    try:
        with urllib.request.urlopen(SCRIPT_URL, timeout=60) as resp:
            target.write_bytes(resp.read())
    except (urllib.error.URLError, OSError) as exc:
        raise Failure(f"Не удалось скачать {SCRIPT_NAME}: {exc}") from exc
    say(f"Готово: {target}")
    return target


def ensure_requests() -> None:
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass
    step("Устанавливаю библиотеку requests")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "requests"]
    )
    if result.returncode != 0:
        # Вторая попытка: в системный Python часто нельзя писать без --user.
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--user",
             "--disable-pip-version-check", "requests"]
        )
    if result.returncode != 0:
        raise Failure(
            "Не удалось установить requests. Выполните вручную:\n"
            f"    {sys.executable} -m pip install --user requests"
        )
    say("requests установлен.")


# -------------------------------------------------------------- Comlink


def _opener():
    """Без прокси: обращаемся к локальному сервису."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def comlink_responds(timeout: float = 3.0) -> bool:
    request = urllib.request.Request(
        f"{COMLINK_URL}/metadata",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _opener().open(request, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def port_busy() -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def wait_until_ready(timeout: int = READY_TIMEOUT) -> None:
    say(f"Жду ответа Comlink (до {timeout} с)", )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if comlink_responds():
            say("Comlink отвечает.")
            return
        time.sleep(2)
        print(".", end="", flush=True)
    raise Failure("Comlink не ответил вовремя.")


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=25
        ).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def start_with_docker() -> str:
    step("Запускаю Comlink в Docker")
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    result = subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER_NAME,
         "-p", f"{PORT}:3000", "-e", f"APP_NAME={APP_NAME}", DOCKER_IMAGE],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise Failure(f"docker run не сработал:\n{result.stderr.strip()}")
    say(f"Контейнер {CONTAINER_NAME} запущен.")
    return "docker"


def _download(url: str, target: Path) -> None:
    say(f"Скачиваю {url}")
    try:
        with urllib.request.urlopen(url, timeout=180) as resp, target.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except (urllib.error.URLError, OSError) as exc:
        raise Failure(f"Не удалось скачать: {exc}") from exc


def _find_executable() -> Path | None:
    return next(iter(sorted(BINARY_DIR.glob("*.exe"))), None)


def start_with_binary():
    step(f"Docker недоступен — беру бинарник Comlink v{COMLINK_VERSION}")
    BINARY_DIR.mkdir(exist_ok=True)

    executable = _find_executable()
    if executable is None:
        archive = BINARY_DIR / COMLINK_ASSET
        if not archive.exists():
            _download(COMLINK_URL_DOWNLOAD, archive)
        say("Распаковываю")
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(BINARY_DIR)
        except (zipfile.BadZipFile, OSError) as exc:
            raise Failure(f"Архив не распаковался: {exc}") from exc
        executable = _find_executable()

    if executable is None:
        raise Failure(f"В {BINARY_DIR} не нашёлся .exe — распакуйте архив вручную.")

    say(f"Запускаю {executable.name}")
    creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        process = subprocess.Popen(
            [str(executable), "--name", APP_NAME, "--port", str(PORT)],
            cwd=str(BINARY_DIR),
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise Failure(f"Не удалось запустить {executable.name}: {exc}") from exc

    time.sleep(3)
    if process.poll() is not None:
        raise Failure(
            f"{executable.name} завершился сразу после запуска.\n"
            "Если в его окне была ошибка 'V8 rejected the bytecode cache' — сборка битая,\n"
            "поставьте Docker Desktop и запустите этот файл снова."
        )
    return process


def start_comlink():
    """Возвращает описатель запущенного сервиса (или None, если он уже работал)."""
    if comlink_responds():
        say(f"Comlink уже работает на порту {PORT} — использую его.")
        return None
    if port_busy():
        raise Failure(
            f"Порт {PORT} занят чем-то другим (на /metadata не отвечает).\n"
            "Закройте занявшую его программу и запустите снова."
        )
    if docker_ready():
        return start_with_docker()
    return start_with_binary()


def stop_comlink(handle) -> None:
    if handle is None:
        return
    step("Останавливаю Comlink")
    if handle == "docker":
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    else:
        handle.terminate()
        try:
            handle.wait(timeout=15)
        except subprocess.TimeoutExpired:
            handle.kill()
    say("Остановлен.")


# ------------------------------------------------------------- выгрузка


def run_export(script: Path, ally_code: str) -> int:
    step("Выгружаю ростер")
    return subprocess.run(
        [sys.executable, str(script), ally_code, "--url", COMLINK_URL, "--out-dir", str(HERE)]
    ).returncode


def show_results() -> None:
    produced = [name for name in ("roster_clean.json", "ships_clean.json", "roster_raw.json")
                if (HERE / name).exists()]
    if not produced:
        return
    step("Готовые файлы")
    for name in produced:
        size = (HERE / name).stat().st_size
        say(f"  {name}  ({size / 1024:.0f} КБ)")
    say("\nВ Claude Project грузить roster_clean.json, мне прислать roster_raw.json.")
    try:
        os.startfile(HERE)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        say(f"\nПапка: {HERE}")


def main(argv: list[str]) -> int:
    say("=" * 62)
    say("  Выгрузка ростера SWGOH")
    say("=" * 62)

    handle = None
    try:
        ally_code = resolve_ally_code(argv)
        say(f"\nAlly Code: ***{ally_code[-3:]}")

        script = ensure_script()
        ensure_requests()

        handle = start_comlink()
        if handle is not None:
            wait_until_ready()

        code = run_export(script, ally_code)
    except Failure as exc:
        say(f"\n[ОШИБКА] {exc}")
        return 1
    except KeyboardInterrupt:
        say("\nПрервано.")
        return 130
    finally:
        stop_comlink(handle)

    if code == 0:
        show_results()
        say("\nГотово.")
    else:
        say(f"\nВыгрузка завершилась с кодом {code}. Смотрите сообщения выше.")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
