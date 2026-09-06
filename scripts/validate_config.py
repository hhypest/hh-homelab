#!/usr/bin/env python3
"""
Проверка конфигурации без запуска Home Assistant.

Что проверяется
---------------
1. Все YAML-файлы разбираются — с поддержкой тегов HA (!secret, !include,
   !include_dir_named), которые обычный парсер не понимает.
2. Все Jinja-шаблоны внутри значений компилируются. Это ловит незакрытые
   {% if %}, опечатки в фильтрах и потерянные кавычки — самую частую
   причину того, что пакет молча не загружается.

   Раньше здесь вызывался Environment.parse(), и обещание было шире
   проверки: parse разбирает синтаксис, но не разрешает имена. Шаблон
   с опечаткой в фильтре проходил её и падал уже у Home Assistant.
   Теперь вызывается from_string(), то есть шаблон компилируется.

   Одного from_string мало: он разрешает имена фильтров, но не тестов.
   Запись `x is nope` компилируется молча и падает только при выполнении.
   Поэтому имена тестов сверяются отдельно, по разобранному дереву.
3. secrets.yaml не попал под контроль версий.
4. В файлах нет очевидных секретов: токенов Пачки, ключей Jellyfin,
   реальных MAC-адресов.

Полноценную проверку (существуют ли интеграции, верны ли ключи) делает
только сам Home Assistant: Инструменты разработчика → YAML → Проверка
конфигурации. Здесь — быстрый барьер, который ловит 90 % ошибок за секунду
и не требует ни установленного HA, ни его зависимостей.

Использование:
    python3 scripts/validate_config.py
Возвращает 0, если всё хорошо, и 1 при любой находке.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("Нужен PyYAML: pip install pyyaml")

try:
    from jinja2 import Environment, nodes
except ImportError:
    sys.exit("Нужен Jinja2: pip install jinja2")


ROOT = pathlib.Path(__file__).resolve().parent.parent

# Каталоги, которые обходим
YAML_GLOBS = [
    "media/*.yaml",
    "homeassistant/*.yaml",
    "homeassistant/config/*.yaml",
    "homeassistant/config/packages/*.yaml",
]

# Теги Home Assistant, которых нет в обычном YAML
HA_TAGS = [
    "!secret", "!include", "!include_dir_named", "!include_dir_list",
    "!include_dir_merge_list", "!include_dir_merge_named", "!env_var", "!input",
]

# Что не должно попасть в публичный репозиторий
FORBIDDEN = [
    (re.compile(r"api\.pachca\.com/webhooks/[A-Za-z0-9_-]{8,}"), "боевой вебхук Пачки"),
    (re.compile(r'MediaBrowser Token="[0-9a-f]{16,}"'), "боевой ключ Jellyfin"),
    (re.compile(r"\b(?!AA:BB:CC)[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", re.I), "похоже на реальный MAC-адрес"),
]

ALLOWED_IN_EXAMPLES = {"secrets.yaml.example"}


class HALoader(yaml.SafeLoader):
    """SafeLoader, который не спотыкается о теги Home Assistant."""


for _tag in HA_TAGS:
    HALoader.add_constructor(_tag, lambda loader, node: None)


def iter_strings(node):
    """Обходит разобранный YAML и отдаёт все строковые значения."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from iter_strings(key)
            yield from iter_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_strings(item)
    elif isinstance(node, str):
        yield node


# Home Assistant добавляет к Jinja свои фильтры и тесты. Обычный Environment
# о них не знает и на компиляции скажет «No filter named». Поэтому имена
# регистрируются заглушками: здесь важно, что имя существует, а не что оно
# делает — шаблон компилируется, но не выполняется.
#
# Список НАМЕРЕННО не повторяет весь Home Assistant: в нём то, что
# используется в этом репозитории, плюс несколько частых имён. Так проверка
# честно говорит, что знает. Понадобился ещё один фильтр — добавьте его сюда,
# скрипт подскажет это прямым текстом в сообщении об ошибке.
HA_FILTERS = (
    "to_json", "from_json",
    "regex_match", "regex_search", "regex_replace", "regex_findall",
    "as_timestamp", "as_datetime", "as_local",
    "timestamp_custom", "timestamp_local", "relative_time",
    "average", "median", "multiply", "ordinal", "slugify",
    "is_defined", "has_value", "iif",
)

HA_TESTS = (
    "match", "search", "contains", "is_defined", "has_value",
)

HINT = (
    "\n         Если это имя Home Assistant, а не опечатка — "
    "добавьте его в HA_FILTERS или HA_TESTS в этом скрипте"
)


def _stub(*_args, **_kwargs) -> str:
    """Заглушка: имя фильтра должно существовать, вызывать его мы не будем."""
    return ""


def unknown_tests(env: Environment, text: str) -> list[str]:
    """
    Имена тестов, которых окружение не знает.

    from_string ловит неизвестные фильтры, но не тесты: конструкция
    `x is nope_test` компилируется без единого возражения и падает только
    при выполнении, то есть уже внутри Home Assistant. Поэтому имена тестов
    приходится сверять по дереву разбора.
    """
    return sorted(
        {
            node.name
            for node in env.parse(text).find_all(nodes.Test)
            if node.name not in env.tests
        }
    )


def ha_environment() -> Environment:
    """Environment, знающий имена Home Assistant, но не его поведение."""
    env = Environment()
    for name in HA_FILTERS:
        env.filters.setdefault(name, _stub)
    for name in HA_TESTS:
        env.tests.setdefault(name, _stub)
    return env


def main() -> int:
    problems: list[str] = []
    env = ha_environment()

    files: list[pathlib.Path] = []
    for pattern in YAML_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))

    if not files:
        problems.append("не найдено ни одного YAML-файла — проверьте пути в YAML_GLOBS")

    for path in files:
        rel = path.relative_to(ROOT)
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=HALoader)
        except yaml.YAMLError as err:
            problems.append(f"{rel}: не разбирается — {err}")
            continue

        print(f"  YAML   ok  {rel}")

        for text in iter_strings(data):
            if "{{" in text or "{%" in text:
                snippet = " ".join(text.split())[:90]
                try:
                    env.from_string(text)
                except Exception as err:
                    problems.append(
                        f"{rel}: шаблон не компилируется — {err}"
                        f"\n         {snippet}{HINT}"
                    )
                    continue

                for name in unknown_tests(env, text):
                    problems.append(
                        f"{rel}: неизвестный тест «{name}» — "
                        f"падёт при выполнении, а не при загрузке"
                        f"\n         {snippet}{HINT}"
                    )

    # --- секреты в рабочем дереве ---
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()

    for name in tracked:
        if name.endswith("config/secrets.yaml"):
            problems.append(f"{name}: боевой secrets.yaml под контролем версий — уберите его из индекса")

    # --- поиск утечек по содержимому ---
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix in {".png", ".jpg", ".zip"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, label in FORBIDDEN:
            if path.name in ALLOWED_IN_EXAMPLES and label != "боевой вебхук Пачки":
                continue
            found = pattern.search(content)
            if found:
                problems.append(f"{name}: {label} — «{found.group(0)[:48]}»")

    print()
    if problems:
        print(f"Найдено проблем: {len(problems)}\n")
        for item in problems:
            print(f"  ✗ {item}")
        return 1

    print(f"Всё в порядке: проверено файлов — {len(files)}, шаблоны компилируются, секретов не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
