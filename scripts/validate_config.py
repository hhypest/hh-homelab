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
    from jinja2 import Environment
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


def main() -> int:
    problems: list[str] = []
    env = Environment()

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
                try:
                    env.parse(text)
                except Exception as err:
                    snippet = " ".join(text.split())[:90]
                    problems.append(f"{rel}: шаблон не компилируется — {err}\n         {snippet}")

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
