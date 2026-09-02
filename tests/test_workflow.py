"""
Проверки самого workflow — того файла, который проверяет всё остальное.

Поводом послужила настоящая поломка. Шаг

    - uses: actions/setup-python@v7
      with:
        cache: pip

выглядит безобидно, но встроенный кэш ищет зависимости только в
**/requirements.txt и **/pyproject.toml. У нас файл называется
requirements-dev.txt, поэтому действие не находило ничего и завершалось
ошибкой «No file ... matched» — а вместе с ним падала и вся задача.
Все шесть задач, ставящих зависимости, падали ещё до первой проверки.

Заметить это по локальному прогону нельзя: локально никакого кэша нет.
Поэтому условие вынесено в тест.

Вторая проверка — про версии действий. Dependabot заводит по PR на каждое
действие, ветки создаются от одного и того же main, и слияние второго PR
откатывает правки первого. Так в main оказалось шесть checkout@v4 рядом
с одним checkout@v7. Расхождение версий внутри файла — признак того, что
слияние прошло не полностью.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def steps(workflow: dict):
    """Все шаги всех задач: (имя задачи, шаг)."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_name, step


@pytest.fixture(params=WORKFLOWS, ids=lambda p: p.name)
def workflow(request) -> dict:
    return yaml.safe_load(request.param.read_text(encoding="utf-8"))


def test_at_least_one_workflow_exists():
    assert WORKFLOWS, "каталог .github/workflows пуст — проверки не запускаются вовсе"


def test_pip_cache_knows_dependency_path(workflow):
    for job_name, step in steps(workflow):
        with_ = step.get("with") or {}
        if not with_.get("cache"):
            continue
        path = with_.get("cache-dependency-path")
        assert path, (
            f"задача «{job_name}»: включён cache без cache-dependency-path — "
            f"действие не найдёт requirements-dev.txt и упадёт"
        )
        assert (ROOT / path).is_file(), (
            f"задача «{job_name}»: cache-dependency-path указывает на {path}, "
            f"а такого файла в репозитории нет"
        )


def test_action_pinned_to_single_version(workflow):
    versions: dict[str, set[str]] = {}
    for _, step in steps(workflow):
        uses = step.get("uses")
        if not uses or "@" not in uses:
            continue
        action, ref = uses.rsplit("@", 1)
        versions.setdefault(action, set()).add(ref)

    for action, refs in versions.items():
        assert len(refs) == 1, (
            f"{action} используется сразу в версиях {', '.join(sorted(refs))} — "
            f"похоже, слияние PR Dependabot откатило часть правок"
        )


def test_pip_install_targets_existing_requirements(workflow):
    for job_name, step in steps(workflow):
        run = step.get("run") or ""
        if "pip install" not in run:
            continue
        for match in re.findall(r"pip install\s+-r\s+(\S+)", run):
            assert (ROOT / match).is_file(), (
                f"задача «{job_name}»: pip install -r {match}, "
                f"а файла {match} в репозитории нет"
            )


def join_continuations(script: str) -> list[str]:
    """
    Склеивает строки, перенесённые обратным слешем.

    Без этого команда, разбитая на две строки, разбирается по половинке:
    shlex спотыкается о висящий слеш, а путь к скрипту с продолжения
    остаётся незамеченным — его `python` уехал на предыдущую строку.
    """
    joined: list[str] = []
    buffer = ""
    for line in script.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined.append(buffer + stripped)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def test_every_invoked_script_exists(workflow):
    """
    Задача может звать скрипт, которого в репозитории нет: файл
    переименовали, а workflow забыли. Узнавать об этом на GitHub незачем.
    """
    import shlex

    for job_name, step in steps(workflow):
        for line in join_continuations(step.get("run") or ""):
            parts = shlex.split(line, comments=True)
            for index, word in enumerate(parts):
                if word.endswith(".py") and index and parts[index - 1].startswith("python"):
                    assert (ROOT / word).is_file(), (
                        f"задача «{job_name}» запускает {word}, "
                        f"а такого файла в репозитории нет"
                    )


def test_every_job_has_checkout(workflow):
    for job_name, job in (workflow.get("jobs") or {}).items():
        uses = [(s.get("uses") or "") for s in job.get("steps") or []]
        assert any(u.startswith("actions/checkout@") for u in uses), (
            f"задача «{job_name}» работает без actions/checkout — "
            f"проверять ей будет нечего"
        )
