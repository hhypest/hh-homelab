"""
Проверки http_check.py — скрипта, на котором держатся HTTP-проверки сервисов.

Он запускается раз в минуту шестью command_line-сенсорами Home Assistant.
Требования к нему жёстче, чем к обычному скрипту:

  * всегда печатать ровно ON или OFF — иначе сенсор уйдёт в unknown,
    и автоматизация «сервис не отвечает» просто не сработает;
  * всегда завершаться с кодом 0, даже когда всё сломалось;
  * считать успехом коды 401 и 403 — qBittorrent отвечает формой логина,
    и это нормальный признак жизни, а не отказ.
"""

from __future__ import annotations

import http.server
import socket
import subprocess
import sys
import threading

import pytest
from conftest import BIN

SCRIPT = BIN / "http_check.py"


class Handler(http.server.BaseHTTPRequestHandler):
    """Отдаёт код, зашитый в путь: /200, /401, /500."""

    def do_GET(self) -> None:
        try:
            code = int(self.path.strip("/") or 200)
        except ValueError:
            code = 200
        body = b'{"ok": true}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture(scope="module")
def server() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_живой_сервис(server: str) -> None:
    result = run(f"{server}/200")
    assert result.stdout.strip() == "ON"
    assert result.returncode == 0


@pytest.mark.parametrize("code", ["401", "403"])
def test_требует_авторизации_считается_живым(server: str, code: str) -> None:
    """qBittorrent за формой логина — живой сервис, а не отказ."""
    assert run(f"{server}/{code}").stdout.strip() == "ON"


def test_ошибка_сервера_это_отказ(server: str) -> None:
    assert run(f"{server}/500").stdout.strip() == "OFF"


def test_явный_список_кодов_сужает_проверку(server: str) -> None:
    """Со вторым аргументом успехом считается только перечисленное."""
    assert run(f"{server}/200", "200").stdout.strip() == "ON"
    assert run(f"{server}/401", "200").stdout.strip() == "OFF"
    assert run(f"{server}/404", "404").stdout.strip() == "ON"


def test_сервис_не_слушает_порт() -> None:
    """Отказ в соединении — это OFF, а не падение."""
    result = run("http://127.0.0.1:9/")
    assert result.stdout.strip() == "OFF"
    assert result.returncode == 0


def test_без_аргументов_не_падает() -> None:
    result = run()
    assert result.stdout.strip() == "OFF"
    assert result.returncode == 0


def test_мусор_вместо_адреса_не_ломает_сенсор() -> None:
    result = run("не-адрес-вовсе")
    assert result.stdout.strip() == "OFF"
    assert result.returncode == 0


def test_печатает_ровно_одно_слово(server: str) -> None:
    """
    command_line-сенсор сравнивает вывод целиком. Любая лишняя строка —
    отладочный print, предупреждение — сделает состояние сенсора неизвестным.
    """
    out = run(f"{server}/200").stdout
    assert out.splitlines() == ["ON"]