"""
Проверки docker_state.py — быстрого обнаружения упавших контейнеров.

Скрипт опрашивает docker-socket-proxy раз в минуту и печатает JSON, который
читает command_line-сенсор. На нём висит уведомление «упал контейнер», поэтому
важно не только «работает», но и то, как он ведёт себя, когда всё плохо:
прокси лежит, отвечает 403, или отдаёт мусор вместо JSON. Во всех этих случаях
сенсор должен получить разбираемый JSON с непустым полем error — иначе он уйдёт
в unavailable, а отдельная автоматизация «не вижу состояние контейнеров»
не сработает.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading

import pytest
from conftest import BIN

SCRIPT = BIN / "docker_state.py"

RUNNING = {"Names": ["/jellyfin"], "State": "running", "Status": "Up 3 days"}
OOM_KILLED = {"Names": ["/radarr"], "State": "exited", "Status": "Exited (137) 2 minutes ago"}
STOPPED = {"Names": ["/prowlarr"], "State": "exited", "Status": "Exited (0) 5 minutes ago"}


def make_server(payload, status: int = 200, raw: bytes | None = None) -> tuple[str, object]:
    """Поднимает подставной docker-socket-proxy на свободном порту."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = raw if raw is not None else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", httpd


def run(proxy_url: str, *names: str) -> dict:
    env = dict(os.environ, DOCKER_PROXY_URL=proxy_url, DOCKER_PROXY_TIMEOUT="3")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *names],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )
    assert result.returncode == 0, f"скрипт обязан завершаться нулём: {result.stderr}"
    return json.loads(result.stdout)


def test_все_на_месте() -> None:
    url, httpd = make_server([RUNNING])
    try:
        data = run(url, "jellyfin")
    finally:
        httpd.shutdown()
    assert data["down"] == 0
    assert data["running"] == 1
    assert data["total"] == 1
    assert data["error"] == ""
    assert data["down_names"] == []


def test_упавший_контейнер_назван_поимённо() -> None:
    url, httpd = make_server([RUNNING, OOM_KILLED])
    try:
        data = run(url, "jellyfin", "radarr")
    finally:
        httpd.shutdown()
    assert data["down"] == 1
    assert data["down_names"] == ["radarr"]
    assert "radarr" in data["down_detail"][0]
    assert "137" in data["down_detail"][0]


def test_код_137_помечается_как_нехватка_памяти() -> None:
    """Ради этого флага в уведомление попадает подсказка про OOM."""
    url, httpd = make_server([OOM_KILLED])
    try:
        assert run(url, "radarr")["oom"] is True
    finally:
        httpd.shutdown()


def test_обычная_остановка_не_считается_нехваткой_памяти() -> None:
    url, httpd = make_server([STOPPED])
    try:
        data = run(url, "prowlarr")
    finally:
        httpd.shutdown()
    assert data["down"] == 1
    assert data["oom"] is False


def test_отсутствующий_контейнер_считается_упавшим() -> None:
    """Проект не поднялся целиком — контейнера нет даже среди остановленных."""
    url, httpd = make_server([RUNNING])
    try:
        data = run(url, "jellyfin", "seerr")
    finally:
        httpd.shutdown()
    assert data["down"] == 1
    assert data["down_names"] == ["seerr"]
    assert "нет" in data["down_detail"][0]


def test_прокси_запрещает_запрос() -> None:
    """403 означает, что в dockerproxy не разрешён CONTAINERS=1."""
    url, httpd = make_server(None, status=403)
    try:
        data = run(url, "jellyfin")
    finally:
        httpd.shutdown()
    assert data["error"] == "proxy HTTP 403"
    assert data["down"] == 0, "при ошибке нельзя объявлять контейнеры упавшими"


def test_прокси_молчит() -> None:
    data = run("http://127.0.0.1:9", "jellyfin")
    assert data["error"] != ""
    assert data["down"] == 0


def test_прокси_отдал_мусор() -> None:
    url, httpd = make_server(None, raw=b"<html>not json</html>")
    try:
        data = run(url, "jellyfin")
    finally:
        httpd.shutdown()
    assert data["error"] != ""


def test_вывод_всегда_одна_строка_json() -> None:
    """
    Сенсор разбирает вывод как JSON целиком. Лишняя строка сделает
    value_template невычислимым, и сенсор замолчит.
    """
    url, httpd = make_server([RUNNING])
    try:
        env = dict(os.environ, DOCKER_PROXY_URL=url)
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "jellyfin"],
            capture_output=True, text=True, timeout=30, check=False, env=env,
        ).stdout
    finally:
        httpd.shutdown()
    assert len(out.strip().splitlines()) == 1
    json.loads(out)


COMPOSE_FILES = ("media/compose.yaml", "homeassistant/compose.yaml")


def контейнеры_из_compose() -> set[str]:
    """
    Имена контейнеров обоих стеков — то, что реально увидит Docker.

    Сверять надо именно container_name, а не ключ сервиса: docker_state.py
    ищет контейнеры по именам из ответа Docker API, и разойдись эти две вещи —
    скрипт молча считал бы сервис отсутствующим. Сейчас в обоих файлах они
    совпадают, но полагаться на это нельзя: container_name можно поменять,
    не трогая ключ.
    """
    import yaml
    from conftest import ROOT

    names = set()
    for name in COMPOSE_FILES:
        config = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
        for service, body in (config.get("services") or {}).items():
            names.add((body or {}).get("container_name") or service)
    return names


def test_список_по_умолчанию_совпадает_с_docker_yaml() -> None:
    """
    Список WATCHED в скрипте и containers в packages/docker.yaml должны
    описывать одни и те же контейнеры: разойдутся — и часть сервисов
    останется без надзора, о чём никто не узнает.
    """
    import yaml
    from conftest import ROOT, load

    module = load(SCRIPT)

    class Loader(yaml.SafeLoader):
        pass

    for tag in ("!secret", "!include", "!include_dir_named"):
        Loader.add_constructor(tag, lambda loader, node: None)

    package = yaml.load(
        (ROOT / "homeassistant/config/packages/docker.yaml").read_text(encoding="utf-8"),
        Loader=Loader,
    )
    from_package = set(package["monitor_docker"][0]["containers"])
    assert set(module.WATCHED) == from_package, (
        "WATCHED в docker_state.py разошёлся со списком containers в docker.yaml"
    )


def test_список_по_умолчанию_совпадает_с_compose() -> None:
    """
    Под надзором должны быть ровно те контейнеры, которые описаны в двух
    compose-файлах — ни больше, ни меньше.

    Без этой проверки предыдущая ничего не стоит: добавить сервис в compose
    и забыть про WATCHED и docker.yaml сразу — самый вероятный сценарий,
    а два согласованных между собой списка проходят её зелёными.
    Забытый контейнер не попадёт ни в уведомление о падении, ни на дашборд,
    и узнать об этом можно будет только когда он ляжет.
    """
    from conftest import load

    module = load(SCRIPT)
    из_compose = контейнеры_из_compose()
    watched = set(module.WATCHED)

    assert watched == из_compose, (
        f"WATCHED разошёлся с compose-файлами. "
        f"Нет под надзором: {sorted(из_compose - watched) or '—'}. "
        f"Под надзором, но нет в compose: {sorted(watched - из_compose) or '—'}"
    )


@pytest.mark.parametrize("names", [("jellyfin",), ("jellyfin", "radarr", "seerr")])
def test_total_равен_числу_запрошенных(names: tuple[str, ...]) -> None:
    url, httpd = make_server([RUNNING])
    try:
        assert run(url, *names)["total"] == len(names)
    finally:
        httpd.shutdown()
