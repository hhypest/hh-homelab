"""
Проверки соответствия .env.example и compose-файлов.

В compose.yaml нет значений по умолчанию: каждая переменная объявлена как
${VAR:?...} и обязана прийти из .env. Это делает .env.example единственным
источником правды о том, что нужно задать, — и одновременно тем файлом,
про который проще всего забыть, добавляя переменную в compose.

Расхождение проявляется поздно и на чужой машине: человек копирует образец,
запускает docker compose up и получает падение на переменной, о которой
образец не сказал ни слова. Здесь это ловится сразу.
"""

from __future__ import annotations

import re

import pytest
from conftest import ROOT

PROJECTS = ["media", "homeassistant"]

# ${VAR}, ${VAR:?сообщение}, ${VAR:-умолчание} — имя нам нужно во всех формах.
VARIABLE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)[:?\-][^}]*\}|\$\{([A-Z_][A-Z0-9_]*)\}")

# Переменные самого Compose: их подставляет он сам, в .env им не место.
COMPOSE_OWN = {"COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "PWD"}


def compose_variables(project: str) -> set[str]:
    text = (ROOT / project / "compose.yaml").read_text(encoding="utf-8")
    found = {m.group(1) or m.group(2) for m in VARIABLE.finditer(text)}
    return found - COMPOSE_OWN


def example_variables(project: str) -> set[str]:
    text = (ROOT / project / ".env.example").read_text(encoding="utf-8")
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


@pytest.mark.parametrize("project", PROJECTS)
def test_example_covers_every_variable(project: str) -> None:
    """Каждая ${...} из compose.yaml должна быть в образце."""
    missing = compose_variables(project) - example_variables(project)
    assert not missing, (
        f"{project}/.env.example не описывает: {', '.join(sorted(missing))}. "
        f"Человек скопирует образец и получит падение на первом же запуске."
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_example_has_nothing_extra(project: str) -> None:
    """И наоборот: переменная в образце, которую никто не читает, — мусор."""
    extra = example_variables(project) - compose_variables(project)
    assert not extra, (
        f"{project}/.env.example описывает лишнее: {', '.join(sorted(extra))}. "
        f"В compose.yaml эти переменные не используются."
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_every_variable_is_required_somewhere(project: str) -> None:
    """
    Смысл затеи в том, что забытый .env роняет запуск, а не подставляет
    пустые строки. Для этого у каждой переменной должно быть хотя бы одно
    вхождение в форме ${VAR:?...}. Голое ${VAR} везде — и Compose вернёт
    код 0 со стеком без опубликованных портов.
    """
    text = (ROOT / project / "compose.yaml").read_text(encoding="utf-8")
    for name in sorted(compose_variables(project)):
        assert re.search(rf"\$\{{{re.escape(name)}:\?", text), (
            f"{project}/compose.yaml: ${{{name}}} нигде не помечена как обязательная — "
            f"забытый .env подставит сюда пустую строку молча"
        )


@pytest.mark.parametrize("project", PROJECTS)
def test_no_default_values_left(project: str) -> None:
    """
    Форма ${VAR:-умолчание} возвращает то, от чего мы ушли: чужие настройки,
    подставленные молча. В публичном репозитории это осечка на своей машине
    и неверное поведение на всех остальных.
    """
    text = (ROOT / project / "compose.yaml").read_text(encoding="utf-8")
    with_defaults = re.findall(r"\$\{([A-Z_][A-Z0-9_]*):-[^}]*\}", text)
    assert not with_defaults, (
        f"{project}/compose.yaml: значения по умолчанию у "
        f"{', '.join(sorted(set(with_defaults)))}"
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_example_is_not_ignored_but_env_is(project: str) -> None:
    """
    .env.example обязан попадать в репозиторий, .env — не обязан никогда.
    Правило в .gitignore легко сломать одной строкой.
    """
    import subprocess

    ignored = subprocess.run(
        ["git", "check-ignore", f"{project}/.env", f"{project}/.env.example"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    assert f"{project}/.env" in ignored, f"{project}/.env не игнорируется — утечёт в историю"
    assert f"{project}/.env.example" not in ignored, f"{project}/.env.example игнорируется"


# --- связка с Home Assistant -------------------------------------------------
# HA опрашивает сервисы медиа-стека по адресу 127.0.0.1:<порт хоста>, а порты
# хоста теперь задаются в media/.env. Разъедутся — сенсор молча уйдёт
# в «не отвечает», Пачка пришлёт тревогу, и искать причину будут в контейнере,
# который на самом деле жив.

HA_PACKAGES = "homeassistant/config/packages"
LOOPBACK = re.compile(r"https?://127\.0\.0\.1:(\d+)")

# 8123 — порт самого Home Assistant, он в host-сети и в .env не выносится.
HA_OWN_PORTS = {"8123"}


def ports_from_examples() -> set[str]:
    ports = set()
    for project in PROJECTS:
        for name, value in (
            line.split("=", 1)
            for line in (ROOT / project / ".env.example").read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        ):
            if "PORT" in name and value.strip().isdigit():
                ports.add(value.strip())
    return ports


def test_home_assistant_polls_ports_that_exist_in_env() -> None:
    """
    Каждый порт, по которому Home Assistant стучится на петлю, должен быть
    в одном из .env.example. Поменяли порт в .env и забыли про monitoring.yaml —
    падает здесь, а не ложной тревогой в три часа ночи.
    """
    known = ports_from_examples() | HA_OWN_PORTS
    strays: list[str] = []
    for path in sorted((ROOT / HA_PACKAGES).glob("*.yaml")):
        for port in set(LOOPBACK.findall(path.read_text(encoding="utf-8"))):
            if port not in known:
                strays.append(f"{path.name}: 127.0.0.1:{port}")
    assert not strays, (
        "Home Assistant опрашивает порты, которых нет ни в одном .env.example: "
        + "; ".join(strays)
        + ". Либо порт сменили только в .env, либо только здесь."
    )


# --- BIND_ADDR и петлевой путь -----------------------------------------------
# Замечание R-03 независимого аудита предлагало развести BIND_ADDR на несколько
# переменных: отдельно админки, отдельно то, что смотрит наружу. Политика
# оказалась одна на весь стек — «внутри локальной сети видно всё, снаружи
# ничего», — поэтому переменная осталась одна.
#
# Но у сужения адреса есть последствие, которое не видно из media/: публикация
# на конкретном адресе убирает петлевой путь. Сокет, привязанный к 192.168.3.53,
# на 127.0.0.1 не отвечает вовсе — connection refused. А Home Assistant
# опрашивает медиа-стек именно по петле: он работает в host-сети на том же NAS.
#
# То есть BIND_ADDR и адреса опросов связаны и обязаны меняться вместе.
# Сужение только здесь выглядит как отказ шести сервисов сразу, причём
# контейнеры при этом живы — искать причину будут в них.

LOOPBACK_ADDRESSES = {"0.0.0.0", "127.0.0.1", "::", "[::]", "localhost", ""}


def bind_addr() -> str:
    for line in (ROOT / "media" / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("BIND_ADDR="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("в media/.env.example нет BIND_ADDR")


def media_ports() -> set[str]:
    """
    Порты хоста, которые публикует медиа-стек, — только они зависят от BIND_ADDR.

    Кроме тех, у кого адрес публикации свой. FlareSolverr публикуется
    на петле отдельной переменной и от BIND_ADDR не зависит вовсе:
    сузьте BIND_ADDR — его опрос по 127.0.0.1 продолжит отвечать.
    """
    ports = set()
    свои_адреса = set()
    строки = [
        line.split("=", 1)
        for line in (ROOT / "media" / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    ]
    for name, _ in строки:
        if name.endswith("_BIND_ADDR"):
            свои_адреса.add(name.removesuffix("_BIND_ADDR"))
    for name, value in строки:
        if "PORT" in name and value.strip().isdigit():
            сервис = name.split("_PORT", 1)[0]
            if сервис not in свои_адреса:
                ports.add(value.strip())
    return ports


def polls_broken_by(address: str) -> list[str]:
    """
    Опросы Home Assistant, которые перестанут отвечать при такой привязке.

    Порты самого проекта homeassistant (8123 и dockerproxy) сюда не входят:
    они публикуются своим compose-файлом и от BIND_ADDR не зависят.
    """
    if address in LOOPBACK_ADDRESSES:
        return []
    ours = media_ports()
    broken: list[str] = []
    for path in sorted((ROOT / HA_PACKAGES).glob("*.yaml")):
        for port in sorted(set(LOOPBACK.findall(path.read_text(encoding="utf-8")))):
            if port in ours:
                broken.append(f"{path.name}: 127.0.0.1:{port}")
    return broken


def test_narrowing_bind_addr_moves_home_assistant_polls() -> None:
    """Настоящая проверка: то, что записано в образце, согласовано с опросами."""
    address = bind_addr()
    broken = polls_broken_by(address)
    assert not broken, (
        f"BIND_ADDR={address} — порты публикуются только на этом адресе, "
        f"и на 127.0.0.1 отвечать некому. Home Assistant всё ещё стучится по петле: "
        + "; ".join(broken)
        + f". Замените адрес в этих опросах на {address} (или на NAS_HOST) — "
        f"иначе сенсоры уйдут в «не отвечает» при живых контейнерах."
    )


def test_the_check_above_actually_finds_something() -> None:
    """
    Проверка выше при нынешнем BIND_ADDR=0.0.0.0 не находит ничего — и не должна.
    Но тест, который молчит всегда, молчал бы и на поломке. Поэтому здесь тот же
    поиск запускается с суженным адресом: связка обязана обнаружиться.
    """
    broken = polls_broken_by("192.168.3.53")
    assert broken, (
        "поиск ничего не нашёл даже с суженным адресом — значит, он смотрит "
        "не туда: проверьте HA_PACKAGES, LOOPBACK и имена портов в media/.env.example"
    )
    assert any("monitoring.yaml" in item for item in broken), (
        f"ожидались опросы из monitoring.yaml, а найдено: {broken}"
    )


# ---------------------------------------------------------------------------
#  Находка 12 разбора: адрес публикации FlareSolverr
# ---------------------------------------------------------------------------
#  На порту 8191 стоит безголовый браузер без какой-либо аутентификации:
#  запрос {"cmd": "request.get", "url": "..."} заставляет его сходить по
#  любому адресу и вернуть тело. Любой, кто дотянулся до этого порта, ходит
#  по сети от имени NAS — в том числе к веб-интерфейсу роутера, к DSM
#  и к самому Home Assistant.
#
#  В локальной сети порт не нужен никому: Prowlarr обращается к контейнеру
#  по имени внутри сети Docker, Home Assistant — по петле.

ПЕТЛЯ = {"127.0.0.1", "::1", "[::1]"}


def строка_публикации(порт: str) -> str:
    текст = (ROOT / "media" / "compose.yaml").read_text(encoding="utf-8")
    строки = [с.strip() for с in текст.splitlines() if f":{порт}\"" in с and с.strip().startswith("-")]
    assert len(строки) == 1, f"ожидалась одна публикация порта {порт}, найдено: {строки}"
    return строки[0]


def значение_переменной(имя: str) -> str:
    for строка in (ROOT / "media" / ".env.example").read_text(encoding="utf-8").splitlines():
        if строка.startswith(f"{имя}="):
            return строка.split("=", 1)[1].strip()
    raise AssertionError(f"в media/.env.example нет {имя}")


def test_flaresolverr_публикуется_не_на_общем_адресе() -> None:
    строка = строка_публикации("8191")
    assert "${BIND_ADDR" not in строка, (
        "FlareSolverr снова публикуется общим адресом стека: безголовый браузер "
        "без аутентификации виден всей домашней сети"
    )
    assert "FLARESOLVERR_BIND_ADDR" in строка


def test_адрес_flaresolverr_петлевой() -> None:
    assert значение_переменной("FLARESOLVERR_BIND_ADDR") in ПЕТЛЯ


def test_остальные_сервисы_остались_на_общем_адресе() -> None:
    """Разводить по сервисам всё подряд не нужно: у этих пяти есть вход по паролю."""
    for порт in ("9080", "9696", "7878", "8096", "5055"):
        assert "${BIND_ADDR" in строка_публикации(порт), f"порт {порт} ушёл со своим адресом"


def test_home_assistant_опрашивает_flaresolverr_по_петле() -> None:
    """Публикация на петле имеет смысл ровно потому, что опрос идёт оттуда же."""
    текст = (ROOT / HA_PACKAGES / "monitoring.yaml").read_text(encoding="utf-8")
    assert "127.0.0.1:8191" in текст


def test_prowlarr_ходит_к_flaresolverr_по_имени_контейнера() -> None:
    """Если бы он ходил по адресу NAS, петлевая публикация сломала бы обход Cloudflare."""
    страница = (ROOT / "docs" / "media-stack.html").read_text(encoding="utf-8")
    assert "http://flaresolverr:8191" in страница
