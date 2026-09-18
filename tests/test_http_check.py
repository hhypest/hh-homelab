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
from conftest import BIN, ROOT

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


def test_live_service_reports_on(server: str) -> None:
    result = run(f"{server}/200")
    assert result.stdout.strip() == "ON"
    assert result.returncode == 0


@pytest.mark.parametrize("code", ["401", "403"])
def test_auth_required_counts_as_live(server: str, code: str) -> None:
    """qBittorrent за формой логина — живой сервис, а не отказ."""
    assert run(f"{server}/{code}").stdout.strip() == "ON"


def test_server_error_reports_off(server: str) -> None:
    assert run(f"{server}/500").stdout.strip() == "OFF"


def test_второй_аргумент_добавляет_а_не_заменяет(server: str) -> None:
    """
    Здесь было записано обратное: «со вторым аргументом успехом считается
    только перечисленное». Код так и работал, а docstring скрипта обещал
    «доп. коды» — и обещание не выполнялось ни для одного из шести сенсоров.

    Стреляло бы это так: Radarr включает аутентификацию, /ping начинает
    отвечать 401, в команде сенсора перечислен 200 — и приходит «сервис
    не отвечает» о полностью живом сервисе. Ровно ради этого случая 401
    и 403 внесены в набор по умолчанию.
    """
    assert run(f"{server}/401", "200").stdout.strip() == "ON", (
        "перечисленный код вытеснил набор по умолчанию"
    )
    assert run(f"{server}/404", "404").stdout.strip() == "ON", "код не добавился"
    assert run(f"{server}/404").stdout.strip() == "OFF", "набор по умолчанию раздулся"


def test_нецифровой_аргумент_не_гасит_проверку(server: str) -> None:
    """Раньше он давал пустой набор кодов, то есть OFF при любом ответе."""
    готово = run(f"{server}/200", "абв")
    assert готово.stdout.strip() == "ON"
    assert "не код ответа" in готово.stderr, "молча проглоченная опечатка не видна в журнале"
    assert готово.stdout.strip().count("\n") == 0, "stdout читает сенсор, там только одно слово"


def test_сенсоры_не_передают_лишних_кодов() -> None:
    """
    Списки кодов в командах остались бы безвредными, но вводящими
    в заблуждение: перечисление 200 больше ничего не означает.
    """
    текст = (ROOT / "homeassistant" / "config" / "packages" / "monitoring.yaml").read_text(
        encoding="utf-8"
    )
    команды = [с for с in текст.splitlines() if "http_check.py" in с and "command:" in с]
    assert len(команды) == 6
    for команда in команды:
        хвост = команда.split("http_check.py", 1)[1].strip().rstrip('"')
        assert len(хвост.split()) == 1, f"лишний аргумент в команде: {команда.strip()}"


def test_connection_refused_reports_off() -> None:
    """Отказ в соединении — это OFF, а не падение."""
    result = run("http://127.0.0.1:9/")
    assert result.stdout.strip() == "OFF"
    assert result.returncode == 0


def test_no_arguments_does_not_crash() -> None:
    result = run()
    assert result.stdout.strip() == "OFF"
    assert result.returncode == 0


def test_garbage_url_does_not_break_sensor() -> None:
    result = run("не-адрес-вовсе")
    assert result.stdout.strip() == "OFF"
    assert result.returncode == 0


def test_prints_exactly_one_word(server: str) -> None:
    """
    command_line-сенсор сравнивает вывод целиком. Любая лишняя строка —
    отладочный print, предупреждение — сделает состояние сенсора неизвестным.
    """
    out = run(f"{server}/200").stdout
    assert out.splitlines() == ["ON"]
