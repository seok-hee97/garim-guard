"""발표자료 PPTX 의 OOXML 스키마 적합성 고정 — PowerPoint '복구' 대화상자 회귀 방지.

회귀 이력: PptxGenJS 4.0.1 은 한 문단에 run 이 여러 개면 **run 마다 `<a:pPr>` 를 뱉는다**.
DrawingML CT_TextParagraph 에서 pPr 은 문단당 정확히 1회, 그것도 **맨 앞** 에만 올 수 있다.
XML 자체는 well-formed 라 파서는 통과하고 Keynote·LibreOffice 도 관대하게 열지만,
PowerPoint 는 스키마 검증에서 "프레젠테이션에 문제가 있습니다 / 복구" 로 거부한다.
gen_pptx.mjs 가 빌드 끝에서 중복 pPr 을 걷어내므로 이 테스트가 그 교정을 고정한다.

검증 대상 순서 규칙:
  CT_TextBody      : bodyPr, lstStyle?, p+
  CT_TextParagraph : pPr?, (r|br|fld)*, endParaRPr?
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from src.config import ROOT

DECK = ROOT / "report" / "발표자료.pptx"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
TEXT_PARTS = re.compile(r"^ppt/(slides|notesSlides|slideLayouts|slideMasters)/\w+\.xml$")

pytestmark = pytest.mark.skipif(
    not DECK.exists(), reason="발표자료.pptx 미생성 (cd scripts/pptx && npm run build)"
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_parts():
    with zipfile.ZipFile(DECK) as z:
        for name in z.namelist():
            if TEXT_PARTS.match(name):
                yield name, ET.fromstring(z.read(name))


def test_every_part_is_wellformed_xml():
    with zipfile.ZipFile(DECK) as z:
        assert z.testzip() is None, "zip 컨테이너 손상"
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(z.read(name))       # 예외 나면 실패


def test_paragraph_child_order_matches_schema():
    """★pPr 은 문단당 1회·맨 앞, endParaRPr 은 맨 뒤★ — PowerPoint 손상 판정의 직접 원인."""
    bad: list[str] = []
    for name, root in _text_parts():
        for para in root.iter(f"{A}p"):
            kids = [_local(c.tag) for c in para]
            if kids.count("pPr") > 1:
                bad.append(f"{name}: pPr {kids.count('pPr')}회 — {kids[:8]}")
            elif "pPr" in kids and kids.index("pPr") != 0:
                bad.append(f"{name}: pPr 이 맨 앞이 아님 — {kids[:8]}")
            if "endParaRPr" in kids and kids.index("endParaRPr") != len(kids) - 1:
                bad.append(f"{name}: endParaRPr 이 맨 뒤가 아님 — {kids[:8]}")
    assert not bad, "OOXML 문단 순서 위반:\n" + "\n".join(bad[:12])


def test_textbody_child_order_matches_schema():
    order = {"bodyPr": 0, "lstStyle": 1, "p": 2}
    bad: list[str] = []
    for name, root in _text_parts():
        for body in root.iter(f"{A}txBody"):
            kids = [_local(c.tag) for c in body]
            seq = [order.get(k, 9) for k in kids]
            if 9 in seq or seq != sorted(seq):
                bad.append(f"{name}: {kids[:6]}")
    assert not bad, "OOXML txBody 순서 위반:\n" + "\n".join(bad[:12])


def test_deck_has_no_star_markers_and_keeps_bold():
    """★ 는 서식으로 전환됐다 — 기호가 표면·노트에 남으면 안 되고 볼드 런은 살아 있어야."""
    stars = bold = 0
    with zipfile.ZipFile(DECK) as z:
        for name in z.namelist():
            if re.match(r"^ppt/(slides|notesSlides)/\w+\.xml$", name):
                xml = z.read(name).decode("utf-8")
                stars += xml.count("★")
                bold += xml.count('b="1"')
    assert stars == 0, f"★ 기호가 {stars}개 남아 있다 — starRuns/starMd 전개 누락"
    assert bold > 50, f"볼드 런이 {bold}개뿐 — 강조가 서식으로 옮겨가지 않았다"


def test_pptx_notes_match_script_markdown():
    """★PPT 발표자 노트 == 발표자료_대본.md 대본★ — 두 산출물이 갈리면 발표 중 다른 걸 읽게 된다.

    회귀 이력: S1 만 newSlide() 를 거치지 않고 손으로 만들어져, 노트를 addNotes(PPT용)와
    DECK.notes(대본용)에 따로 적었다. 그 결과 PPT 노트에는 상세 노트만 들어가고 표준 대본·
    용어 블록은 .md 에만 있었다. 한 소스에서 두 산출물을 낸다는 이 파일의 전제가 S1 에서만
    깨져 있었던 것이다.
    """
    md_path = ROOT / "report" / "발표자료_대본.md"
    if not md_path.exists():
        pytest.skip("발표자료_대본.md 미생성")
    md = md_path.read_text("utf-8")
    sections = re.split(r"^## S(\d+)\. ", md, flags=re.M)
    script = {int(sections[i]): sections[i + 1] for i in range(1, len(sections), 2)}

    with zipfile.ZipFile(DECK) as z:
        for no in range(1, 19):
            rels = z.read(f"ppt/slides/_rels/slide{no}.xml.rels").decode("utf-8")
            m = re.search(r'Target="\.\./(notesSlides/notesSlide\d+\.xml)"', rels)
            assert m, f"S{no} 에 발표자 노트가 없다"
            xml = z.read("ppt/" + m.group(1)).decode("utf-8")
            note = "".join(re.findall(r"<a:t>(.*?)</a:t>", xml, re.S))
            note = note.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            body = script[no]
            for block in ("🎬", "📖 [용어·배경]", "[상세 노트"):
                if block in body:
                    assert block in note, f"S{no}: 대본의 '{block}' 블록이 PPT 노트에 없다"
            # 대본 첫 문장이 노트에도 있어야 한다(내용 동일성 표본 검증)
            first = next((l.strip() for l in body.splitlines()
                          if l.strip().startswith("🎬")), "")
            if first:
                probe = first[first.find("]") + 1:].strip()[:24]
                assert probe and probe in note, f"S{no}: 표준 대본 본문이 PPT 노트와 다르다"
