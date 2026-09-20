"""
Раздел про DTS в документации описывает механизм, а не догадку.

История такая. Сначала в шаге 7.5 было написано: «Android-TV приставки почти
всегда имеют собственный декодер DTS, поставьте на приставке PCM вместо
Bitstream». Отчёт о медиавозможностях этой приставки показал обратное:
в списке декодеров устройства DTS нет вообще, а транскод снимается
переопределением профиля в самом клиенте Jellyfin. Совет про PCM верен для
другого железа и на этой паре устройств не работает — что честно признавала
соседняя страница про переносимость, противореча шагу 7.5.

Здесь проверяется не красота текста, а то, что страница называет рабочую
настройку и не возвращается к прежнему совету: такую правку легко потерять
при следующей переработке раздела, и потерю никто не заметит — текст
останется гладким.
"""

from __future__ import annotations

import re

from conftest import ROOT

MEDIA = ROOT / "docs" / "media-stack.html"
PORTABILITY = ROOT / "docs" / "portability.html"


def dts_section() -> str:
    """Кусок шага 7.5 от заголовка про звук до конца шага."""
    text = MEDIA.read_text(encoding="utf-8")
    start = text.index("Звук: почему фильмы с DTS встают")
    end = text.index('data-key="m7-6"', start)
    return text[start:end]


def test_setting_that_removes_transcoding_is_named():
    """
    Имя пункта берём как в клиенте: перевода у него нет, и на экране он
    выглядит по-английски. Без этой строки читателю нечего искать в меню.
    """
    section = dts_section()
    assert "Bitstream Digital Theater System" in section
    assert "«Включено»" in section, "не сказано, в какое положение переводить переопределение"


def test_audio_mode_stays_direct_is_stated():
    """
    Понижающее микширование ограничивает профиль двумя каналами и выбрасывает
    из него AC-3 — то есть чинит одно и ломает другое. Предупреждение об этом
    важнее самой настройки.
    """
    section = dts_section()
    assert "«Напрямую»" in section
    assert "AC-3" in section and "микширование" in section


def test_pcm_is_no_longer_presented_as_the_cure():
    """
    Прежний совет остался в тексте как оговорка про другую ручку — это нормально.
    Недопустимо другое: снова представить его способом убрать транскод.
    """
    section = dts_section()
    assert not re.search(r"поставить\s+<b>PCM</b>\s+вместо", section), (
        "вернулся совет переключать вывод приставки в PCM как лечение транскода"
    )


def test_pages_do_not_contradict_each_other():
    """
    Про отсутствие у приставки своего декодера DTS сказано на обеих страницах,
    и обе должны объяснять это одинаково — встроенным в клиент FFmpeg.
    """
    section = dts_section()
    wrap = PORTABILITY.read_text(encoding="utf-8")
    assert "FFmpeg" in section
    assert "FFmpeg" in wrap, "страница переносимости всё ещё обещает лечение выводом в PCM"


def test_verification_by_report_is_explained():
    """
    Проверка должна быть по отчёту из шага 7.6, а не «на глаз»: в профиле
    видно и сам кодек, и число каналов.

    Отчёт по шагу 7.6 снимается до всякой настройки, и в нём <code>dts</code>
    как раз отсутствует. Поэтому проверка обязана просить второй отчёт —
    иначе читатель, идущий по чек-листу по порядку, увидит «не сработало»
    там, где всё сделано правильно.

    И требовать она должна только тот кодек, который включается по инструкции.
    Соседнее переопределение для TrueHD никто включать не просил: если ждать
    <code>truehd</code> в профиле, верная настройка выглядит как провал.
    """
    section = dts_section()
    assert "7.6" in section
    assert "DirectPlayProfiles" in section and "AudioChannels" in section
    assert "повторно" in section or "ещё раз" in section, (
        "не сказано, что отчёт нужно снять второй раз — после настройки"
    )
    assert "должен появиться <code>dts</code>" in section
    assert not re.search(r"<code>dts</code>\s*и\s*\n?\s*<code>truehd</code>", section), (
        "проверка требует в профиле truehd, включать который инструкция не просила"
    )
