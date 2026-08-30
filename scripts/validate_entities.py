#!/usr/bin/env python3
"""
Сверка ссылок на сущности внутри конфигурации Home Assistant.

Зачем
-----
Home Assistant не ругается на ссылку в несуществующую сущность. Автоматизация
с опечаткой в entity_id спокойно загрузится и просто никогда не сработает —
узнаёшь об этом в тот день, когда она была нужна.

Проверить можно не всё: сущности от интеграций (Synology DSM, Jellyfin, webOS,
Android TV) появляются только на живой системе. Зато всё, что репозиторий
создаёт сам — скрипты, флаги, шаблонные сенсоры, HTTP-проверки, сенсоры
Monitor Docker, — можно сверить прямо здесь.

Именно на этом классе ошибок уже спотыкались: переименование контейнера
dockerproxy в «Docker Proxy» дало бы sensor.docker_docker_proxy_state вместо
ожидаемого sensor.docker_proxy_state, и карточка на дашборде молча показывала
бы «объект не найден».

Что делает
----------
1. Собирает сущности, которые репозиторий определяет сам.
2. Собирает все ссылки вида domain.object_id из пакетов, автоматизаций,
   скриптов и дашборда.
3. Сравнивает — но только в «своих» пространствах имён, перечисленных
   в OWNED. Остальное лишь показывает списком, чтобы можно было глазами
   сверить с живой системой.
4. Заодно ищет дубликаты id автоматизаций и имён скриптов: пакеты
   склеиваются в одну конфигурацию, и совпадение молча съест одну из них.

Использование:
    python3 scripts/validate_entities.py [--verbose]
"""

from __future__ import annotations

import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("Нужен PyYAML: pip install pyyaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "homeassistant" / "config"
PACKAGES = sorted((CONFIG / "packages").glob("*.yaml"))
DASHBOARD = ROOT / "homeassistant" / "dashboard-infrastructure.yaml"

# Пространства имён, за которые репозиторий отвечает сам.
# Ссылка сюда на несуществующую сущность — точно ошибка.
OWNED = (
    "script.",
    "input_boolean.",
    "binary_sensor.svc_",
    "binary_sensor.stack_",
    "binary_sensor.docker_",
    "binary_sensor.tv_",
    "sensor.docker_",
    "sensor.server_",
    "sensor.stack_",
)

# Суффиксы сенсоров Monitor Docker: имя = "Docker {контейнер} {метрика}".
# Список должен отвечать monitored_conditions в packages/docker.yaml.
DOCKER_SUFFIXES = ("state", "uptime", "cpu", "memory", "memory_percentage")
DOCKER_GLOBAL = (
    "sensor.docker_version",
    "sensor.docker_containers_running",
    "sensor.docker_containers_total",
    "sensor.docker_containers_cpu_percentage",
    "sensor.docker_containers_memory",
)

ENTITY_RE = re.compile(
    r"\b(sensor|binary_sensor|script|input_boolean|media_player|remote|switch|button|"
    r"input_number|input_text|automation|scene|notify)\.[a-z0-9_]+\b"
)


class Loader(yaml.SafeLoader):
    pass


for _tag in ("!secret", "!include", "!include_dir_named", "!include_dir_list",
             "!include_dir_merge_list", "!include_dir_merge_named", "!env_var"):
    Loader.add_constructor(_tag, lambda loader, node: None)


def slug(name: str) -> str:
    """Как Home Assistant делает object_id из имени. Для латиницы — точно."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")


# Ключи, значение которых — не сущность, а имя службы или платформы.
# Без этого «action: input_boolean.turn_off» выглядит как ссылка
# на несуществующий флаг с именем turn_off.
NOT_ENTITY_KEYS = {
    "action", "service", "platform", "trigger", "condition", "domain",
    "device_class", "state_class", "media_content_type", "type",
}


def walk_strings(node, key: str | None = None):
    """Отдаёт строки вместе с ключом, под которым они лежат."""
    if isinstance(node, dict):
        for k, value in node.items():
            yield from walk_strings(value, k if isinstance(k, str) else None)
    elif isinstance(node, list):
        for item in node:
            yield from walk_strings(item, key)
    elif isinstance(node, str) and key not in NOT_ENTITY_KEYS:
        yield node


def collect_defined(docs: dict[pathlib.Path, dict]) -> tuple[set[str], list[str]]:
    defined: set[str] = set()
    problems: list[str] = []
    automation_ids: dict[str, str] = {}
    script_keys: dict[str, str] = {}

    for path, data in docs.items():
        if not isinstance(data, dict):
            continue

        for key in data.get("script", {}) or {}:
            defined.add(f"script.{key}")
            if key in script_keys:
                problems.append(f"скрипт «{key}» определён дважды: {script_keys[key]} и {path.name}")
            script_keys[key] = path.name

        for key in data.get("input_boolean", {}) or {}:
            defined.add(f"input_boolean.{key}")

        for item in data.get("automation", []) or []:
            ident = (item or {}).get("id")
            if not ident:
                problems.append(f"{path.name}: автоматизация «{(item or {}).get('alias', '?')}» без id")
                continue
            if ident in automation_ids:
                problems.append(f"id автоматизации «{ident}» повторяется: {automation_ids[ident]} и {path.name}")
            automation_ids[ident] = path.name

        # template: список блоков, в каждом sensor / binary_sensor
        for block in data.get("template", []) or []:
            for domain in ("sensor", "binary_sensor"):
                for item in (block or {}).get(domain, []) or []:
                    name = (item or {}).get("name")
                    if name:
                        defined.add(f"{domain}.{slug(name)}")

        # command_line: список блоков с одним ключом-доменом
        for block in data.get("command_line", []) or []:
            for domain in ("sensor", "binary_sensor"):
                item = (block or {}).get(domain)
                if item and item.get("name"):
                    defined.add(f"{domain}.{slug(item['name'])}")

        # rest: список источников, внутри sensor
        for block in data.get("rest", []) or []:
            for item in (block or {}).get("sensor", []) or []:
                if item.get("name"):
                    defined.add(f"sensor.{slug(item['name'])}")

        # sensor: платформенные записи (history_stats)
        for item in data.get("sensor", []) or []:
            if isinstance(item, dict) and item.get("name"):
                defined.add(f"sensor.{slug(item['name'])}")

        # monitor_docker разворачиваем по правилу именования
        for instance in data.get("monitor_docker", []) or []:
            rename = (instance or {}).get("rename", {}) or {}
            for container in (instance or {}).get("containers", []) or []:
                label = rename.get(container, container)
                for suffix in DOCKER_SUFFIXES:
                    defined.add(f"sensor.docker_{slug(label)}_{suffix}")
            defined.update(DOCKER_GLOBAL)

    return defined, problems


def main() -> int:
    verbose = "--verbose" in sys.argv
    docs: dict[pathlib.Path, dict] = {}

    for path in [*PACKAGES, DASHBOARD]:
        if not path.exists():
            continue
        try:
            docs[path] = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)
        except yaml.YAMLError as err:
            print(f"  ✗ {path.name}: не разбирается — {err}")
            return 1

    defined, problems = collect_defined(docs)

    referenced: dict[str, set[str]] = {}
    for path, data in docs.items():
        for text in walk_strings(data):
            for match in ENTITY_RE.finditer(text):
                entity = match.group(0)
                # Обрывок регулярного выражения, а не идентификатор:
                # маски вида ^binary_sensor\.svc_ кончаются подчёркиванием.
                if entity.endswith("_"):
                    continue
                referenced.setdefault(entity, set()).add(path.name)

    unknown = {
        entity: where
        for entity, where in referenced.items()
        if entity.startswith(OWNED) and entity not in defined
    }
    for entity, where in sorted(unknown.items()):
        problems.append(f"ссылка на несуществующую сущность {entity} — в {', '.join(sorted(where))}")

    external = sorted(e for e in referenced if not e.startswith(OWNED))

    print(f"Определено репозиторием: {len(defined)}")
    print(f"Найдено ссылок: {len(referenced)}, из них проверяемых: "
          f"{sum(1 for e in referenced if e.startswith(OWNED))}")
    print(f"От интеграций (проверить можно только на живой системе): {len(external)}")
    if verbose:
        for entity in sorted(defined):
            print(f"    + {entity}")
        for entity in external:
            print(f"    ? {entity}")

    print()
    if problems:
        print(f"Найдено проблем: {len(problems)}\n")
        for item in problems:
            print(f"  ✗ {item}")
        return 1

    print("Все ссылки на собственные сущности разрешаются, дубликатов нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
