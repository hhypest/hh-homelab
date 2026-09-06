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
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


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
    """
    Путь может быть многострочным: с появлением constraints.txt ключ кэша
    обязан зависеть от обоих файлов. Иначе правка ограничений достанет
    из кэша прежний набор пакетов — и проверка пройдёт не на том, что
    записано в репозитории.
    """
    for job_name, step in steps(workflow):
        with_ = step.get("with") or {}
        if not with_.get("cache"):
            continue
        path = with_.get("cache-dependency-path")
        assert path, (
            f"задача «{job_name}»: включён cache без cache-dependency-path — "
            f"действие не найдёт requirements-dev.txt и упадёт"
        )
        listed = [line.strip() for line in str(path).splitlines() if line.strip()]
        for item in listed:
            assert (ROOT / item).is_file(), (
                f"задача «{job_name}»: cache-dependency-path указывает на {item}, "
                f"а такого файла в репозитории нет"
            )
        assert "constraints.txt" in listed, (
            f"задача «{job_name}»: в ключе кэша нет constraints.txt — "
            f"правка версий не сбросит кэш, и проверки пойдут на старом наборе"
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


# -----------------------------------------------------------------------------
# Безопасность выпуска
# -----------------------------------------------------------------------------
# Замечание R-02 независимого аудита. В шаге проверки формата версии стояло
#
#     version='${{ inputs.version }}'
#
# и подстановка выполнялась ДО регулярного выражения: одинарная кавычка
# во вводе обрывала строку, а остаток доставался оболочке — в задаче,
# у которой было право ставить теги. Проверки формата это не спасало,
# потому что она шла уже после.


def test_no_expression_interpolation_in_run(workflow):
    """
    Значения из ввода и контекста передаются через env, а не подставляются
    в текст скрипта. Подстановка происходит до запуска оболочки, поэтому
    любая проверка внутри скрипта заведомо опаздывает.
    """
    for job_name, step in steps(workflow):
        run = step.get("run") or ""
        assert "${{" not in run, (
            f"задача «{job_name}», шаг «{step.get('name', 'без имени')}»: "
            f"выражение подставляется прямо в скрипт — передайте значение через env"
        )


def release() -> dict:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))


def test_release_write_permission_is_isolated():
    """
    Право записи — ровно у одной задачи. Проверки ставят зависимости из сети
    и запускают их код; делать это там, где можно поставить тег, незачем.
    """
    doc = release()
    assert (doc.get("permissions") or {}).get("contents") == "read", (
        "у workflow по умолчанию должно быть только чтение"
    )
    writers = [
        name
        for name, job in (doc.get("jobs") or {}).items()
        if ((job.get("permissions") or {}).get("contents") == "write")
    ]
    assert len(writers) == 1, (
        f"право записи должно быть ровно у одной задачи, а оно у: {writers or 'ни одной'}"
    )


def test_release_write_job_installs_nothing():
    """
    В задаче с правом записи нечего ставить из сети — описание собирается
    тем, что уже есть в образе runner.

    Счётчик здесь не для красоты. Пока права выдавались всему workflow,
    а не задаче, цикл не находил ни одной подходящей задачи и тест
    проходил вхолостую — то есть молчал бы и на настоящей поломке.
    """
    doc = release()
    checked = 0
    for name, job in (doc.get("jobs") or {}).items():
        if (job.get("permissions") or {}).get("contents") != "write":
            continue
        checked += 1
        for step in job.get("steps") or []:
            run = step.get("run") or ""
            assert "pip install" not in run, (
                f"задача «{name}» имеет право записи и ставит зависимости — разделите их"
            )
    assert checked == 1, (
        f"проверять было нечего: задач с правом записи найдено {checked}. "
        f"Права должны стоять на задаче, а не на всём workflow"
    )


def test_release_only_from_main():
    """workflow_dispatch запускается на выбранном ref, поэтому без явной
    проверки тег можно поставить с любой ветки."""
    doc = release()
    scripts = [
        step.get("run") or ""
        for job in (doc.get("jobs") or {}).values()
        for step in job.get("steps") or []
    ]
    assert any("refs/heads/main" in run for run in scripts), (
        "нет проверки, что выпуск идёт из main"
    )


# -----------------------------------------------------------------------------
# Воспроизводимость и закрепление действий
# -----------------------------------------------------------------------------
# Замечание R-06 независимого аудита, две половины.
#
# Первая: действия стояли под изменяемым тегом (@v7). Тег можно переставить,
# и в задаче с правом ставить теги выполнился бы чужой коммит. SHA неизменяем.
#
# Вторая: requirements-dev.txt задаёт только нижние границы, поэтому CI ставил
# то, что вышло сегодня. Один и тот же коммит мог быть зелёным вчера и красным
# сегодня — без единой правки в репозитории, и локально это не воспроизводится.

SHA = re.compile(r"^[0-9a-f]{40}$")


def test_actions_pinned_by_commit_sha(workflow):
    for job_name, step in steps(workflow):
        uses = step.get("uses")
        if not uses or "@" not in uses:
            continue
        action, ref = uses.rsplit("@", 1)
        assert SHA.match(ref), (
            f"задача «{job_name}»: {action} закреплено по «{ref}» — это изменяемая "
            f"ссылка. Нужен полный SHA коммита, а версия — комментарием рядом"
        )


def test_pinned_actions_keep_a_readable_version_comment():
    """
    Голый SHA нечитаем: по нему не видно ни версии, ни того, насколько
    закрепление устарело. Комментарий рядом Dependabot обновляет сам.
    """
    for path in WORKFLOWS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "uses:" not in line or "@" not in line:
                continue
            ref = line.split("@", 1)[1]
            if not SHA.match(ref.split()[0] if ref.split() else ""):
                continue
            assert "#" in ref, (
                f"{path.name}:{number}: закрепление по SHA без комментария с версией"
            )


def test_installs_are_constrained(workflow):
    """
    Каждая установка зависимостей обязана идти с файлом ограничений.
    Забытый -c в новой задаче возвращает ровно ту неопределённость,
    ради которой файл и заведён.
    """
    checked = 0
    for job_name, step in steps(workflow):
        run = step.get("run") or ""
        if "pip install" not in run:
            continue
        checked += 1
        assert "-c constraints.txt" in run, (
            f"задача «{job_name}»: pip install без -c constraints.txt — "
            f"поставится то, что вышло сегодня"
        )
    if checked:
        assert (ROOT / "constraints.txt").is_file(), "constraints.txt нет в репозитории"


def test_constraints_pin_exact_versions():
    """
    Файл ограничений с «>=» бесполезен: он ничего не закрепляет.
    Проверяется каждая содержательная строка, а не только несколько.
    """
    lines = [
        line.strip()
        for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines, "constraints.txt пуст — закреплять нечего"
    loose = [line for line in lines if "==" not in line]
    assert not loose, (
        f"в constraints.txt строки без точной версии: {', '.join(loose)}"
    )


def test_constraints_cover_every_direct_dependency():
    """
    Прямая зависимость, не попавшая в ограничения, обновится молча —
    то есть дыра останется ровно там, где её проще всего не заметить.
    """
    def names(path: str) -> set[str]:
        found = set()
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            found.add(re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower().replace("_", "-"))
        return found

    missing = names("requirements-dev.txt") - names("constraints.txt")
    assert not missing, (
        f"в constraints.txt нет прямых зависимостей: {', '.join(sorted(missing))}"
    )
