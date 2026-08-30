#!/usr/bin/env python3
"""
Рендер Liquid-шаблонов Пачки на примерах полезной нагрузки.

Зачем
-----
Шаблон живёт на стороне Пачки, и единственный способ проверить его там —
дождаться настоящего события. Это долго и неудобно. Скрипт прогоняет
шаблоны локально на сохранённых примерах: синтаксическая ошибка и
опечатка в имени поля видны сразу.

Использование:
    python3 scripts/render_pachca.py              # прогнать всё и показать
    python3 scripts/render_pachca.py --check      # тихо, только код возврата
    python3 scripts/render_pachca.py radarr       # только шаблоны с этим словом

Соответствие «шаблон → примеры» определяется префиксом имени файла:
samples/radarr-*.json проверяются шаблоном radarr.liquid, и так далее.
Файлы с префиксом любого сервиса дополнительно прогоняются через
media-router.liquid — он должен уметь разобрать всё.
"""

from __future__ import annotations

import json
import pathlib
import sys

try:
    from liquid import Environment
except ImportError:
    sys.exit("Нужен python-liquid: pip install python-liquid")

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACHCA = ROOT / "pachca"
SAMPLES = PACHCA / "samples"
ROUTER = PACHCA / "media-router.liquid"

SERVICES = ["radarr", "prowlarr", "jellyfin", "seerr"]


def render(template_path: pathlib.Path, payload: dict) -> str:
    env = Environment()
    template = env.from_string(template_path.read_text(encoding="utf-8"))
    return template.render(**payload).strip()


def main() -> int:
    quiet = "--check" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]

    problems: list[str] = []
    rendered = 0

    for sample in sorted(SAMPLES.glob("*.json")):
        service = sample.stem.split("-")[0]
        if service not in SERVICES:
            problems.append(f"{sample.name}: префикс «{service}» не соответствует ни одному шаблону")
            continue
        if wanted and not any(w in sample.stem for w in wanted):
            continue

        payload = json.loads(sample.read_text(encoding="utf-8"))

        targets = [PACHCA / f"{service}.liquid"]
        if ROUTER.exists():
            targets.append(ROUTER)

        for template in targets:
            if not template.exists():
                problems.append(f"{template.name}: шаблон не найден")
                continue
            try:
                text = render(template, payload)
            except Exception as err:
                problems.append(f"{template.name} на {sample.name}: {type(err).__name__}: {err}")
                continue

            rendered += 1

            if not text:
                problems.append(f"{template.name} на {sample.name}: пустое сообщение — Пачка такое не примет")
            if len(text.encode("utf-8")) > 40000:
                problems.append(f"{template.name} на {sample.name}: длиннее 40 000 байт")

            if not quiet and template != ROUTER:
                print("─" * 68)
                print(f"{sample.name}  →  {template.name}")
                print("─" * 68)
                print(text)
                print()

    print(f"Отрендерено: {rendered}. Проблем: {len(problems)}.")
    for item in problems:
        print(f"  ✗ {item}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
