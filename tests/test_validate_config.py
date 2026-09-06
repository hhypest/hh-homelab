"""
Проверки компиляции шаблонов.

Замечание R-05 независимого аудита. Скрипт вызывал Environment.parse()
и печатал «шаблоны компилируются». Это разные вещи: parse разбирает
синтаксис, но не разрешает имена фильтров. Шаблон с опечаткой вроде

    {{ x | definitely_missing_filter }}

проходил проверку и падал уже у Home Assistant — то есть ровно там,
где проверка обещала подстраховать.

Тесты здесь фиксируют само поведение Jinja, из-за которого это случилось.
Их два, потому что дыра оказалась двойной: имена фильтров разрешаются
при компиляции, а имена тестов — только при выполнении. Вторую половину
нашёл упавший тест этого же файла: ожидание, что from_string поймает
и неизвестный тест, оказалось неверным.
"""

from __future__ import annotations

import jinja2
import pytest
import yaml
from conftest import ROOT, load

vc = load(ROOT / "scripts" / "validate_config.py")

BROKEN = "{{ value | definitely_missing_filter }}"


def test_parse_alone_misses_unknown_filter():
    """Причина поломки, зафиксированная исполняемо: parse пропускает то,
    на чём from_string падает."""
    plain = jinja2.Environment()
    plain.parse(BROKEN)  # не падает — именно поэтому проверка молчала
    with pytest.raises(jinja2.TemplateAssertionError):
        plain.from_string(BROKEN)


def test_окружение_отвергает_неизвестный_фильтр():
    with pytest.raises(jinja2.TemplateAssertionError):
        vc.ha_environment().from_string(BROKEN)


BROKEN_TEST = "{% if x is definitely_missing_test %}да{% endif %}"


def test_компиляция_одна_не_ловит_неизвестный_тест():
    """
    Вторая половина той же дыры, найденная упавшим тестом.

    Jinja разрешает имена фильтров при компиляции, а имена тестов — только
    при выполнении. Поэтому одного from_string мало: `x is nope`
    компилируется без возражений и падает уже внутри Home Assistant.
    """
    env = vc.ha_environment()
    env.from_string(BROKEN_TEST)  # не падает — вот почему нужна отдельная сверка
    with pytest.raises(jinja2.TemplateRuntimeError):
        env.from_string(BROKEN_TEST).render(x=1)


def test_неизвестный_тест_находится_по_дереву():
    assert vc.unknown_tests(vc.ha_environment(), BROKEN_TEST) == ["definitely_missing_test"]


def test_известные_тесты_не_считаются_неизвестными():
    env = vc.ha_environment()
    assert vc.unknown_tests(env, "{% if name is search('radarr') %}да{% endif %}") == []
    assert vc.unknown_tests(env, "{% if x is defined %}да{% endif %}") == []


@pytest.mark.parametrize(
    "template",
    [
        "{{ value | to_json }}",
        "{% if name is search('radarr') %}да{% endif %}",
        "{{ value | default('нет') }}",
        "{{ items | count }}",
        "{{ '%02d' % (number | int(0)) }}",
    ],
)
def test_настоящие_шаблоны_компилируются(template):
    """Заглушки не должны мешать: имена Home Assistant и обычный Jinja проходят."""
    vc.ha_environment().from_string(template)


def test_все_шаблоны_репозитория_компилируются():
    """
    Регрессия на случай, если список HA_FILTERS отстанет от конфигурации:
    здесь падает та же проверка, что и в CI, но с именем файла под рукой.
    """
    env = vc.ha_environment()
    files = []
    for pattern in vc.YAML_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    assert files, "не найдено ни одного YAML — проверять было бы нечего"

    checked = 0
    for path in files:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=vc.HALoader)
        for text in vc.iter_strings(data):
            if "{{" not in text and "{%" not in text:
                continue
            checked += 1
            try:
                env.from_string(text)
            except jinja2.TemplateError as err:
                snippet = " ".join(text.split())[:70]
                pytest.fail(f"{path.relative_to(ROOT)}: {err}\n  {snippet}")

    assert checked > 0, "ни одного шаблона не нашлось — проверка прошла вхолостую"


# --- недопустимые escape-последовательности ----------------------------------
# Jinja пропускает содержимое кавычек через unicode-escape, а '\.' там
# последовательностью не является. Python отвечает DeprecationWarning
# и оставляет строку как есть — регулярка работает, и никто ничего
# не замечает. В одной из следующих версий это станет SyntaxError,
# и десять шаблонов перестанут компилироваться разом.

ESCAPE_BAD = r"""{{ states.sensor | selectattr('entity_id', 'search', '^sensor\.x_') | list }}"""
ESCAPE_OK = r"""{{ states.sensor | selectattr('entity_id', 'search', '^sensor\\.x_') | list }}"""


def test_invalid_escape_is_reported() -> None:
    env = vc.ha_environment()
    found = vc.invalid_escapes(env, ESCAPE_BAD)
    assert found, "недопустимая последовательность '\\.' не замечена"
    assert any("escape" in message for message in found), found


def test_doubled_escape_is_clean() -> None:
    env = vc.ha_environment()
    assert vc.invalid_escapes(env, ESCAPE_OK) == []


def test_doubling_the_slash_does_not_change_the_string() -> None:
    """
    Смысл правки в том, что она ничего не меняет: обе записи дают одну и ту же
    строку, а значит и одну и ту же регулярку. Если бы меняла — сломались бы
    все сенсоры, считающие контейнеры, и заметили бы это далеко не сразу.
    """
    import warnings

    env = vc.ha_environment()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # он тут ожидаем
        was = env.from_string(r"{{ '^sensor\.x_' }}").render()
    became = env.from_string(r"{{ '^sensor\\.x_' }}").render()
    assert was == became == r"^sensor\.x_"


def test_repository_templates_have_no_invalid_escapes() -> None:
    """Регрессия: в самих пакетах таких последовательностей больше нет."""
    env = vc.ha_environment()
    problems: list[str] = []
    for path in sorted((ROOT / "homeassistant" / "config" / "packages").glob("*.yaml")):
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=vc.HALoader)
        for text in vc.iter_strings(data):
            if "{{" in text or "{%" in text:
                for message in vc.invalid_escapes(env, text):
                    problems.append(f"{path.name}: {message}")
    assert not problems, problems
