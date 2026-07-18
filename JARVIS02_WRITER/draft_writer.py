"""JARVIS02_WRITER/draft_writer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
블로그 텍스트 대본 생성 — 단일 진입점.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
★ 단일 진입점 원칙 (강제 — 예외 없음)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
시스템 내 *모든* 블로그 텍스트 대본 생성 (LLM Pass-1) 은 이 파일에서만 관리한다.

금지:
  - 다른 .py 파일에 블로그 본문·제목·태그 생성 프롬프트 하드코딩
  - tistory_html_writer / theme_html_writer 에 LLM 직접 호출로
    Pass-1 텍스트 생성 추가
  - 새 블로그 유형/플랫폼 추가 시 html_writer 에 Pass-1 함수 신설

발견 즉시:
  - Pass-1 프롬프트 → 이 파일로 이관
  - 호출자는 `from JARVIS02_WRITER.draft_writer import generate_draft` 로 교체


HTML 조립·SVG 생성·이미지·발행은 각 html_writer / poster 파일이 담당.
이 파일에서 프롬프트·문체·분량·구조를 변경하면
경제 브리핑(티스토리·네이버) + 테마글 모든 대본에 반영된다.

공개 API:
    generate_economic_draft(platform, keyword, sector, reason, supreme_block) -> str
    generate_theme_draft(platform, theme, sector, stocks_data, supreme_block) -> str
    generate_draft(blog_type, platform, **kwargs) -> str  ← 통합 진입점
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.llm import invoke_text

try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L  # 직접 실행 시

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

_TODAY     = date.today()
_TODAY_KR  = _TODAY.strftime("%Y년 %m월 %d일")
_TODAY_DOW = ["월", "화", "수", "목", "금", "토", "일"][_TODAY.weekday()]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  플랫폼 스펙 (문체·독자·제목 스타일)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PLATFORM_SPEC = {
    "tistory": {
        "name": "티스토리 블로그",
        "tone": "격식체(~습니다/~합니다)",
        "reader": "실용적 정보를 찾는 독자",
        "title_style": f"궁금증 유발형 ({_L.TITLE_TISTORY_PROMPT_MAX}자 이내)",
    },
    "naver": {
        "name": "네이버 블로그",
        "tone": "해요체(~해요/~이에요/~더라고요)",
        "reader": "생활 밀착 정보를 찾는 일반 독자",
        "title_style": f"친근하고 생활 밀착형 ({_L.TITLE_PROMPT_MAX}자 이내)",
    },
}

# 하위 호환 (tistory_html_writer / theme_html_writer 기존 import 유지)
_PLATFORM_SPEC = PLATFORM_SPEC


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공통 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def strip_html_wrapper(raw: str) -> str:
    """LLM 출력에서 마크다운·프롬프트 누설 제거."""
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"^```html?\s*\n?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\n?```\s*$", "", s.strip())
    s = re.sub(r"```html?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"```\s*", "", s)
    s = re.sub(r"\*\*([^\*\n]+?)\*\*", r"\1", s)
    s = re.sub(r"^\s*#{1,6}\s+[^\n]*\n", "", s, flags=re.MULTILINE)
    leak_patterns = [
        r"\(?\s*제0[\-A-Z]*조\s*\)?",
        r"\(?\s*제[1-9][\-A-Z]*조[^\)]*\)?",
        r"정확히\s*\d+문장",
        r"이모지\s*없음",
        r"미완성\s*표현\s*없음",
        r"격식체\s*\([^\)]+\)\s*사용",
        r"섹션\s*구성[^.\n]{0,80}",
        r"\d+개\s*차트\s*플레이스홀더[^.\n]{0,40}",
        r"플레이스홀더\s*포함[^.\n]{0,40}",
        r"소제목\s*앞\s*\d+행\s*여백[^.\n]{0,40}",
        r"발행\s*시\s*자동\s*삽입",
        r"섹션\s*\d+\s*\+\s*마무리\s*생성",
        r"변경\s*금지",
    ]
    for pat in leak_patterns:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = re.sub(r"`+([^`\n]{1,200})`+", r"\1", s)
    s = re.sub(r"`+", "", s)
    s = re.sub(r"(?:^|\s)[–\-—]\s*(?:,\s*)?(?=[–\-—]|\s|$)", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"  +", " ", s)
    return s.strip()

# 하위 호환 별칭 (tistory_html_writer 기존 import 유지)
_strip_html_wrapper = strip_html_wrapper


def _inject_missing_charts(html: str, target_count: int, start_idx: int = 1,
                           datasets=None) -> str:
    """CHART 부족 시 미사용 실데이터로 신형식 슬롯([CHART_N]...[/CHART_N]) 삽입.
    datasets 없으면 no-op — 실데이터 없이 차트 생성 금지(규정 12)."""
    existing_nums = sorted(int(m.group(1)) for m in re.finditer(r'\[CHART_(\d+)\]', html))
    existing = len(existing_nums)
    needed = target_count - existing
    ds_list = list(datasets or [])
    if needed <= 0 or not ds_list:
        return html

    next_num = (existing_nums[-1] + 1) if existing_nums else start_idx
    new_slots = []
    for i in range(needed):
        ds_idx = existing + i
        if ds_idx >= len(ds_list):
            break  # 남은 dataset 없음 → 더 삽입 불가
        ds = ds_list[ds_idx]
        data_str = " | ".join(
            f"{str(r.get('label', '')).strip()}={r.get('value', '')}"
            for r in (ds.get("data") or [])   # ★ 데이터포인트 전량 (8개 상한 폐지 2026-07-17)
            if r.get("label") and r.get("value") is not None
        )
        src = ds.get("source") or {}
        src_name = (src.get("name") or src.get("provider") or "").strip()
        new_slots.append(
            f"[CHART_{next_num}]\n"
            f"제목: {ds.get('title', '추가 데이터 시각화')}\n"
            f"단위: {ds.get('unit', '')}\n"
            f"데이터: {data_str}\n"
            f"출처: {src_name}\n"
            f"[/CHART_{next_num}]"
        )
        next_num += 1

    if not new_slots:
        return html

    # 삽입 위치: </p> 뒤 중 차트 닫는 태그([/CHART_N]) 직후가 아닌 곳 (이미지 연속 방지)
    candidates = []
    for m in re.finditer(r'</p>', html, re.IGNORECASE):
        before = html[:m.start()].rstrip()
        if re.search(r'\[/CHART_\d+\]\s*$', before):
            continue
        candidates.append(m.end())

    if not candidates:
        return html + "\n" + "\n".join(new_slots) + "\n"

    # 균등 분산 삽입 (step 간격으로 후보 위치 선택)
    step = max(1, len(candidates) // (len(new_slots) + 1))
    result = html
    offset = 0
    for i, slot in enumerate(new_slots):
        idx = min((i + 1) * step, len(candidates) - 1)
        pos = candidates[idx] + offset
        insert = f"\n{slot}\n"
        result = result[:pos] + insert + result[pos:]
        offset += len(insert)

    return result


def _renumber_charts(html: str) -> str:
    """[CHART_N: ...] 플레이스홀더를 1부터 순서대로 재번호 부여.

    3-call 병합 후 또는 다른 경로로 번호가 뒤섞인 경우 사용.
    """
    counter = [0]
    def _repl(m: re.Match) -> str:
        counter[0] += 1
        return f'[CHART_{counter[0]}: {m.group(1)}]'
    return re.sub(r'\[CHART_\d+:\s*([^\]]+)\]', _repl, html)


def _extract_chart_context(content: str, chart_idx: int) -> str:
    """[CHART_N] 앞뒤 <p> 1개씩 추출 → SVG 생성 컨텍스트 텍스트.

    Pass-2 SVG 생성 시 플레이스홀더 주변 문단을 전달해
    차트가 대본 흐름에 맞게 생성되도록 한다.
    """
    m = re.search(rf'\[CHART_{chart_idx}:[^\]]+\]', content)
    if not m:
        return ""
    before = content[:m.start()]
    after  = content[m.end():]
    p_before = re.findall(r'<p[^>]*>(.*?)</p>', before, re.DOTALL | re.IGNORECASE)
    p_after  = re.findall(r'<p[^>]*>(.*?)</p>', after,  re.DOTALL | re.IGNORECASE)
    parts = []
    if p_before:
        txt = re.sub(r'<[^>]+>', '', p_before[-1]).strip()
        if txt:
            parts.append(f"[앞 문단] {txt}")
    if p_after:
        txt = re.sub(r'<[^>]+>', '', p_after[0]).strip()
        if txt:
            parts.append(f"[뒤 문단] {txt}")
    return "\n".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  감성 도입부 (hook) — 헌법 제1-B조 동적 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _gen_hook(keyword: str, platform: str = "tistory") -> str:
    """경제 브리핑 감성 도입부 1문장 동적 생성."""
    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
    supreme_block = _law_blk()
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    try:
        result = invoke_text(
            "writer",
            f"{supreme_block}\n\n{spec['name']} 글 첫 문장. 키워드: '{keyword}'.\n"
            f"독자({spec['reader']})가 공감할 수 있는 일상 관찰·질문·감성 표현 {_L.build_length_phrase(1)}.\n"
            "★ 이 문장은 근거 데이터 없이 생성되므로 특정 수치·통계 창작 절대 금지 — "
            "연도·분기+금액('2023년 1분기 16만원' 류)·비율(%)·'~배 증가/폭등' 비교·지수·명명된 통계를 "
            "넣지 말 것. 근거 없는 수치는 사실성 게이트에서 차단된다. 정성적 관찰·질문·감성 서술만.\n"
            "마침표로 끝낼 것. 이모지 금지. 문장만 출력.",
            timeout=30,
        ).strip().split("\n")[0].strip()
        return result if result else "요즘 이 주제가 부쩍 화제가 되고 있더라고요."
    except Exception:
        return "요즘 이 주제가 부쩍 화제가 되고 있더라고요."


def _gen_hook_theme(theme: str, platform: str = "tistory") -> str:
    """테마주 감성 도입부 1문장 동적 생성."""
    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
    supreme_block = _law_blk()
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    try:
        result = invoke_text(
            "writer",
            f"{supreme_block}\n\n{spec['name']} 블로그 첫 문장. 테마: '{theme}'.\n"
            f"독자({spec['reader']})가 공감할 수 있는 일상 관찰·궁금증·감성 표현 {_L.build_length_phrase(1)}.\n"
            "★ 이 문장은 근거 데이터 없이 생성되므로 특정 수치·통계 창작 절대 금지 — "
            "연도·분기+금액('2023년 1분기 16만원' 류)·비율(%)·'~배 증가/폭등' 비교·지수·명명된 통계는 물론 "
            "산업·업계 단위 수치(생산능력·감축/증설 톤수·시장 규모·점유율·'○○% 감축/증설 로드맵' 류)까지 "
            "넣지 말 것(날짜가 안 붙어도, 현재 추진 중·업계 전체·로드맵이어도 동일). "
            "근거 없는 수치는 사실성 게이트에서 차단된다. 정성적 관찰·궁금증·감성 서술만.\n"
            "마침표로 끝낼 것. 이모지 금지. 문장만 출력.",
            timeout=30,
        ).strip().split("\n")[0].strip()
        return result if result else f"요즘 '{theme}' 얘기가 부쩍 자주 들리더라고요."
    except Exception:
        return f"요즘 '{theme}' 얘기가 부쩍 자주 들리더라고요."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  테마 종목 데이터 포매터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fmt_marcap(v) -> str:
    if not v:
        return "N/A"
    try:
        v = float(v)
        if v >= 1e12:
            return f"{v / 1e12:.1f}조원"
        if v >= 1e8:
            return f"{v / 1e8:.0f}억원"
        return f"{v:,.0f}원"
    except Exception:
        return "N/A"


def _fmt_price(v) -> str:
    if not v:
        return "N/A"
    try:
        return f"{int(v):,}원"
    except Exception:
        return "N/A"


def _fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    try:
        return f"{float(v) * 100:.1f}%"
    except Exception:
        return "N/A"


def _stocks_text(stocks_data: dict) -> str:
    """종목 데이터를 프롬프트용 표 형식 텍스트로 변환."""
    stocks = (stocks_data or {}).get("stocks", [])
    if not stocks:
        return "(종목 데이터 없음)"

    lines = []
    for s in stocks[:2]:
        rank = s.get("rank") or 0
        label = "대장주" if rank == 1 else "부대장주"
        lines.append(f"\n[{label} — {s.get('name','?')} (rank={rank})]")
        lines.append(f"- 현재가: {_fmt_price(s.get('price'))}")
        lines.append(f"- 시가총액: {_fmt_marcap(s.get('marcap'))}")
        per_v = s.get("per")
        lines.append(f"- PER: {f'{per_v:.1f}배' if per_v else 'N/A'}")
        lines.append(f"- ROE: {_fmt_pct(s.get('roe'))}")
        lines.append(f"- 영업이익률: {_fmt_pct(s.get('op_margin'))}")
        lines.append(f"- 흑/적자: {'흑자' if s.get('is_profit') else '적자'}")
        if s.get("business"):
            lines.append(f"- 사업성: {s['business']}")
        if s.get("tech"):
            lines.append(f"- 기술·경쟁력: {s['tech']}")
        if s.get("relation"):
            lines.append(f"- 타사 관계: {s['relation']}")

    if len(stocks) > 2:
        lines.append(f"\n[나머지 5종목 — 통합 섹션용 (rank 3~{len(stocks)})]")
        lines.append("순위 | 종목명 | 현재가 | 시가총액 | PER | ROE | 흑/적자")
        lines.append("-" * 70)
        for s in stocks[2:]:
            rank = s.get("rank") or 0
            name = s.get("name") or "?"
            price = _fmt_price(s.get("price"))
            marcap = _fmt_marcap(s.get("marcap"))
            per_v = s.get("per")
            per = f"{per_v:.1f}배" if per_v else "N/A"
            roe = _fmt_pct(s.get("roe"))
            prof = "흑자" if s.get("is_profit") else "적자"
            lines.append(f"{rank} | {name} | {price} | {marcap} | {per} | {roe} | {prof}")

    summary = stocks_data.get("summary", {})
    if summary:
        lines.append("")
        lines.append(
            f"요약 — 흑자 {summary.get('profit_count', 0)}개 / 적자 {summary.get('loss_count', 0)}개"
            f" · 대장주: {summary.get('leader_name', '?')}"
            f" · 부대장주: {summary.get('second_name', '?')}"
        )
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  경제 브리핑 텍스트 대본 — 티스토리·네이버 (Pass-1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_corpus_block(docs, max_total: int | None = None, per_doc: int | None = None) -> str:
    """★ 수집 자료 *전문* 주입 (사용자 박제 2026-07-03 — "내용이 풍부해야 퀄리티도 높다").

    자비스09 수집 문서 전부를 대본 프롬프트에 전달 — LLM 이 모든 자료를 보고
    주제·서사·통찰을 구성한다. evidence_brief(수치 규율)와 *병행* 주입.
    신뢰 서열(논문>API>뉴스>기사>웹) 정렬.

    ★ 입력 절단 폐지 (사용자 박제 2026-07-17): per_doc(문서당 자수컷) 기본 None = 전문 그대로.
      max_total 은 *컨텍스트 오버플로 방지용 최후 안전판* 일 뿐 — 초과 시에만 저신뢰부터 통째
      생략(건수 명시). 15건 신뢰 쿼터 규모에선 사실상 발동 안 함.
    """
    if not docs:
        return ""
    import os as _os_c
    if max_total is None:
        max_total = int(_os_c.getenv("DRAFT_CORPUS_MAX_CHARS", "200000") or "200000")

    def _a(d, k, default=""):
        return d.get(k, default) if isinstance(d, dict) else getattr(d, k, default)

    try:
        from JARVIS09_COLLECTOR.models import trust_rank
        docs = sorted(docs, key=lambda d: trust_rank(str(_a(d, "source_type"))))
    except Exception:
        docs = list(docs)

    lines = ["[★ 수집 자료 전문 — 글의 서사·맥락·통찰은 아래 *모든 자료* 를 근거로 "
             "풍부하게 구성하라. 수치 인용 규칙은 실데이터 카탈로그·근거 팩 참조]"]
    used = 0
    included = 0
    for i, d in enumerate(docs, 1):
        _ct = str(_a(d, "cleaned_text") or "").strip()
        body = _ct[:per_doc] if per_doc else _ct   # ★ 수집 원본 전문 (per_doc 절단 폐지 2026-07-17)
        if not body:
            continue
        entry = f"--- 자료 {i} [{_a(d, 'source_type')}] {str(_a(d, 'title'))[:70]}\n{body}"
        if used + len(entry) > max_total:
            break
        lines.append(entry)
        used += len(entry)
        included += 1
    omitted = len(docs) - included
    lines.append(f"(수집 자료 {len(docs)}건 중 {included}건 수록"
                 + (f" — 길이 상한으로 신뢰 낮은 순 {omitted}건 생략" if omitted > 0 else "")
                 + ")")
    return "\n".join(lines)


def _build_data_catalog(datasets) -> str:
    """수집된 실데이터 → 대본 프롬프트용 카탈로그.

    ★ 1-d (2026-07-02): 제목·단위뿐 아니라 *실제 값(라벨:값)·기준일* 까지 주입한다.
      이전엔 제목만 줘서 본문 프로즈의 구체 수치를 LLM 이 지어냈다 — 이제 본문이
      '있는 실데이터 수치만 그대로' 인용하도록 값을 명시한다.
    ★ 프롬프트 상한 (2026-07-03 — ADR 013 넉넉한 수집 도입 후): 세션풀은 전량 보유하되
      카탈로그는 상위 N개만 주입 — 프롬프트 비대로 인한 절단·품질 저하 방지.
    """
    if not datasets:
        return ""
    import os as _os_cat
    # ★ 수집 데이터셋 전량 주입 (사용자 박제 2026-07-17 — 16개 상한 폐지). env 지정 시만 상한 적용.
    _cat_max = int(_os_cat.getenv("DATA_CATALOG_MAX", "0") or "0")
    datasets = list(datasets)
    if _cat_max > 0:
        datasets = datasets[:_cat_max]
    lines = ["[★ 사용 가능한 실데이터 — 차트도 본문 수치도 *이 값만* 인용할 것]"]
    for i, d in enumerate(datasets, 1):
        u = d.get("unit", "")
        src = d.get("source") or {}
        as_of = src.get("as_of", "")
        src_name = (src.get("name") or src.get("provider") or "").strip()
        head = f"D{i}. {d.get('title', '')}{(' (단위 ' + u + ')') if u else ''}"
        if as_of:
            head += f" [기준 {as_of}]"
        if src_name:
            head += f" [출처 {src_name[:40]}]"
        lines.append(head)
        for r in (d.get("data") or []):   # ★ 데이터포인트 전량 주입 (8개 상한 폐지 2026-07-17)
            lbl = str(r.get("label", "")).strip()
            val = r.get("value", "")
            if lbl != "" and val != "":
                lines.append(f"    - {lbl}: {val}{u}")
    # ★ 차트 슬롯 형식 (2026-07-11 — 사용자 박제): LLM은 D번호+제목만 선언,
    #   수치는 JARVIS가 D번호로 실데이터를 직접 가져온다. LLM이 수치를 쓰는 행위 전면 금지.
    lines.append("")
    lines.append("★★ 차트 슬롯 규칙 — 차트가 필요한 자리에 D번호와 제목만 선언한다:")
    lines.append("[CHART_1]")
    lines.append("데이터셋: D2")
    lines.append("제목: <독자에게 보여줄 차트 제목 (한국어, 명확하게)>")
    lines.append("[/CHART_1]")
    lines.append("- 데이터셋: 위 카탈로그(D1, D2, D3...)의 번호를 그대로 적는다 — 수치 연결용.")
    lines.append("- 제목: 인포그래픽 상단에 표시될 실제 제목. D번호가 아닌 한국어로.")
    lines.append("- 데이터·단위·출처·종류 필드 절대 쓰지 말 것.")
    lines.append("  수치는 JARVIS가 D번호로 실데이터를 직접 가져온다 — 네가 적으면 무시된다.")
    lines.append("- 카탈로그에 없는 번호 쓰지 말 것. 같은 D번호로 슬롯 2개 쓰지 마라.")
    lines.append("★ 본문 수치는 카탈로그·근거 팩에 *명시된* 값만 그대로 인용"
                 " (창작·임의 반올림 금지 — 출처 없는 숫자는 거짓이다).")
    return "\n".join(lines)


# ── ★ 동적 설계-우선 (사용자 박제 2026-07-05, ERRORS [376]) ───────────────────────
#   "무턱대고 쓰지 말고, 먼저 이 자료에 맞는 설계를 하고 그 설계대로 써라." 추가 LLM
#   호출 없이 *같은 생성* 안에서 설계→작성(plan-and-solve). 설계는 <design> 블록에 담고
#   발행 전 제거 — 하드코딩 아님(매 글의 자료에 따라 LLM 이 동적으로 설계).
_DESIGN_FIRST_BLOCK = (
    "\n[★ 작성 전 — 먼저 *전문 편집 기획서* 를 작성 (무턱대고 쓰지 말 것)]\n"
    "위 자료(종목 데이터·수집 문서 전문·근거 팩)를 *전문 에디터의 눈* 으로 종합 검토해,\n"
    "이 글만의 *상세 기획서* 를 <design>...</design> 안에 작성하라. 하드코딩이 아니라 *이 자료에\n"
    "맞춰 동적으로* — 기획이 꼼꼼·전문·디테일할수록 다음 작성이 수월하고 품질이 높다.\n"
    "각 항목 1~2줄, 전체 18줄 이내로 밀도 있게:\n"
    "1. [핵심 논지] 이 글이 주는 단 하나의 통찰 + 독자가 *지금* 읽어야 할 시의성.\n"
    "2. [독자 니즈] 이 독자가 궁금해하고 불안해하는 지점 → 글이 그걸 어떻게 풀어주나.\n"
    "3. [섹션별 설계] 각 <h2> 마다: ⓐ 핵심 메시지 1줄 ⓑ *이 자료 중 무엇* 으로 뒷받침 "
    "ⓒ 앞뒤 섹션과의 서사 연결(흐름이 끊기지 않게).\n"
    "4. [이미지 슬롯 설계] 각 [CHART_N] 마다: 카탈로그 D몇 + *그 차트가 보여주는\n"
    "   인사이트 1줄*(왜 이 데이터를 하필 이 자리에). 데이터 없는 슬롯은 [PHOTO_N] 으로.\n"
    "   ★ 종류(bar/line 등)는 적지 말 것 — JARVIS06이 데이터 성격 보고 자율 결정.\n"
    "5. [도입·마무리 전략] 감성 도입부의 구체 앵글(독자 상황에서 시작) + 마무리의 행동·통찰.\n"
    "6. [자료 공백 처리] 수치·근거가 얕은 부분은 어떤 정성 서술(맥락·비교·경향)로 설득력 있게 메울지.\n"
    "그 다음, *네가 짠 그 기획서 그대로* 아래 형식으로 작성하라. "
    "<design> 블록은 기획용 — 발행 본문 아님(시스템이 자동 제거).\n"
)


def _load_learn_insights(scope: str, platform: str = "") -> str:
    """ADR 014 — UCB 선택 인사이트 블록 로드 + 사용 기록. 실패 시 "" (글 작성 절대 안 막음)."""
    try:
        from JARVIS07_GUARDIAN.quality_learner import build_insights_block
        blk = build_insights_block(scope=scope, platform=platform, limit=8)
        return (blk + "\n") if blk else ""
    except Exception as _e:
        print(f"  ⚠️ 학습 지침 로드 실패(무시): {_e}")
        return ""


def _strip_design(raw: str) -> str:
    """대본 응답에서 <design> 설계 블록 제거 (발행 본문 아님, ERRORS [376]).

    ★ 안전판 (ERRORS [381]): LLM 이 본문 전체를 하나의 <design>...</design> 로 감싸거나
      닫는 태그를 맨 끝에 두면, 비탐욕 정규식이 TITLE:/CONTENT: 본문까지 통째로 지워
      '본문 한글 0자' 로 발행이 실패했다. 제거로 본문 마커가 유실되면 TITLE:/CONTENT:
      지점부터 본문을 복원하고, 짝 안 맞는 design 태그 잔존물을 마저 정리한다.
    """
    import re as _re_d
    raw = (raw or "").strip()
    if not raw:
        return ""
    # 1) 정상 <design>...</design> 블록 제거 (비탐욕)
    out = _re_d.sub(r"<design>[\s\S]*?</design>", "", raw, flags=_re_d.I).strip()
    had_body  = ("TITLE:" in raw) or ("CONTENT:" in raw)
    lost_body = had_body and ("TITLE:" not in out) and ("CONTENT:" not in out)
    if not out or lost_body:
        # design 이 본문을 삼킴(전체 래핑·미종료). 본문 마커가 있으면 원본에서 복원하고,
        # 없으면(설계만 쓰고 본문 누락) 빈 문자열 반환 → harness 가 재생성하게 둔다.
        if not had_body:
            return ""
        out = raw
    # 2) 짝 안 맞는 여는/닫는 <design> 태그 잔존물 제거 (본문 유실 없이)
    out = _re_d.sub(r"</?design[^>]*>", "", out, flags=_re_d.I).strip()
    # 3) 본문 마커가 있으면 그 지점부터 = 발행 본문 (선행 설계 텍스트 잔존 차단)
    m = _re_d.search(r"TITLE\s*:|CONTENT\s*:", out, flags=_re_d.I)
    if m:
        return out[m.start():].strip()
    # ★ 마커 없음 (ERRORS [381] 후속 — 결정적 결함): 스로틀로 </design>·TITLE 전에 절단된
    #   *설계 산문* 이 유효 본문인 척 흘러 has_publishable_body 를 '텍스트 블록 0/본문 0자'
    #   3중오류로 실패시키고, _draft_invoke 의 '설계 빼고 재시도(②)' 가드까지 (body 가 비어있지
    #   않아) 무력화됐다. <p>/<h*> 구조가 있으면 마커 없어도 본문 보존, 없으면(=구조 없는 설계
    #   산문) 빈 문자열 → _draft_invoke 가 비인프라면 설계 없이 재시도 / 인프라면 호출자가 분기.
    if _re_d.search(r"<(?:p|h[1-6])\b", out, flags=_re_d.I):
        return out
    return ""


def has_publishable_body(content: str, min_korean: int | None = None) -> bool:
    """Pass-1 대본이 *발행 가능한 본문 구조* 를 갖췄는지 최종 검증.

    ★ 근본 (ERRORS [381] 보강 — 스로틀 절단 응답): `_draft_invoke` 는 빈 응답·`<design>`-only
      는 막지만, 비어있지 않으나 구조가 퇴화한 응답(예: 'TITLE: 제목' 만·CONTENT/<p> 누락)은
      통과시킨다. 이 경우 `if not raw` 검사는 통과하지만 하류 assemble_blocks 가 텍스트 블록
      0개를 만들어 process_draft 가 썸네일 1장만 붙여 '블록 수 부족(1개)/텍스트 블록 없음/
      본문 한글 0자' 3중 오류를 낸다(#2120-2122). 이를 *생성 실패* 로 상류에서 판정 →
      호출자가 return "" → harness 가 draft_failed 로 깔끔히 재생성(스로틀 해소 시 성공).

    판정 기준 (둘 다 만족해야 발행 가능):
      ① <p> 또는 <h1~6> 텍스트 블록이 최소 1개 (assemble_blocks 의 텍스트 블록 생성 전제)
      ② 한글 본문이 최소 min_korean(기본 INDEXER_BODY_MIN≈200자) — Layer3 임계와 동일
    """
    if not content or not content.strip():
        return False
    import re as _re_b
    # ① 발행 본문은 최소 1개의 <p> 또는 <h1~6> 텍스트 블록 필요.
    if not _re_b.search(r"<(?:p|h[1-6])\b[^>]*>[\s\S]*?</(?:p|h[1-6])>", content, _re_b.I):
        return False
    # ② 한글 본문 최소 길이 (Layer3 '본문 한글 N자' 임계와 동일 기준 — 상류 선차단).
    try:
        from JARVIS02_WRITER import length_manager as _Lm
    except ImportError:
        import length_manager as _Lm
    floor = _Lm.INDEXER_BODY_MIN if min_korean is None else min_korean
    return _Lm.count(content) >= floor


def _draft_invoke(system_msg: str, user_msg: str) -> str:
    """설계-우선 대본 1회 호출 + 견고성 가드 (ERRORS [381] — 사용자 박제 2026-07-06).

    설계-우선(<design>)이 스로틀·부분응답 시 *본문 없이 설계만* 반환하면 _strip_design 이
    0자로 만들어 발행이 20분 재시도로 갇히던 문제를 근본 차단:
      ① 빈 응답(스로틀) → 1회 재시도.
      ② <design>만 오고 본문 없음 → 설계 지시 제거한 프롬프트로 1회 재시도 (설계 없이라도 본문 확보).
    → 대본은 *절대 0자로 넘어가지 않는다* (LLM 이 응답만 하면 본문 확보).
    """
    # ★ 스로틀 인지형 (rank3): invoke_text 는 이미 내부에서 백오프 재시도(최대 3)를 한다.
    #   그 위에 여기서 폴백을 *또* 연쇄하면 같은 스로틀 창에 SDK spawn 을 몰아 rate-limit 을
    #   자가 증폭한다. 인프라 사유(스로틀 빈응답/hang/절단)면 폴백을 멈추고 best-effort 만
    #   반환 → 상류(harness)가 defer/backoff 로 처리. *진짜 비인프라 빈응답/설계-only* 일 때만
    #   기존 콘텐츠 폴백(①②) 유지.
    from shared.llm import last_call_infra_incomplete as _infra
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if _infra():
        return _strip_design(raw)                     # 인프라 스로틀 — 같은 창 재발사 금지
    if not (raw or "").strip():                       # ① 진짜 빈 응답(비인프라) → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
        if _infra():
            return _strip_design(raw)
    body = _strip_design(raw)
    if not body and (raw or "").strip():              # ② <design>만·본문 없음 → 설계 빼고 재시도
        _plain = (user_msg.replace(_DESIGN_FIRST_BLOCK, "")
                  if _DESIGN_FIRST_BLOCK in (user_msg or "") else user_msg)
        raw2 = invoke_text("writer", _plain, timeout=300, system=system_msg)
        if _infra():
            return _strip_design(raw2)
        body = _strip_design(raw2) or (raw2 or "").strip()
    return body


def _build_economic_sections(section_plan, mid_emo_phrase: str, chart_start: int = 3) -> str | None:
    """★ 주제별 섹션 스켈레톤 동적 조립 (사용자 박제 2026-07-18) — 고정 '소제목1..N' 탈피.

    topic_pack warm 단계가 선계산한 section_plan(주제 맞춤 섹션명·개수)을 받아 경제 대본
    CONTENT 의 섹션 골격을 매 주제마다 다르게 만든다. 삼중감성 중간 문단(제0-C조)을 섹션
    중앙에 재삽입하고, 차트 슬롯([CHART_n])·데이터 카탈로그 힌트(Dn) 형식을 보존한다.
    면책·generic('섹션 N') 섹션은 제외. 실섹션 2개 미만이면 None → 호출자가 기존 리터럴 폴백.
    """
    secs = []
    for s in (section_plan or []):
        if not isinstance(s, dict):
            continue
        nm = str(s.get("name", "")).strip()
        if not nm or "면책" in nm or re.match(r"^섹션\s*\d+$", nm):
            continue
        secs.append(nm)
    if len(secs) < 2:
        return None
    out, _cn, _mid = [], chart_start, len(secs) // 2
    for i, nm in enumerate(secs):
        out.append(f"<h2>{nm}</h2>")
        out.append(f"<p>{nm} 핵심 2문장.</p>")
        out.append(f"[CHART_{_cn}]\n제목: {nm} 관련 시각화\n단위: (D{_cn} 단위)\n"
                   f"데이터: (D{_cn} 라벨=값)\n출처: (D{_cn} 출처)\n[/CHART_{_cn}]")
        out.append(f"<p>{nm} 부연 2문장.</p>")
        _cn += 1
        if i == _mid:   # ★ 감성 중간 문단 재삽입 (헌법 제0-C조 — 절대 누락 금지)
            out.append(f"<p>(★ 감성 중간 문단 — 본문 중간에 글쓴이의 개인적 소회·공감을 "
                       f"{mid_emo_phrase}. 수치·데이터 없이 감성 서술만. 헌법 제0-C조)</p>")
    return "\n".join(out)


def _gen_economic_ts_nv(
    keyword: str, sector: str, reason: str,
    supreme_block: str,
    platform: str = "tistory",
    datasets=None,
    section_plan=None,
) -> str:
    """티스토리·네이버 경제 브리핑 Pass-1: 텍스트 + [CHART_N] 플레이스홀더.

    ★ 데이터-우선 (사용자 박제 2026-06-30): datasets(미리 수집한 실데이터)가 오면 그 목록을
      카탈로그로 주입 → 대본이 *실제 있는 차트만* 계획 (없는 데이터 상상 금지).
    """
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    # ★ 대본 1회 호출 (ERRORS [373]): 도입부(hook) 별도 LLM 호출 폐지 — 아래 구조에 지시 내장.
    _catalog = _build_data_catalog(datasets)
    _insights = _load_learn_insights("economic", platform)

    # ★ 분량 상한 블록 (2026-07-16 근본 수정 — 41문장 초과 사고): 테마 Pass-1 과 동일 패턴.
    #   숫자는 post_type_specs 단일 소스 파생 (하드코딩 금지, 제8-B조 문장+글자 병기).
    _spec_eco = None
    try:
        from JARVIS02_WRITER.post_type_specs import get_spec as _gs_eco
        _spec_eco = _gs_eco("economic")
    except Exception:
        pass
    _max_sents = _spec_eco.max_sentences if _spec_eco else 40
    _max_kor = _spec_eco.max_korean if _spec_eco else 2000
    _target_sents = _spec_eco.target_sentences if _spec_eco else 30
    _min_sents = _spec_eco.min_sentences if _spec_eco else 20
    _p_cap = max(15, _max_sents // 2)   # 한 <p> 최대 2문장 → <p> 상한 = 문장 상한/2

    system_msg = f"""당신은 한국 경제 블로그의 전문 작가입니다.
이 글은 오늘 트렌드에서 화제인 *경제·금융 상식과 배경 지식*(거시지표·경제 개념·정책·산업 배경)을 쉽게 풀어 설명하는 글입니다.
★ 개별 종목의 재무표(PER·ROE·영업이익률·매출액·시가총액)·대장주/부대장주 선정·특정 종목 매수 추천은 절대 쓰지 마십시오 — 그것은 '테마주 분석' 글의 영역이며, 이 글과는 성격이 완전히 다릅니다. 오늘의 경제/금융 이슈가 *무엇인지·왜 중요한지·배경과 파급 효과*를 독자 눈높이로 풀어 설명하십시오.

{supreme_block}
{_insights}

[★ 분량 — 하한·상한 둘 다 엄수 (하한 미달·상한 초과 모두 응답 거부)]
- **★ 하한 미달 절대 금지: 최소 {_L.build_length_phrase(_min_sents)} 이상 반드시 채운다 — 미달 시 발행 차단·전체 재작성.** 목표 {_L.build_length_phrase(_target_sents)}.
- **★ 절대 상한: {_max_sents}문장 / {_max_kor}자** — 응답 자체 한계. 초과분은 발행 전 검증에서 잘려나간다.
- {_max_kor}자 가까워지면 면책 마무리 후 종료. 단, 목표 분량은 반드시 채운다(성의 없이 짧게 끝내지 말 것).

[절대 제약 — 출력 시 반드시 준수 (위 헌법 블록 전체 적용)]
- <p> 태그 15~{_p_cap}개 (한 <p>에 최대 2문장 — 상한 초과 금지)
- [CHART_N]...[/CHART_N] 데이터 내장 슬롯 {_L.MIN_CHART_COUNT}개 이상 (★ 위 카탈로그 데이터 직접 박기)
  ★ 반드시 [CHART_N] 오프닝 태그로 시작하고 [/CHART_N] 클로징 태그로 닫는다. 오프닝 생략 절대 금지.
  ★ 슬롯 안 필드: 제목 / 단위 / 데이터 / 출처 — 이 4개만. 종류: 필드 쓰지 말 것.
- <svg>·<img> 태그 직접 쓰지 말 것
- 연속 <p>↔<p> 사이마다 슬롯 삽입 (h2 직전·면책 직전 제외)
- 문체: {spec['tone']}
- 위 지시문(괄호 안 설명·헌법 조항 번호·"정확히 N문장" 등) 본문에 그대로 출력 금지 — *완성된 HTML만* 출력"""

    user_msg = f"""[오늘 작성 요청]
플랫폼: {spec['name']} | 독자: {spec['reader']}
날짜: {_TODAY_KR} ({_TODAY_DOW}요일)
키워드: {keyword} | 섹터: {sector} | 급상승 이유: {reason}

[★ 이 글의 정체성 — 경제·금융 상식/배경 설명 (테마주 분석 아님)]
'{keyword}'가 지금 왜 화제인지, 그 뒤에 있는 *경제·금융의 배경지식·개념·원리*를 일반 독자가 "아하" 하고 이해하도록 쉽게 풀어 설명하는 글이다.
- 소제목은 이 주제와 연결된 *경제 개념·배경·파급 효과*로 구성한다 (예: 관련 정책·제도가 작동하는 원리, 시장·산업 메커니즘, 거시지표와의 연결, 알아두면 유용한 경제 상식).
- 개별 기업의 재무(PER·ROE·영업이익·매출액)·대장주 선정·매수 추천은 절대 다루지 않는다 — 그건 '테마주 분석' 글의 영역이다.
- 수치·차트는 주제를 이해시키는 *근거*로만 쓴다. 데이터가 얕으면 억지 수치 대신 개념·맥락·비유로 설득력 있게 설명한다.

{_catalog}

[출력 형식 — 아래 구조 패턴을 따르되, 차트 개수는 글 분량에 맞게 자유 결정]

★ 총 차트: {_L.MIN_CHART_COUNT}~{_L.MAX_CHART_COUNT}개 (소제목 수·단락 수에 따라 자유롭게 배치. 번호는 [CHART_1]부터 순서대로).
★ 문단-이미지 배치 (제4조 허용 패턴): 문단+이미지+문단 / 문단+이미지+문단+이미지+문단 / 문단+문단+이미지+문단 / 문단+이미지+문단+문단. 이미지 연속·문단 3개+ 연속만 금지.
★ 삼중 감성 배치 (헌법 제0조·제0-C조): ① 도입부 감성 오프닝 ② *본문 중간* 감성 문단 1개({_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)}, 글쓴이의 개인적 소회·공감) ③ 감성 마무리 — 글 처음·중간·끝 세 곳에 사람이 직접 쓴 온기(수치 없는 감성 서술).

TITLE: {spec['title_style']}

CONTENT:
<p>감성 오프닝1. 감성 오프닝2. — 독자의 구체적 상황·감정에서 시작 (일반론·AI투 시작 금지).</p>
<p>배경1. 배경2.</p>
[CHART_1]
제목: {keyword} 핵심 지표
단위: (D1 단위 그대로)
데이터: (D1 라벨=값 그대로)
출처: (D1 출처 그대로)
[/CHART_1]
<p>본문1. 본문2.</p>
[CHART_2]
제목: 섹터 비교 또는 추이
단위: (D2 단위 그대로)
데이터: (D2 라벨=값 그대로)
출처: (D2 출처 그대로)
[/CHART_2]
<p>전환.</p>
__SECTIONS__
<p>감성 마무리1. 감성 마무리2. — 단순 요약·행동 지시가 아니라 개인적 소회·독자에게 건네는 따뜻한 인사로 마무리 (헌법 제0-C조 감성 마무리).</p>
<p>(여기에 면책 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} — 본문에 *맞춤형 표현*으로 작성)</p>

{_DESIGN_FIRST_BLOCK}
먼저 <design>설계</design> 블록을 쓰고, *그 다음* 위 형식대로 TITLE: 부터 작성. <design> 외에는 위 출력 형식만 — 설명·주석·코드블록 금지.
"""
    # ★ 동적 섹션 골격 주입 (사용자 박제 2026-07-18): section_plan(warm 선계산 — 주제별 섹션)
    #   있으면 __SECTIONS__ 를 주제 맞춤 구조로, 없으면 기존 고정 스켈레톤으로 안전 폴백.
    _mid_emo = _L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)
    _literal_sections = (
        "<h2>소제목1</h2>\n<p>섹션1 단락.</p>\n"
        "[CHART_3]\n제목: 섹션1 관련 시각화\n단위: (D3 단위)\n데이터: (D3 라벨=값)\n출처: (D3 출처)\n[/CHART_3]\n"
        "<p>섹션1 단락.</p>\n"
        "[CHART_4]\n제목: 섹션1 추가 분석\n단위: (D4 단위)\n데이터: (D4 라벨=값)\n출처: (D4 출처)\n[/CHART_4]   ← 섹션 분량이 길면 차트 더 추가 가능\n"
        f"<p>(★ 감성 중간 문단 — 본문 중간에 글쓴이의 개인적 소회·공감을 {_mid_emo}. 사람이 직접 쓴 듯한 온기 — 수치·데이터 없이 감성 서술만. 헌법 제0-C조)</p>\n"
        "...\n<h2>소제목N</h2>\n<p>마지막 섹션 단락.</p>\n"
        "[CHART_M]\n제목: 마무리 차트\n단위: (Dm 단위)\n데이터: (Dm 라벨=값)\n출처: (Dm 출처)\n[/CHART_M]"
    )
    _dyn_sections = _build_economic_sections(section_plan, _mid_emo) if section_plan else None
    user_msg = user_msg.replace("__SECTIONS__", _dyn_sections or _literal_sections)
    if _dyn_sections:
        print(f"  🧩 [Pass-1/{platform}] 주제별 동적 섹션 {_dyn_sections.count('<h2>')}개 적용")

    _chart_floor = max(8, _L.MAX_CHART_COUNT * 2 // 3)  # 경제: 8~12 범위 하한선
    user_msg = user_msg.replace(
        f"{_L.MIN_CHART_COUNT}~{_L.MAX_CHART_COUNT}",
        f"{_chart_floor}~{_L.MAX_CHART_COUNT}"
    )
    print(f"  ✍️  [Pass-1/{platform}] 텍스트 생성 (system 분리 적용): {keyword}...")
    # ★ 동적 설계-우선 (ERRORS [376]): 같은 호출에서 설계→작성. <design> 블록 제거 후 반환.
    raw = _draft_invoke(system_msg, user_msg)   # ★ 견고성 가드 (빈응답·design-only 방어, ERRORS [381])
    return strip_html_wrapper(raw)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  경제 브리핑 — 섹션별 분할 생성 (병렬 최적화 지원)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_section_system_msg(supreme_block: str, platform: str) -> str:
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    return f"""당신은 한국 경제 블로그의 전문 작가입니다.
이 글은 오늘 트렌드에서 화제인 *경제·금융 상식과 배경 지식*(거시지표·경제 개념·정책·산업 배경)을 쉽게 풀어 설명하는 글입니다.
★ 개별 종목의 재무표(PER·ROE·영업이익률·매출액·시가총액)·대장주/부대장주 선정·특정 종목 매수 추천은 절대 쓰지 마십시오 — 그것은 '테마주 분석' 글의 영역이며, 이 글과는 성격이 완전히 다릅니다. 오늘의 경제/금융 이슈가 *무엇인지·왜 중요한지·배경과 파급 효과*를 독자 눈높이로 풀어 설명하십시오.

{supreme_block}

[절대 제약 — 출력 시 반드시 준수 (위 헌법 블록 전체 적용)]
- 한 <p> 태그에 최대 2문장
- [CHART_N]...[/CHART_N] 슬롯은 *그대로 유지* (내용 채우지 말 것)
  ★ 반드시 [CHART_N] 오프닝 태그로 시작하고 [/CHART_N] 클로징 태그로 닫는다.
  ★ 슬롯 안 필드: 제목 / 단위 / 데이터 / 출처 — 이 4개만. 종류: 필드 절대 금지.
- 문체: {spec['tone']}
- *위 지시문(헌법 조항 번호·"정확히 N문장"·"플레이스홀더 포함" 등) 본문에 그대로 출력 금지* — *완성된 HTML 만* 출력
- 출력 형식 외 설명·주석·코드블록 절대 금지"""


def _gen_section_call1(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
    datasets=None,
) -> str:
    """Call-1: 오프닝 + 섹션1 생성."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    system_msg = _build_section_system_msg(supreme_block, platform)
    _catalog = _build_data_catalog(datasets)
    _call1_min = max(2, _L.MIN_CHART_COUNT // 2)  # 전체 최솟값의 절반 (call-1은 절반 담당)
    user_msg = f"""[작성 요청] {platform} 경제 글 — 오프닝 + 섹션1만 생성

플랫폼: {spec['name']} | 독자: {spec['reader']}
키워드: {keyword} | 섹터: {sector} | 이유: {reason}

{_catalog}
★ CHART 최소 {_call1_min}개 이상 포함. 카탈로그 데이터가 충분하면 더 추가 가능. 번호는 [CHART_1]부터 순서대로.
★ 문단-이미지 배치 (제4조): 이미지 연속 금지, 문단 3개+ 연속 금지. 문단+문단+이미지+문단 / 문단+이미지+문단+문단 패턴 OK.

[출력 형식] — 아래만 생성하고 STOP

TITLE: {spec['title_style']}

CONTENT:
<p>감성 오프닝1. 감성 오프닝2. — 독자의 구체적 상황·감정에서 시작 (일반론·AI투 시작 금지).</p>
<p>배경1. 배경2.</p>
[CHART_1]
제목: {keyword} 핵심 지표
단위: (카탈로그 D1 단위)
데이터: (카탈로그 D1 라벨=값)
출처: (카탈로그 D1 출처)
[/CHART_1]
<p>본문1. 본문2.</p>
[CHART_2]
제목: 섹터 비교 또는 추이
단위: (카탈로그 D2 단위)
데이터: (카탈로그 D2 라벨=값)
출처: (카탈로그 D2 출처)
[/CHART_2]
<p>전환.</p>
<h2>소제목1</h2>
<p>섹션1 단락.</p>
[CHART_3]
제목: 섹션1 관련 시각화
단위: (카탈로그 D3 단위)
데이터: (카탈로그 D3 라벨=값)
출처: (카탈로그 D3 출처)
[/CHART_3]
<p>섹션1 단락.</p>
[CHART_4]
제목: 섹션1 추가 분석
단위: (카탈로그 D4 단위)
데이터: (카탈로그 D4 라벨=값)
출처: (카탈로그 D4 출처)
[/CHART_4]
<p>섹션1 단락.</p>
[CHART_5]
제목: 섹션1 심화 데이터
단위: (카탈로그 D5 단위)
데이터: (카탈로그 D5 라벨=값)
출처: (카탈로그 D5 출처)
[/CHART_5]   ← (카탈로그에 데이터가 충분하면 [CHART_6] 추가 가능)
"""
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if not raw:  # 일시 LLM 장애 → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    result = strip_html_wrapper(raw)
    chart_count = len(re.findall(r'\[CHART_\d+\]', result))
    if chart_count < _call1_min:
        print(f"  ⚠️ [Pass-1 Call-1] CHART 부족 ({chart_count}/{_call1_min}) — 강제 삽입")
        result = _inject_missing_charts(result, _call1_min, 1, datasets)
    return result


def _gen_section_call2(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
    datasets=None,
) -> str:
    """Call-2: 섹션2만 생성."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    system_msg = _build_section_system_msg(supreme_block, platform)
    _catalog = _build_data_catalog(datasets)
    user_msg = f"""[작성 요청] {platform} 경제 글 — 섹션2만 생성 (독립적)

플랫폼: {spec['name']} | 키워드: {keyword} | 섹터: {sector}

{_catalog}
★ CHART 최소 2개 이상 포함. 단락 수·분량에 따라 추가 배치 가능. 번호는 [CHART_6]부터 시작.
★ 문단-이미지 배치 (제4조): 이미지 연속 금지, 문단 3개+ 연속 금지. 문단+문단+이미지+문단 패턴 OK.

[출력 형식] — 섹션2만 생성

<h2>소제목2</h2>
<p>섹션2 단락.</p>
[CHART_6]
제목: 섹션2 관련 시각화
단위: (카탈로그 D6 단위)
데이터: (카탈로그 D6 라벨=값)
출처: (카탈로그 D6 출처)
[/CHART_6]
<p>섹션2 단락.</p>
[CHART_7]
제목: 섹션2 추가 분석
단위: (카탈로그 D7 단위)
데이터: (카탈로그 D7 라벨=값)
출처: (카탈로그 D7 출처)
[/CHART_7]   ← (분량이 길면 차트 추가 가능)
<p>(★ 감성 중간 문단 — 본문 중간에 글쓴이의 개인적 소회·공감 {_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)}, 수치 없이 감성 서술만. 헌법 제0-C조)</p>
"""
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if not raw:  # 일시 LLM 장애 → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    result = strip_html_wrapper(raw)
    chart_count = len(re.findall(r'\[CHART_\d+\]', result))
    _call2_min = max(2, _L.MIN_CHART_COUNT // 4)
    if chart_count < _call2_min:
        print(f"  ⚠️ [Pass-1 Call-2] CHART 부족 ({chart_count}/{_call2_min}) — 강제 삽입")
        result = _inject_missing_charts(result, _call2_min, 6, datasets)
    return result


def _gen_section_call3(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
    datasets=None,
) -> str:
    """Call-3: 섹션3 + 마무리 생성."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    system_msg = _build_section_system_msg(supreme_block, platform)
    _catalog = _build_data_catalog(datasets)
    user_msg = f"""[작성 요청] {platform} 경제 글 — 섹션3 + 마무리 생성

플랫폼: {spec['name']} | 키워드: {keyword} | 섹터: {sector}

{_catalog}
★ CHART 최소 2개 이상 포함. 단락 수·분량에 따라 추가 배치 가능. 번호는 [CHART_8]부터 시작.
★ 문단-이미지 배치 (제4조): 이미지 연속 금지, 문단 3개+ 연속 금지. 문단+이미지+문단+문단 패턴 OK.

[출력 형식] — 섹션3 + 마무리 생성

<h2>소제목3</h2>
<p>섹션3 단락.</p>
[CHART_8]
제목: 섹션3 관련 시각화
단위: (카탈로그 D8 단위)
데이터: (카탈로그 D8 라벨=값)
출처: (카탈로그 D8 출처)
[/CHART_8]
<p>섹션3 단락.</p>
[CHART_9]
제목: 섹션3 마무리 차트
단위: (카탈로그 D9 단위)
데이터: (카탈로그 D9 라벨=값)
출처: (카탈로그 D9 출처)
[/CHART_9]   ← (분량이 길면 차트 추가 가능)
<p>감성 마무리1. 감성 마무리2. — 단순 요약이 아니라 개인적 소회·독자에게 건네는 따뜻한 인사 (헌법 제0-C조 감성 마무리).</p>
<p>(여기에 면책 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} — 본문에 맞춤형 표현으로 작성)</p>
"""
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if not raw:  # 일시 LLM 장애 → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    result = strip_html_wrapper(raw)
    chart_count = len(re.findall(r'\[CHART_\d+\]', result))
    _call3_min = max(2, _L.MIN_CHART_COUNT // 4)
    if chart_count < _call3_min:
        print(f"  ⚠️ [Pass-1 Call-3] CHART 부족 ({chart_count}/{_call3_min}) — 강제 삽입")
        result = _inject_missing_charts(result, _call3_min, 8, datasets)
    return result


def _gen_economic_ts_nv_parallel(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
    datasets=None,
) -> str:
    """티스토리·네이버 Pass-1: 3개 섹션 순차 생성 (rate limit 방지)."""
    print(f"  ⚡ [Pass-1/{platform}] 섹션별 순차 생성 ...")
    with ThreadPoolExecutor(max_workers=1) as executor:
        call1_fut = executor.submit(_gen_section_call1, keyword, sector, reason, supreme_block, platform, datasets)
        call2_fut = executor.submit(_gen_section_call2, keyword, sector, reason, supreme_block, platform, datasets)
        call3_fut = executor.submit(_gen_section_call3, keyword, sector, reason, supreme_block, platform, datasets)
        try:
            call1_content = call1_fut.result(timeout=300)
            call2_content = call2_fut.result(timeout=300)
            call3_content = call3_fut.result(timeout=300)
        except Exception as e:
            print(f"  ❌ [Pass-1/{platform}] 순차 생성 오류: {e}")
            return _gen_economic_ts_nv(keyword, sector, reason, supreme_block, platform, datasets)

    if "CONTENT:" not in call1_content:
        return ""
    title_part, _, content1 = call1_content.partition("CONTENT:")
    sec2_match = re.search(r"<h2>소제목2.*?(?=<h2>|$)", call2_content, re.DOTALL | re.IGNORECASE)
    sec2_content = sec2_match.group(0) if sec2_match else call2_content
    sec3_match = re.search(r"<h2>소제목3.*?$", call3_content, re.DOTALL | re.IGNORECASE)
    sec3_content = sec3_match.group(0) if sec3_match else call3_content
    combined = title_part + "CONTENT:" + content1 + "\n" + sec2_content + "\n" + sec3_content
    combined = _renumber_charts(combined)  # 3-call 병합 후 CHART 번호 1부터 재정렬
    print(f"  ✅ [Pass-1/{platform}] 섹션별 순차 생성 완료")
    return combined


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ★ 리서치 근거 주입 + 서사 아웃라인 + 자기비평 (ADR 012 — 사용자 박제 2026-07-02)
#
#  "수집한 양질의 데이터가 대본에 *전부* 살아 들어가야 한다."
#  기존: 수집 문서 상위 5건 앞부분만 잘라 주입 → 근거 대부분 유실.
#  개선: JARVIS09 EvidencePack(사실 단위·출처 박제) 브리프 주입
#        + 서사 아웃라인 1패스(구조 설계) + 자기비평 1패스(작성 후 다듬기).
#  킬스위치: WRITER_RESEARCH_FIRST=0 (근거팩), WRITER_CRITIQUE=0 (비평 패스)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os as _os


def _build_evidence_block(evidence_pack: dict | None) -> str:
    """EvidencePack → 대본 프롬프트 근거 블록. 실패·미가용 시 빈 문자열 (fail-open)."""
    if not evidence_pack or _os.getenv("WRITER_RESEARCH_FIRST", "1") == "0":
        return ""
    try:
        from JARVIS09_COLLECTOR.evidence_pack import evidence_brief
        return evidence_brief(evidence_pack)
    except Exception as e:
        _g_report("writer", e, module=__name__, func_name="_build_evidence_block")
        return ""


_NARRATIVE_CACHE: dict[str, str] = {}     # theme+date → 아웃라인 (양 플랫폼 재사용)



def _structure_signature(content: str) -> tuple:
    """비평 패스 안전 가드용 구조 시그니처 — 플레이스홀더·표·소제목 보존 검증."""
    charts = sorted(re.findall(r"\[CHART_\d+:", content))
    photos = sorted(re.findall(r"\[PHOTO_\d+:", content))
    tables = len(re.findall(r"<table", content, re.IGNORECASE))
    h2s = len(re.findall(r"<h2", content, re.IGNORECASE))
    return (tuple(charts), tuple(photos), tables, h2s)



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  테마글 텍스트 대본 — 전 플랫폼 (Pass-1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_gate_feedback_block(gate_feedback: list | None) -> str:
    """★ 재작성 순환 피드백 (ERRORS [311]) — 직전 시도의 발행 차단 사유를 Pass-1 프롬프트에 주입.

    없으면 같은 창작 수치를 재생산해 max_attempts 를 그대로 소진한다."""
    items = [str(s).strip() for s in (gate_feedback or []) if str(s).strip()]
    if not items:
        return ""
    lines = "\n".join(f"- {s[:300]}" for s in items[-8:])
    return f"""

★★ 직전 시도 발행 차단 사유 (반드시 반영 — 어길 시 또 차단):
아래 주장·수치는 출처·웹 어디서도 검증되지 않아 발행이 차단됐다. 이번 대본에서는
해당 수치·주장을 *아예 쓰지 말라*. 비슷한 변형(수치만 바꾼 같은 주장)도 금지.
근거 자료에 실재하는 수치로 대체하거나, 수치 없이 정성 서술로 바꿔라.
{lines}"""


def _gen_theme(
    theme: str, sector: str, stocks_data: dict,
    supreme_block: str, platform: str = "tistory",
    collection_docs: list | None = None,
    evidence_pack: dict | None = None,
    gate_feedback: list | None = None,
) -> str:
    """테마글 Pass-1: 텍스트 + 데이터 내장 [CHART_N] 블록 (+구형식 폴백).

    ★ 대본 1회 호출 (사용자 박제 2026-07-05, ERRORS [373]): 도입부(hook)·아웃라인(plan)
      별도 LLM 호출 폐지 — user_msg 에 7섹션 구조·도입부 지시가 이미 완비돼 중복이었다.
      LLM 호출 3→1 로 축소 → rate-limit 압박·프로세스 스폰 오버헤드 대폭 감소.
    """
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    stocks_text = _stocks_text(stocks_data)

    # ★ 데이터 내장 슬롯 카탈로그 (ADR 013 테마 이행 — ERRORS [316]): 경제와 동일 로직.
    #   종목 시세 승격 + 텍스트 수치 승격 → 카탈로그 주입, 슬롯 안에 차트 데이터까지 내장.
    #   카탈로그에 맞는 데이터 없는 슬롯만 구형식 유지 → 자비스06 Pass-2 실데이터 폴백.
    _theme_catalog = ""
    try:
        from JARVIS09_COLLECTOR import stocks_to_datasets as _s2d_t, facts_to_datasets as _f2d_t
        _cat_ds = _s2d_t(stocks_data) + _f2d_t(evidence_pack or {})
        _theme_catalog = _build_data_catalog(_cat_ds)
    except Exception as _ce:
        print(f"  ⚠️ [Theme/Pass-1] 카탈로그 구성 실패 (구형식 슬롯으로 진행): {_ce}")
    if _theme_catalog:
        _theme_catalog += ("\n★ 위 모든 [CHART_N] 슬롯은 *반드시* 블록 형식([CHART_N]...[/CHART_N])으로 "
                           "카탈로그(D1..) 실데이터를 직접 박아 작성. "
                           "카탈로그에 맞는 데이터 없는 슬롯은 [PHOTO_N: 설명] AI 사진으로 대체.")
    summary = (stocks_data or {}).get("summary", {})
    leader = summary.get("leader_name", "")
    second = summary.get("second_name", "")
    others = [s.get("name", "?") for s in (stocks_data or {}).get("stocks", [])[2:]]
    others_csv = ", ".join(others) if others else "(없음)"

    _leader_phrase   = _L.build_length_phrase(_L.THEME_LEADER_SENTS)
    _others_phrase   = _L.build_length_phrase(_L.THEME_OTHERS_SENTS)
    _multi_phrase    = _L.build_length_phrase(_L.THEME_MULTI_SENTS)
    _sector_phrase   = _L.build_length_phrase(_L.THEME_SECTOR_SENTS)
    _strategy_phrase = _L.build_length_phrase(_L.THEME_STRATEGY_SENTS)
    _intro_phrase    = _L.build_length_phrase(_L.INTRO_SENTS_MAX)
    _disc_phrase     = _L.build_length_phrase(_L.DISCLAIMER_SENTS)

    _spec_theme = None
    try:
        from JARVIS02_WRITER.post_type_specs import get_spec as _gs
        _spec_theme = _gs("theme")
    except Exception:
        pass
    _max_sents = _spec_theme.max_sentences if _spec_theme else 40
    _max_kor = _spec_theme.max_korean if _spec_theme else 2000
    _target_sents = _spec_theme.target_sentences if _spec_theme else 32
    _insights = _load_learn_insights("theme", platform)

    system_msg = f"""당신은 한국 테마주 분석 블로그의 전문 작가입니다.

{supreme_block}
{_insights}

[★ 분량 상한 — 절대 초과 금지 (위반 시 응답 자체 거부)]
- 정확히 {_target_sents}문장 (약 {_target_sents * 50}자)
- **★ 절대 상한: {_max_sents}문장 / {_max_kor}자** — 응답 자체 한계.
- {_max_kor}자 가까워지면 즉시 면책 마무리 후 출력 종료.
- 길게 풀어쓰지 말 것. 핵심만 *간결한 문장* 으로.

[절대 제약 — 출력 시 반드시 준수 (위 헌법 블록 전체 적용)]
- [CHART_N]...[/CHART_N] = 데이터 내장 인포그래픽 슬롯 (★ 카탈로그 데이터 직접 박기)
  ★ 반드시 [CHART_N] 오프닝 태그로 시작하고 [/CHART_N] 클로징 태그로 닫는다. 오프닝 생략 절대 금지.
  ★ 슬롯 안 필드: 제목 / 단위 / 데이터 / 출처 — 이 4개만. 종류: 필드 쓰지 말 것.
- [PHOTO_N: 설명] = AI 사진 슬롯 (카탈로그 데이터 없는 슬롯에 사용)
- [PRICE_CHART_LEADER]...[/PRICE_CHART_LEADER] = 대장주 주가 흐름 차트 슬롯. 카탈로그에 "주가 흐름" 데이터가 있으면 반드시 포함하고 데이터 박기. 없으면 [PRICE_CHART_LEADER][/PRICE_CHART_LEADER] 빈 슬롯 유지 (삭제 금지).
- [PRICE_CHART_SECOND]...[/PRICE_CHART_SECOND] = 부대장주 주가 흐름 차트 슬롯. 동일 규칙.
- <svg>·<img> 태그 직접 쓰지 말 것 — 반드시 위 슬롯만 사용
- 문체: {spec['tone']}
- 종목 데이터의 수치는 *그대로 인용* (가공·임의 변경 금지). 없으면 "N/A" 표기.
- ★ 출처 없는 수치 창작 절대 금지 — 특정 연도·분기·기간의 가격·비용·규모·비율·지수뿐 아니라
  *산업·업계 단위 수치*(생산능력·감축/증설 톤수·시장 규모·점유율·"○○% 감축/증설 로드맵" 류)도 포함.
  ★ *거시경제 지표*(기준금리·물가상승률·환율·GDP성장률 등)도 예외 아님 — "누구나 아는 상식"처럼
  느껴져도 이 테마의 수집 자료·종목 데이터에 그 수치가 없으면 절대 인용 금지 (엉뚱한 주제에 갖다 붙인
  거시 통계는 사실성 게이트에서 반드시 차단됨). 날짜가 안 붙어도(현재 추진 중·업계 전체·로드맵 등)
  아래 수집 자료나 종목 데이터에 *명시된 값만* 인용.
  근거 없는 임의 수치는 사실성 게이트에서 차단된다. 없으면 "수치를 확인할 수 없었습니다" 등 정성 서술로 대체.
- ★ 수치 없이도 설득력 있게 서술 — 맥락·경향·비교 표현은 검증 불가 숫자보다 낫다.
  과거 특정 시점 임의 통계("2023년 1분기 ○○원" 류)뿐 아니라 현재·미래 산업 규모 추정
  ("생산능력 25%·370만 톤 감축" 류)도 근거 없으면 생성 금지 — 대신 "구조조정 논의가 진행 중" 식 정성 서술.
- 위 지시문(헌법 조항·"N문장"·"플레이스홀더 포함" 등) 본문에 그대로 출력 금지
- *완성된 HTML 만* 출력. 설명·주석·코드블록 금지.

[문단-이미지 배치 규정 (헌법 제4조 허용 패턴 4가지)]
- 패턴1: 문단 → 이미지 → 문단
- 패턴2: 문단 → 이미지 → 문단 → 이미지 → 문단
- 패턴3: 문단 → 문단 → 이미지 → 문단  (문단 2개 연속 후 이미지 OK)
- 패턴4: 문단 → 이미지 → 문단 → 문단  (이미지 후 문단 2개 연속 OK)
- 금지: 이미지·표 두 개 연속 (예: [CHART_X][CHART_Y]), 문단 3개+ 연속
- 표(<table>)도 시각 요소로 카운트 — 표 뒤에 즉시 차트 금지, 반드시 <p> 1개 삽입 후 차트."""

    # ★ 근거 주입 (ADR 012 → 2026-07-03 확대): EvidencePack 브리프(수치 규율) +
    #   수집 자료 *전문*(서사 재료) 병행 — "내용이 풍부해야 퀄리티도 높다" (사용자 박제).
    _ref_block = ""
    _evidence_block = _build_evidence_block(evidence_pack)
    if _evidence_block:
        _ref_block += f"\n\n{_evidence_block}"
    _corpus_block = build_corpus_block(collection_docs)
    if _corpus_block:
        _ref_block += f"\n\n{_corpus_block}"

    # ★ 아웃라인(plan) 별도 호출 폐지 (ERRORS [373]) — user_msg 의 7섹션 구조가 곧 아웃라인.
    _narrative_block = ""

    user_msg = f"""[오늘 작성 요청 — 테마주 분석 글]
플랫폼: {spec['name']} | 독자: {spec['reader']}
날짜: {_TODAY_KR} ({_TODAY_DOW}요일)
테마: {theme} | 섹터: {sector or '-'}
대장주(시총 1위): {leader} · 부대장주(시총 2위): {second}
나머지 5종목: {others_csv}

[종목 데이터]
{stocks_text}
{_narrative_block}
[★ 본문 구조 — 반드시 이 순서 준수 (★ 분량은 헌법 제8조·length_manager.THEME_TOTAL_SENTS 위임. 배치는 제4조 허용 패턴 4가지)]
★ 차트 총 개수: {_L.THEME_TOTAL_CHART_COUNT}~{_L.MAX_CHART_COUNT}개 (각 섹션 분량에 따라 자유 결정). 번호는 [CHART_1]부터 순서대로.
★ 배치 허용: 문단+이미지+문단 / 문단+이미지+문단+이미지+문단 / 문단+문단+이미지+문단 / 문단+이미지+문단+문단. 이미지 연속·문단 3개+ 연속만 금지.
★ 삼중 감성 배치 (헌법 제0조·제0-C조): ① 도입부 감성 오프닝 ② *본문 중간*(종목 분석 사이) 감성 문단 1개({_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)}, 글쓴이의 개인적 소회·공감) ③ 감성 마무리 — 글 처음·중간·끝 세 곳에 사람이 직접 쓴 온기(수치 없는 감성 서술).

1. 도입부 {_intro_phrase} — <p>2문</p> [CHART_1] <p>2문</p>
2. <h2>대장주 — {leader}</h2> — {_leader_phrase} · 단락-이미지 교대 배치 (표 + 차트 최소 1개, 분량에 따라 추가)
   <p>사업성·주력 2문</p> → <table> → <p>핵심기술·실적 2문</p> → [CHART_2] → <p>투자 포인트</p> → [PRICE_CHART_LEADER](카탈로그 주가이력 데이터)[/PRICE_CHART_LEADER]
3. <h2>부대장주 — {second}</h2> — {_others_phrase} · 단락-이미지 교대 배치 (표 + 차트 최소 1개, 분량에 따라 추가)
   <p>사업성·주력 2문</p> → <table> → <p>핵심기술·실적 2문</p> → [CHART_3] → <p>투자 포인트</p> → [PRICE_CHART_SECOND](카탈로그 주가이력 데이터)[/PRICE_CHART_SECOND]
4. <h2>그 외 주목 종목 5개</h2> — {_multi_phrase} · 단락-이미지 교대 배치 (차트 최소 2개, 분량에 따라 추가)
   <p>종목 1·2 톺아보기 2문</p> → [CHART_4] → <p>종목 3·4 분석 2문</p> → [CHART_5] → <p>종목 5 + 종합 평가</p>
   → <p>(★ 감성 중간 문단 — 글쓴이의 개인적 소회·공감 {_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)}, 수치·데이터 없이 감성 서술만. 헌법 제0-C조)</p>
5. <h2>섹터 & 시장 분석</h2> — {_sector_phrase} · 단락-이미지 교대 배치 (차트 최소 1개, 분량에 따라 추가)
   <p>관련 섹터 흐름·업계 동향 2문</p> → [CHART_6] → <p>시장 환경 + 자금 흐름 2문</p>
6. <h2>투자 전략 & 위험 요인</h2> — {_strategy_phrase} · 단락-이미지 교대 배치 (차트 최소 1개, 분량에 따라 추가)
   <p>진입 시점·매매 시그널 2문</p> → [CHART_7] → <p>리스크 관리·손절선 2문</p>
7. <p>감성 마무리 {_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)} — 단순 요약·투자 지시가 아니라 개인적 소회·독자에게 건네는 따뜻한 인사 (헌법 제0-C조 감성 마무리)</p>
8. <p>면책 {_disc_phrase}</p>  ← (헌법 제5조 적용 — 정보 제공·투자 권유 아님·판단 책임은 독자)

[출력 형식 — 아래 구조를 따르되, 차트는 {_L.THEME_TOTAL_CHART_COUNT}~{_L.MAX_CHART_COUNT}개 범위 내 자유 결정]

TITLE: {spec['title_style']}

CONTENT:
<p>감성 오프닝 2문장 — 독자의 구체적 상황·감정에서 시작 (일반론·AI투 시작 금지).</p>
[CHART_1]
제목: {theme} 테마 — 주가 흐름·수급 트렌드
단위: (카탈로그 D1 단위)
데이터: (카탈로그 D1 라벨=값)
출처: (카탈로그 D1 출처)
[/CHART_1]
<p>{theme} 테마 배경 2문장.</p>

<h2>대장주 — {leader}</h2>
<p>{leader} 사업성·주력 제품 2문장.</p>
<table border="1" cellpadding="6" style="border-collapse:collapse;width:100%;font-size:14px">
  <tr><th>지표</th><th>{leader}</th></tr>
  <tr><td>현재가</td><td>(데이터 수치)</td></tr>
  <tr><td>시가총액</td><td>(데이터 수치)</td></tr>
  <tr><td>PER</td><td>(데이터 수치)</td></tr>
  <tr><td>ROE</td><td>(데이터 수치)</td></tr>
  <tr><td>영업이익률</td><td>(데이터 수치)</td></tr>
</table>
<p>핵심 기술·실적 2문장.</p>
[CHART_2]
제목: {leader} 재무지표 비교
단위: (카탈로그 D2 단위)
데이터: (카탈로그 D2 라벨=값)
출처: (카탈로그 D2 출처)
[/CHART_2]
<p>투자 포인트 1문장.</p>
[PRICE_CHART_LEADER]
제목: {leader} 주가 흐름 (카탈로그 주가이력 기간)
단위: 원
데이터: (카탈로그 주가이력 월별 라벨=값 그대로 — 예: 2021.01=80000, 2021.02=78000, ...)
출처: Yahoo Finance
[/PRICE_CHART_LEADER]
← (대장주 섹션이 길면 추가 인포그래픽 삽입 가능)

<h2>부대장주 — {second}</h2>
<p>{second} 사업성·주력 제품 2문장.</p>
<table border="1" cellpadding="6" style="border-collapse:collapse;width:100%;font-size:14px">
  <tr><th>지표</th><th>{second}</th></tr>
  <tr><td>현재가</td><td>(데이터 수치)</td></tr>
  <tr><td>시가총액</td><td>(데이터 수치)</td></tr>
  <tr><td>PER</td><td>(데이터 수치)</td></tr>
  <tr><td>ROE</td><td>(데이터 수치)</td></tr>
  <tr><td>영업이익률</td><td>(데이터 수치)</td></tr>
</table>
<p>핵심 기술·실적 2문장.</p>
[CHART_3]
제목: {second} 재무지표 비교
단위: (카탈로그 D3 단위)
데이터: (카탈로그 D3 라벨=값)
출처: (카탈로그 D3 출처)
[/CHART_3]
<p>투자 포인트 1문장.</p>
[PRICE_CHART_SECOND]
제목: {second} 주가 흐름 (카탈로그 주가이력 기간)
단위: 원
데이터: (카탈로그 주가이력 월별 라벨=값 그대로 — 예: 2021.01=80000, 2021.02=78000, ...)
출처: Yahoo Finance
[/PRICE_CHART_SECOND]
← (부대장주 섹션이 길면 추가 인포그래픽 삽입 가능)

<h2>그 외 주목 종목 5개</h2>
<p>{others_csv} 종목 1·2 톺아보기 2문장.</p>
[CHART_4]
제목: 종목별 시총·주가 비교
단위: (카탈로그 D4 단위)
데이터: (카탈로그 D4 라벨=값)
출처: (카탈로그 D4 출처)
[/CHART_4]
<p>종목 3·4 섹터 흐름·실적 분석 2문장.</p>
[CHART_5]
제목: 종목별 PER·ROE 분포
단위: (카탈로그 D5 단위)
데이터: (카탈로그 D5 라벨=값)
출처: (카탈로그 D5 출처)
[/CHART_5]
<p>종목 5 + 5종목 종합 평가 2문장.</p>
← (종목 섹션이 길면 차트 추가 가능)
<p>(★ 감성 중간 문단 — 글쓴이의 개인적 소회·공감 {_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)}, 수치 없이 감성 서술만. 헌법 제0-C조)</p>

<h2>섹터 & 시장 분석</h2>
<p>관련 섹터 흐름·업계 동향 2문장.</p>
[CHART_6]
제목: 업계 성장률·이익률 추이
단위: (카탈로그 D6 단위)
데이터: (카탈로그 D6 라벨=값)
출처: (카탈로그 D6 출처)
[/CHART_6]
<p>시장 환경 + 자금 흐름 종합 2문장.</p>
← (섹터 섹션이 길면 차트 추가 가능)

<h2>투자 전략 & 위험 요인</h2>
<p>진입 시점·단기·중기 매매 시그널 2문장.</p>
[CHART_7]
제목: 종목별 기회·위험도 비교
단위: (카탈로그 D7 단위)
데이터: (카탈로그 D7 라벨=값)
출처: (카탈로그 D7 출처)
[/CHART_7]
<p>리스크 관리·손절선·위험 요인 2문장.</p>
← (전략 섹션이 길면 차트 추가 가능)

<p>감성 마무리 {_L.build_length_phrase(_L.MID_EMOTION_SENTS_MIN, _L.MID_EMOTION_SENTS_MAX)} — 단순 요약·투자 지시가 아니라 개인적 소회·독자에게 건네는 따뜻한 인사 (헌법 제0-C조 감성 마무리)</p>
<p>(여기에 면책 2문장 — 본문에 맞춤형 표현. 헌법 제5조 적용 — 정보 제공·투자 권유 아님·판단 책임은 독자)</p>

{_DESIGN_FIRST_BLOCK}
먼저 <design>설계</design> 블록을 쓰고, *그 다음* 위 형식대로 TITLE: 부터 작성. <design> 외에는 위 출력 형식만 — 설명·주석·코드블록 금지.
{_theme_catalog}
{_ref_block}"""
    _theme_floor = max(6, _L.THEME_TOTAL_CHART_COUNT)  # 테마: 7~10 범위 하한선
    _theme_max   = _spec_theme.max_images if _spec_theme else 10
    user_msg = user_msg.replace(
        f"{_L.THEME_TOTAL_CHART_COUNT}~{_L.MAX_CHART_COUNT}",
        f"{_theme_floor}~{_theme_max}"
    )
    _fb_block = build_gate_feedback_block(gate_feedback)
    if _fb_block:
        user_msg += _fb_block
        print(f"  🔁 [Theme/Pass-1/{platform}] 직전 차단 사유 {len(gate_feedback or [])}건 주입 — 재작성")
    print(f"  ✍️  [Theme/Pass-1/{platform}] 텍스트 생성 (system 분리): {theme}...")
    # ★ 동적 설계-우선 (ERRORS [376]): 같은 호출에서 설계→작성. <design> 블록 제거 후 반환.
    raw = _draft_invoke(system_msg, user_msg)   # ★ 견고성 가드 (빈응답·design-only 방어, ERRORS [381])
    return strip_html_wrapper(raw)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공개 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_economic_draft(
    platform: str,
    keyword: str,
    sector: str,
    reason: str,
    supreme_block: str,
) -> str:
    """경제 브리핑 텍스트 대본 생성 (Pass-1).

    Args:
        platform: "tistory" | "naver"
        keyword: 오늘의 트렌드 키워드
        sector: 섹터 (예: "IT·테크")
        reason: 급상승 이유
        supreme_block: BLOG_SUPREME_LAW.md 헌법 블록

    Returns:
        "TITLE: ...\\n[EXCERPT: ...\\n]CONTENT: ..." 형식 텍스트. 실패 시 빈 문자열.
    """
    # ★ 대본 1회 호출 + 동적 설계-우선 (ERRORS [373][376]): 단일 호출 _gen_economic_ts_nv 를
    #   *주 경로* 로 — 설계-우선으로 전체 경제글 생성. 3섹션 순차(_parallel)는 실패 시 폴백만
    #   (rate-limit 압박·스폰 3→1, 테마와 동일 구조). 경제 브리핑도 이제 1회 호출.
    raw = _gen_economic_ts_nv(keyword, sector, reason, supreme_block, platform)
    if not raw:
        raw = _gen_economic_ts_nv_parallel(keyword, sector, reason, supreme_block, platform)
    return raw


def generate_theme_draft(
    platform: str,
    theme: str,
    sector: str,
    stocks_data: dict,
    supreme_block: str,
    collection_docs: list | None = None,
    evidence_pack: dict | None = None,
    gate_feedback: list | None = None,
) -> str:
    """테마글 텍스트 대본 생성 (Pass-1).

    Args:
        platform: "tistory" | "naver"
        theme: 테마명 (예: "AI 반도체")
        sector: 섹터
        stocks_data: {"theme", "stocks": [...], "summary": {...}}
        supreme_block: BLOG_SUPREME_LAW.md 헌법 블록
        collection_docs: JARVIS09 수집 자료 리스트 (CollectionResult)
        evidence_pack: JARVIS09 collect_research 근거 팩 (ADR 012 — 있으면 우선 주입)

    Returns:
        "TITLE: ...\\nCONTENT: ..." 형식 텍스트. 실패 시 빈 문자열.
    """
    return _gen_theme(theme, sector, stocks_data, supreme_block, platform,
                      collection_docs=collection_docs or [],
                      evidence_pack=evidence_pack,
                      gate_feedback=gate_feedback)

