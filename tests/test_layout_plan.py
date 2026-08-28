"""切题切割规划（plan_question_spans）单元测试：跨页残句、大题标题并入。"""

from __future__ import annotations

from kurotutor.services.layout import (
    TextLine,
    _is_section_header,
    plan_question_spans,
)


def _line(text: str, top: int, height: int = 20) -> TextLine:
    return TextLine(text, (40, top, 400, top + height))


def test_section_header_detected():
    assert _is_section_header("三、实验探究题")
    assert _is_section_header("二、填空题")
    assert not _is_section_header("18. 实验探究和推理都是科学研究的基本方法")
    assert not _is_section_header("3、下列说法正确的是")


def test_residual_span_before_first_question():
    lines = [
        _line("（填\"高\"或\"低\"）温物体放出热量，内能___。", 100),
        _line("吸收热量，内能___。", 130),
        _line("16. 端午佳节，粽香万里。", 200),
        _line("17. 在生活中我们要勤洗手。", 300),
    ]
    residual, spans = plan_question_spans(lines)
    assert residual is not None, "首题号之前的跨页残句应单独成块"
    top, bottom = residual
    assert top <= 100 and bottom >= 150
    assert len(spans) == 2  # 16、17 两题


def test_no_residual_when_first_line_is_question():
    lines = [
        _line("1. 解方程 x² - 5x + 6 = 0", 60),
        _line("步骤：因式分解", 90),
        _line("2. 已知 a=3, b=4, 求 c", 160),
    ]
    residual, spans = plan_question_spans(lines)
    assert residual is None
    assert len(spans) == 2


def test_section_header_excluded_from_questions():
    lines = [
        _line("17. 在生活中我们要勤洗手。", 100),
        _line("三、实验探究题", 180),
        _line("18. 实验探究和推理都是科学研究的基本方法。", 240),
        _line("19. 如图 A 所示。", 400),
    ]
    residual, spans = plan_question_spans(lines)
    assert residual is None
    assert len(spans) == 3, "标题不单独成块：17 / 18 / 19"
    top_17, bottom_17 = spans[0]
    top_18, _ = spans[1]
    assert bottom_17 <= 180, "17 题不应把大题标题包进去"
    assert top_18 > 200, "18 题从自身题号行开始，不含大题标题"


def test_empty_lines():
    residual, spans = plan_question_spans([])
    assert residual is None
    assert spans == []
