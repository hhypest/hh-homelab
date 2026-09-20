"""
Схема в README должна оставаться узкой.

README читают с телефона, а телефон вписывает mermaid-схему по ширине
экрана — примерно 360 точек. Дальше всё решает собственная ширина схемы:
она делится на 360, и на столько же уменьшаются подписи. Прежняя схема
рендерилась в 1137×853 (mermaid-cli 11): масштаб 0,32, подписи в пять
пикселей, читать невозможно. Нынешняя — 563×780, масштаб 0,64.

Ширину задают три вещи: сколько узлов встаёт в один ряд, длина подписей
и длина заголовков подграфов. Прямо измерить ширину здесь нельзя —
для этого нужен браузер и сеть, а проверки обходятся без того и другого.
Поэтому проверяются причины, а не следствие: на них схема и распухла
в прошлый раз, когда в один узел уместили шесть имён сервисов.

Пределы взяты с запасом к нынешней схеме, но так, чтобы прежняя их
нарушала: «Jellyfin · Radarr · Prowlarr» — 28 символов в строке подписи,
и четвёртый подграф, вложенный в третий.
"""

from __future__ import annotations

import re

from conftest import ROOT

README = ROOT / "README.md"

# Больше ряда из двух узлов телефон уже не вмещает. Вертикальная схема
# растёт вниз, а вниз страница и так прокручивается.
DIRECTION = "TB"
NODES = 8
SUBGRAPHS = 3
CAPTION = 20
HEADING = 34

NODE = re.compile(r"^\s*(\w+)\[(.+?)\]\s*$")
SUBGRAPH = re.compile(r"^\s*subgraph\s+\w+\s*\[(.+?)\]\s*$")


def diagram() -> list[str]:
    """Строки единственного mermaid-блока README."""
    text = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
    assert len(blocks) == 1, f"ожидался один mermaid-блок, найдено {len(blocks)}"
    return blocks[0].splitlines()


def test_diagram_grows_downward() -> None:
    """flowchart LR разложит то же самое в ширину — на телефоне это конец."""
    first = diagram()[0].strip()
    assert first == f"flowchart {DIRECTION}", (
        f"схема объявлена как «{first}»: на телефоне её вписывают по ширине, "
        f"поэтому расти она должна вниз"
    )


def test_node_captions_are_short() -> None:
    for line in diagram():
        matched = NODE.match(line)
        if not matched:
            continue
        for part in matched[2].split("<br/>"):
            assert len(part) <= CAPTION, (
                f"подпись «{part}» — {len(part)} символов при пределе {CAPTION}. "
                f"Узел растянет весь ряд, и схема уедет за край экрана"
            )


def test_subgraph_titles_are_short() -> None:
    for line in diagram():
        matched = SUBGRAPH.match(line)
        if not matched:
            continue
        assert len(matched[1]) <= HEADING, (
            f"заголовок «{matched[1]}» — {len(matched[1])} символов при пределе "
            f"{HEADING}. Рамка не может быть уже своего заголовка"
        )


def test_nodes_and_subgraphs_are_few() -> None:
    lines = diagram()
    nodes = [line for line in lines if NODE.match(line)]
    subgraphs = [line for line in lines if SUBGRAPH.match(line)]
    assert len(nodes) <= NODES, (
        f"узлов {len(nodes)} при пределе {NODES}: подробности принадлежат "
        f"обзору проекта, README — указатель"
    )
    assert len(subgraphs) <= SUBGRAPHS, (
        f"подграфов {len(subgraphs)} при пределе {SUBGRAPHS}: вложенные рамки "
        f"складываются по ширине, и каждая добавляет отступы"
    )


def test_detailed_diagram_is_not_lost() -> None:
    """Из README убраны контейнеры и порты — читателю сказано, где они."""
    text = README.read_text(encoding="utf-8")
    end = text.index("```", text.index("```mermaid") + 10) + 3
    assert "overview.html" in text[end : end + 1200], (
        "после схемы нет ссылки на подробную: упрощение превратилось в потерю"
    )
