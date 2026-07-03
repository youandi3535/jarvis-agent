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
        "title_style": f"궁금증 유발형 ({_L.TITLE_PROMPT_MAX}자 이내)",
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
        r"`?\[CHART_\d+\]`?\s*,?\s*`?\[CHART_\d+\]`?[^.\n]{0,80}",
        r"`?\[CHART_\d+\]`?",
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


def _inject_missing_charts(html: str, target_count: int, start_idx: int = 1) -> str:
    """HTML에 부족한 [CHART_N] 플레이스홀더 자동 삽입.

    ★ 2026-07-02: 설명을 "섹션 N 관련 데이터 시각화" 같은 플레이스홀더로 두면
    ① 차트 제목이 무의미 ② _detect_type 에 키워드 0 → 랜덤 타입 ③ collect_chart_data
    가 주제어 없이 데이터 못 찾아 스킵 — 삼중 문제. 삽입 지점(마지막 문단) 주변
    본문에서 실제 주제어를 뽑아 데이터·제목 모두 근거 있게 만든다.
    """
    existing = len(re.findall(r'\[CHART_\d+:', html))
    if existing >= target_count:
        return html
    missing = target_count - existing

    def _last_para_topic(h: str) -> str:
        # 삽입 위치(마지막 </p>) 앞 문단에서 20자+ 첫 문장 조각을 주제어로
        paras = re.findall(r'<p[^>]*>(.*?)</p>', h, re.S)
        for p in reversed(paras):
            txt = re.sub(r'<[^>]+>', '', p)
            txt = re.sub(r'\s+', ' ', txt).strip()
            if len(txt) >= 20:
                # 첫 문장 또는 앞 30자
                head = re.split(r'[.!?。]', txt)[0].strip()
                return (head or txt)[:30]
        return ""

    for i in range(missing):
        chart_idx = start_idx + existing + i
        topic = _last_para_topic(html)
        description = f"{topic} 핵심 수치 비교" if topic else "핵심 지표 비교"
        html = re.sub(r'(</p>)(?!.*</p>)', rf'\1\n[CHART_{chart_idx}: {description}]', html, count=1)
    return html


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

def build_corpus_block(docs, max_total: int | None = None, per_doc: int = 2500) -> str:
    """★ 수집 자료 *전문* 주입 (사용자 박제 2026-07-03 — "내용이 풍부해야 퀄리티도 높다").

    자비스09 수집 문서 전부를 대본 프롬프트에 전달 — LLM 이 모든 자료를 보고
    주제·서사·통찰을 구성한다. evidence_brief(수치 규율)와 *병행* 주입.
    신뢰 서열(논문>API>뉴스>기사>웹) 정렬 — 상한 초과 시 저신뢰부터 생략(건수 명시).
    """
    if not docs:
        return ""
    import os as _os_c
    if max_total is None:
        max_total = int(_os_c.getenv("DRAFT_CORPUS_MAX_CHARS", "120000") or "120000")

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
        body = str(_a(d, "cleaned_text") or "").strip()[:per_doc]
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
    _cat_max = int(_os_cat.getenv("DATA_CATALOG_MAX", "16") or "16")
    datasets = list(datasets)[:_cat_max]
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
        for r in (d.get("data") or [])[:8]:
            lbl = str(r.get("label", "")).strip()
            val = r.get("value", "")
            if lbl != "" and val != "":
                lines.append(f"    - {lbl}: {val}{u}")
    # ★ 데이터 내장 슬롯 (사용자 박제 2026-07-03): 작성자가 차트 설계까지 완료 —
    #   슬롯 안에 차트를 만들 *모든 수치* 를 직접 박는다. 자비스06 은 렌더만.
    lines.append("")
    lines.append("★★ 차트 슬롯 작성 규칙 — 차트가 들어갈 자리마다 아래 *블록 형식* 으로")
    lines.append("차트 데이터 전체를 직접 박는다 (여기서 차트 설계까지 끝낸다):")
    lines.append("[CHART_1]")
    lines.append("제목: <차트 제목>")
    lines.append("종류: bar")
    lines.append("단위: <단위>")
    lines.append("데이터: 라벨A=값 | 라벨B=값 | 라벨C=값")
    lines.append("출처: <위 카탈로그의 출처 그대로>")
    lines.append("[/CHART_1]")
    lines.append("- 종류는 bar|line|area|pie|kpi 중 1 (시계열=line/area, 비교=bar, 비율=pie, 단일수치=kpi)")
    lines.append("- 데이터 값은 위 카탈로그(D1..)의 값을 *그대로 복사* — 창작·변형·반올림 금지")
    lines.append("- 단위도 그 데이터셋의 단위 *그대로* — 값과 단위는 한 몸 (값만 복사하고 단위를 바꾸면 거짓)")
    lines.append("- 한 슬롯 = 카탈로그 한 데이터셋 기반. 시간 라벨은 과거→최근 순서")
    lines.append("- 같은 데이터셋으로 슬롯 2개 만들지 마라 (중복 금지)")
    lines.append("- 한 슬롯 안에서 *같은 값을 다른 라벨로 반복* 금지 (예: '매출=16.59 | 매출액=16.59' ✗)")
    lines.append("- 슬롯 제목에 데이터 값 숫자를 그대로 쓰지 마라 (차트 본체와 중복 표기 방지)")
    lines.append("★ 본문 수치는 위 카탈로그·근거 팩·수집 자료 전문에 *명시된* 값만 그대로 인용"
                 " (창작·임의 반올림 금지 — 출처 없는 숫자는 거짓이다).")
    return "\n".join(lines)


def _gen_economic_ts_nv(
    keyword: str, sector: str, reason: str,
    supreme_block: str,
    platform: str = "tistory",
    datasets=None,
) -> str:
    """티스토리·네이버 경제 브리핑 Pass-1: 텍스트 + [CHART_N] 플레이스홀더.

    ★ 데이터-우선 (사용자 박제 2026-06-30): datasets(미리 수집한 실데이터)가 오면 그 목록을
      카탈로그로 주입 → 대본이 *실제 있는 차트만* 계획 (없는 데이터 상상 금지).
    """
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    hook = _gen_hook(keyword, platform)
    _catalog = _build_data_catalog(datasets)

    system_msg = f"""당신은 한국 경제 블로그의 전문 작가입니다.

{supreme_block}

[절대 제약 — 출력 시 반드시 준수 (위 헌법 블록 전체 적용)]
- <p> 태그 15개 이상 (한 <p>에 최대 2문장)
- [CHART_N: 설명] 플레이스홀더 {_L.MIN_CHART_COUNT}개 이상 — <svg> 태그 직접 쓰지 말 것
- 연속 <p>↔<p> 사이마다 [CHART_N: 설명] 삽입 (h2 직전·면책 직전 제외)
- 문체: {spec['tone']}
- 위 지시문(괄호 안 설명·헌법 조항 번호·"정확히 N문장" 등) 본문에 그대로 출력 금지 — *완성된 HTML만* 출력"""

    user_msg = f"""[오늘 작성 요청]
플랫폼: {spec['name']} | 독자: {spec['reader']}
날짜: {_TODAY_KR} ({_TODAY_DOW}요일)
키워드: {keyword} | 섹터: {sector} | 급상승 이유: {reason}

{_catalog}

[출력 형식 — 아래 구조 패턴을 따르되, 차트 개수는 글 분량에 맞게 자유 결정]

★ 총 차트: {_L.MIN_CHART_COUNT}~{_L.MAX_CHART_COUNT}개 (소제목 수·단락 수에 따라 자유롭게 배치. 번호는 [CHART_1]부터 순서대로).
★ 문단-이미지 배치 (제4조 허용 패턴): 문단+이미지+문단 / 문단+이미지+문단+이미지+문단 / 문단+문단+이미지+문단 / 문단+이미지+문단+문단. 이미지 연속·문단 3개+ 연속만 금지.

TITLE: {spec['title_style']}

CONTENT:
<p>감성 오프닝1. 감성 오프닝2.</p>        ← 힌트: "{hook}"
<p>배경1. 배경2.</p>
[CHART_1: {keyword} 관련 핵심 지표 차트]
<p>본문1. 본문2.</p>
[CHART_2: 섹터 비교 또는 추이 차트]
<p>전환.</p>
<h2>소제목1</h2>
<p>섹션1 단락.</p>
[CHART_3: 섹션1 관련 시각화]
<p>섹션1 단락.</p>
[CHART_4: 섹션1 추가]   ← 섹션 분량이 길면 차트 더 추가 가능
...
<h2>소제목N</h2>
<p>마지막 섹션 단락.</p>
[CHART_M: 마무리 차트]
<p>마무리.</p>
<p>(여기에 면책 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} — 본문에 *맞춤형 표현*으로 작성)</p>

지금 바로 TITLE: 부터 출력. 위 출력 형식 외의 설명·주석·코드블록 절대 금지.
"""
    _chart_floor = max(8, _L.MAX_CHART_COUNT * 2 // 3)  # 경제: 8~12 범위 하한선
    user_msg = user_msg.replace(
        f"{_L.MIN_CHART_COUNT}~{_L.MAX_CHART_COUNT}",
        f"{_chart_floor}~{_L.MAX_CHART_COUNT}"
    )
    print(f"  ✍️  [Pass-1/{platform}] 텍스트 생성 (system 분리 적용): {keyword}...")
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    return strip_html_wrapper(raw)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  경제 브리핑 — 섹션별 분할 생성 (병렬 최적화 지원)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_section_system_msg(supreme_block: str, platform: str) -> str:
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    return f"""당신은 한국 경제 블로그의 전문 작가입니다.

{supreme_block}

[절대 제약 — 출력 시 반드시 준수 (위 헌법 블록 전체 적용)]
- 한 <p> 태그에 최대 2문장
- [CHART_N: 설명] 플레이스홀더는 *그대로 유지* (내용 채우지 말 것)
- 문체: {spec['tone']}
- *위 지시문(헌법 조항 번호·"정확히 N문장"·"플레이스홀더 포함" 등) 본문에 그대로 출력 금지* — *완성된 HTML 만* 출력
- 출력 형식 외 설명·주석·코드블록 절대 금지"""


def _gen_section_call1(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
) -> str:
    """Call-1: 오프닝 + 섹션1 생성."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    hook = _gen_hook(keyword, platform)
    system_msg = _build_section_system_msg(supreme_block, platform)
    user_msg = f"""[작성 요청] {platform} 경제 글 — 오프닝 + 섹션1만 생성

플랫폼: {spec['name']} | 독자: {spec['reader']}
키워드: {keyword} | 섹터: {sector} | 이유: {reason}

★ CHART 최소 3개 이상 포함. 단락 수·분량에 따라 추가 배치 가능. 번호는 [CHART_1]부터 순서대로.
★ 문단-이미지 배치 (제4조): 이미지 연속 금지, 문단 3개+ 연속 금지. 문단+문단+이미지+문단 / 문단+이미지+문단+문단 패턴 OK.

[출력 형식] — 아래만 생성하고 STOP

TITLE: {spec['title_style']}

CONTENT:
<p>감성 오프닝1. 감성 오프닝2.</p>        ← 힌트: "{hook}"
<p>배경1. 배경2.</p>
[CHART_1: {keyword} 관련 핵심 지표 차트]
<p>본문1. 본문2.</p>
[CHART_2: 섹터 비교 또는 추이 차트]
<p>전환.</p>
<h2>소제목1</h2>
<p>섹션1 단락.</p>
[CHART_3: 섹션1 관련 시각화]
<p>섹션1 단락.</p>
[CHART_4: 섹션1 추가 분석]   ← (섹션 분량이 길면 차트 추가 가능)
"""
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if not raw:  # 일시 LLM 장애 → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    result = strip_html_wrapper(raw)
    chart_count = len(re.findall(r'\[CHART_\d+:', result))
    _call1_min = max(2, _L.MIN_CHART_COUNT // 2)  # 전체 최솟값의 절반 (call-1은 절반 담당)
    if chart_count < _call1_min:
        print(f"  ⚠️ [Pass-1 Call-1] CHART 부족 ({chart_count}/{_call1_min}) — 강제 삽입")
        result = _inject_missing_charts(result, _call1_min, 1)
    return result


def _gen_section_call2(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
) -> str:
    """Call-2: 섹션2만 생성."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    system_msg = _build_section_system_msg(supreme_block, platform)
    user_msg = f"""[작성 요청] {platform} 경제 글 — 섹션2만 생성 (독립적)

플랫폼: {spec['name']} | 키워드: {keyword} | 섹터: {sector}

★ CHART 최소 2개 이상 포함. 단락 수·분량에 따라 추가 배치 가능. 번호는 [CHART_5]부터 시작.
★ 문단-이미지 배치 (제4조): 이미지 연속 금지, 문단 3개+ 연속 금지. 문단+문단+이미지+문단 패턴 OK.

[출력 형식] — 섹션2만 생성

<h2>소제목2</h2>
<p>섹션2 단락.</p>
[CHART_5: 섹션2 관련 시각화]
<p>섹션2 단락.</p>
[CHART_6: 섹션2 추가 분석]   ← (분량이 길면 차트 추가 가능)
"""
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if not raw:  # 일시 LLM 장애 → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    result = strip_html_wrapper(raw)
    chart_count = len(re.findall(r'\[CHART_\d+:', result))
    _call2_min = max(2, _L.MIN_CHART_COUNT // 4)
    if chart_count < _call2_min:
        print(f"  ⚠️ [Pass-1 Call-2] CHART 부족 ({chart_count}/{_call2_min}) — 강제 삽입")
        result = _inject_missing_charts(result, _call2_min, 5)
    return result


def _gen_section_call3(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
) -> str:
    """Call-3: 섹션3 + 마무리 생성."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    system_msg = _build_section_system_msg(supreme_block, platform)
    user_msg = f"""[작성 요청] {platform} 경제 글 — 섹션3 + 마무리 생성

플랫폼: {spec['name']} | 키워드: {keyword} | 섹터: {sector}

★ CHART 최소 2개 이상 포함. 단락 수·분량에 따라 추가 배치 가능. 번호는 [CHART_7]부터 시작.
★ 문단-이미지 배치 (제4조): 이미지 연속 금지, 문단 3개+ 연속 금지. 문단+이미지+문단+문단 패턴 OK.

[출력 형식] — 섹션3 + 마무리 생성

<h2>소제목3</h2>
<p>섹션3 단락.</p>
[CHART_7: 섹션3 관련 시각화]
<p>섹션3 단락.</p>
[CHART_8: 섹션3 마무리 차트]   ← (분량이 길면 차트 추가 가능)
<p>마무리.</p>
<p>(여기에 면책 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} — 본문에 맞춤형 표현으로 작성)</p>
"""
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    if not raw:  # 일시 LLM 장애 → 1회 재시도
        raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
    result = strip_html_wrapper(raw)
    chart_count = len(re.findall(r'\[CHART_\d+:', result))
    _call3_min = max(2, _L.MIN_CHART_COUNT // 4)
    if chart_count < _call3_min:
        print(f"  ⚠️ [Pass-1 Call-3] CHART 부족 ({chart_count}/{_call3_min}) — 강제 삽입")
        result = _inject_missing_charts(result, _call3_min, 7)
    return result


def _gen_economic_ts_nv_parallel(
    keyword: str, sector: str, reason: str,
    supreme_block: str, platform: str = "tistory",
) -> str:
    """티스토리·네이버 Pass-1: 3개 섹션 순차 생성 (rate limit 방지)."""
    print(f"  ⚡ [Pass-1/{platform}] 섹션별 순차 생성 ...")
    with ThreadPoolExecutor(max_workers=1) as executor:
        call1_fut = executor.submit(_gen_section_call1, keyword, sector, reason, supreme_block, platform)
        call2_fut = executor.submit(_gen_section_call2, keyword, sector, reason, supreme_block, platform)
        call3_fut = executor.submit(_gen_section_call3, keyword, sector, reason, supreme_block, platform)
        try:
            call1_content = call1_fut.result(timeout=300)
            call2_content = call2_fut.result(timeout=300)
            call3_content = call3_fut.result(timeout=300)
        except Exception as e:
            print(f"  ❌ [Pass-1/{platform}] 순차 생성 오류: {e}")
            return _gen_economic_ts_nv(keyword, sector, reason, supreme_block, platform)

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


def _plan_narrative(theme: str, sector: str, evidence_block: str,
                    stocks_text: str = "", post_type: str = "theme") -> str:
    """서사 아웃라인 1패스 — 글의 감정 곡선·섹션 메시지·근거 배정 설계.

    반환: 프롬프트 주입용 아웃라인 텍스트 블록 (실패 시 빈 문자열 — fail-open).
    구조 뼈대(섹션 순서)는 본 프롬프트가 정하므로, 여기서는 *각 섹션이 전할
    핵심 메시지·감정 흐름·어떤 근거(F#)를 어디에 쓸지* 만 설계한다.
    """
    key = f"{theme}|{post_type}|{_TODAY.isoformat()}"
    if key in _NARRATIVE_CACHE:
        return _NARRATIVE_CACHE[key]
    if not evidence_block and not stocks_text:
        return ""
    prompt = f"""주제: {theme} | 섹터: {sector or '-'} | 글 유형: {post_type}

{evidence_block or '(근거 팩 없음 — 종목 데이터 기반으로 설계)'}

{('[종목 데이터 요약]' + chr(10) + stocks_text[:800]) if stocks_text else ''}

이 글의 *서사 설계도* 를 만들어라. 독자의 마음을 움직이는 글은 구조가 먼저다.

출력 (이 형식 그대로, 다른 말 금지):
공감포인트: 독자가 "내 얘기다" 싶을 구체적 상황 1문장
긴장: 글 중반까지 끌고 갈 궁금증·문제의식 1문장
해소: 글이 제시할 답·통찰 1문장
섹션메시지:
- 도입: (핵심 메시지 + 사용할 근거 F# 나열)
- 본론1: (핵심 메시지 + 근거 F#)
- 본론2: (핵심 메시지 + 근거 F#)
- 본론3: (핵심 메시지 + 근거 F#)
- 마무리: (독자가 얻어갈 것 1가지)
차별화한줄: 다른 블로그와 이 글이 다른 점 1문장"""
    try:
        raw = invoke_text("writer_fast", prompt, timeout=90,
                          system="당신은 콘텐츠 서사 설계자다. 설계만 하고 본문은 쓰지 않는다.",
                          max_tokens=900, temperature=0.5)
        outline = strip_html_wrapper(raw or "").strip()
        if outline and "섹션메시지" in outline:
            block = f"\n[★ 서사 설계도 — 이 설계의 흐름·근거 배정대로 전개하라]\n{outline}\n"
            _NARRATIVE_CACHE[key] = block
            if len(_NARRATIVE_CACHE) > 16:
                _NARRATIVE_CACHE.pop(next(iter(_NARRATIVE_CACHE)))
            return block
    except Exception as e:
        _g_report("writer", e, module=__name__, func_name="_plan_narrative")
    return ""


def _structure_signature(content: str) -> tuple:
    """비평 패스 안전 가드용 구조 시그니처 — 플레이스홀더·표·소제목 보존 검증."""
    charts = sorted(re.findall(r"\[CHART_\d+:", content))
    photos = sorted(re.findall(r"\[PHOTO_\d+:", content))
    tables = len(re.findall(r"<table", content, re.IGNORECASE))
    h2s = len(re.findall(r"<h2", content, re.IGNORECASE))
    return (tuple(charts), tuple(photos), tables, h2s)


def critique_and_refine(content: str, platform: str, evidence_block: str = "",
                        post_type: str = "theme") -> str:
    """자기비평 1패스 — 초안을 루브릭으로 점검하고 *문장만* 다듬은 전체본 반환.

    안전 가드: 플레이스홀더·표·소제목 구조가 1개라도 달라지거나 분량이 크게
    변하면 원본 유지 (구조 훼손 < 문장 미세 개선). 킬스위치 WRITER_CRITIQUE=0.
    """
    if not content or _os.getenv("WRITER_CRITIQUE", "1") == "0":
        return content
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    sig_before = _structure_signature(content)
    prompt = f"""아래는 {spec['name']} 블로그 초안이다. 루브릭으로 점검하고 *문장 수준만* 고쳐라.

[루브릭]
1) 도입부가 독자 상황에서 시작하는가 (일반론·AI투 시작 금지)
2) 근거·수치가 문장에 자연스럽게 녹았는가 (나열식 금지)
3) 같은 어미·같은 문장 구조 3회 이상 반복 없는가
4) 각 소제목 아래 첫 문장이 그 섹션의 핵심을 즉시 말하는가
5) 마무리가 요약 반복이 아니라 독자 행동·통찰로 끝나는가
{('6) 본문 수치가 아래 근거 팩과 일치하는가 (불일치 시 근거 팩 값으로 교체)' + chr(10) + evidence_block) if evidence_block else ''}

[절대 규칙]
- [CHART_N: ...] / [PHOTO_N: ...] 플레이스홀더는 글자 하나도 바꾸지 말고 그 자리 유지
- <table>...</table>, <h2>...</h2> 태그·구조·개수 유지 (내용 텍스트 오탈자만 수정 가능)
- <p> 당 문장 수·전체 분량 유지 (문장 추가·삭제 최소화 — 다듬기만)
- 문체 유지: {spec['tone']}
- 수정한 *전체 본문 HTML만* 출력. 설명·주석·코드블록 금지.

[초안]
{content}"""
    try:
        raw = invoke_text("writer", prompt, timeout=300,
                          system="당신은 냉정한 블로그 편집장이다. 구조는 건드리지 않고 문장만 다듬는다.")
        refined = strip_html_wrapper(raw or "").strip()
        if not refined:
            return content
        if _structure_signature(refined) != sig_before:
            print("  ⚠️ [비평] 구조 변형 감지 — 원본 유지")
            return content
        ratio = len(refined) / max(1, len(content))
        if not (0.7 <= ratio <= 1.3):
            print(f"  ⚠️ [비평] 분량 변동 과다({ratio:.2f}) — 원본 유지")
            return content
        print("  ✨ [비평] 자기비평 패스 적용 완료")
        return refined
    except Exception as e:
        _g_report("writer", e, module=__name__, func_name="critique_and_refine")
        return content


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
    """테마글 Pass-1: 텍스트 + [CHART_N]/[PHOTO_N] 플레이스홀더."""
    spec = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["tistory"])
    stocks_text = _stocks_text(stocks_data)
    hook = _gen_hook_theme(theme, platform)
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

    system_msg = f"""당신은 한국 테마주 분석 블로그의 전문 작가입니다.

{supreme_block}

[★ 분량 상한 — 절대 초과 금지 (위반 시 응답 자체 거부)]
- 정확히 {_target_sents}문장 (약 {_target_sents * 50}자)
- **★ 절대 상한: {_max_sents}문장 / {_max_kor}자** — 응답 자체 한계.
- {_max_kor}자 가까워지면 즉시 면책 마무리 후 출력 종료.
- 길게 풀어쓰지 말 것. 핵심만 *간결한 문장* 으로.

[절대 제약 — 출력 시 반드시 준수 (위 헌법 블록 전체 적용)]
- [CHART_N: 설명] = SVG 차트 플레이스홀더, [PHOTO_N: 설명] = AI 사진 플레이스홀더
- <svg>·<img> 태그 직접 쓰지 말 것 — 반드시 위 플레이스홀더만 사용
- 문체: {spec['tone']}
- 종목 데이터의 수치는 *그대로 인용* (가공·임의 변경 금지). 없으면 "N/A" 표기.
- ★ 출처 없는 역사적 수치 창작 절대 금지 — 특정 연도·분기·기간의 가격·비용·규모·비율·지수 등은
  아래 수집 자료나 종목 데이터에 *명시된 값만* 인용. 근거 없는 임의 수치는 사실성 게이트에서
  차단된다. 없으면 "수치를 확인할 수 없었습니다" 등 정성 서술로 대체.
- ★ 수치 없이도 설득력 있게 서술 — 맥락·경향·비교 표현은 검증 불가 숫자보다 낫다.
  과거 특정 시점 임의 통계("2023년 1분기 ○○원" 류) 생성 금지.
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

    # ★ 서사 아웃라인 1패스 (ADR 012) — 구조 설계 후 작성 (실패 시 빈 블록)
    _narrative_block = _plan_narrative(theme, sector, _evidence_block,
                                       stocks_text=stocks_text, post_type="theme")

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

1. 도입부 {_intro_phrase} — <p>2문</p> [CHART_1] <p>2문</p>
2. <h2>대장주 — {leader}</h2> — {_leader_phrase} · 단락-이미지 교대 배치 (표 + 차트 최소 1개, 분량에 따라 추가)
   <p>사업성·주력 2문</p> → <table> → <p>핵심기술·실적 2문</p> → [CHART_2] → <p>투자 포인트</p>
3. <h2>부대장주 — {second}</h2> — {_others_phrase} · 단락-이미지 교대 배치 (표 + 차트 최소 1개, 분량에 따라 추가)
   <p>사업성·주력 2문</p> → <table> → <p>핵심기술·실적 2문</p> → [CHART_3] → <p>투자 포인트</p>
4. <h2>그 외 주목 종목 5개</h2> — {_multi_phrase} · 단락-이미지 교대 배치 (차트 최소 2개, 분량에 따라 추가)
   <p>종목 1·2 톺아보기 2문</p> → [CHART_4] → <p>종목 3·4 분석 2문</p> → [CHART_5] → <p>종목 5 + 종합 평가</p>
5. <h2>섹터 & 시장 분석</h2> — {_sector_phrase} · 단락-이미지 교대 배치 (차트 최소 1개, 분량에 따라 추가)
   <p>관련 섹터 흐름·업계 동향 2문</p> → [CHART_6] → <p>시장 환경 + 자금 흐름 2문</p>
6. <h2>투자 전략 & 위험 요인</h2> — {_strategy_phrase} · 단락-이미지 교대 배치 (차트 최소 1개, 분량에 따라 추가)
   <p>진입 시점·매매 시그널 2문</p> → [CHART_7] → <p>리스크 관리·손절선 2문</p>
7. <p>면책 {_disc_phrase}</p>  ← (헌법 제5조 적용 — 정보 제공·투자 권유 아님·판단 책임은 독자)

[출력 형식 — 아래 구조를 따르되, 차트는 {_L.THEME_TOTAL_CHART_COUNT}~{_L.MAX_CHART_COUNT}개 범위 내 자유 결정]

TITLE: {spec['title_style']}

CONTENT:
<p>감성 오프닝 2문장.</p>        ← 힌트: "{hook}"
[CHART_1: {theme} 테마 개념 — 산업 지도·수급 트렌드·주가 흐름]
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
[CHART_2: {leader} 재무지표 비교 (시총·PER·ROE·영업이익률)]
<p>투자 포인트 1문장.</p>
← (대장주 섹션이 길면 차트 추가 가능)

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
[CHART_3: {second} 재무지표 비교]
<p>투자 포인트 1문장.</p>
← (부대장주 섹션이 길면 차트 추가 가능)

<h2>그 외 주목 종목 5개</h2>
<p>{others_csv} 종목 1·2 톺아보기 2문장.</p>
[CHART_4: 5종목 시총·주가 비교]
<p>종목 3·4 섹터 흐름·실적 분석 2문장.</p>
[CHART_5: 5종목 PER·ROE 분포]
<p>종목 5 + 5종목 종합 평가 2문장.</p>
← (종목 섹션이 길면 차트 추가 가능)

<h2>섹터 & 시장 분석</h2>
<p>관련 섹터 흐름·업계 동향 2문장.</p>
[CHART_6: 업계 성장률·이익률 추이]
<p>시장 환경 + 자금 흐름 종합 2문장.</p>
← (섹터 섹션이 길면 차트 추가 가능)

<h2>투자 전략 & 위험 요인</h2>
<p>진입 시점·단기·중기 매매 시그널 2문장.</p>
[CHART_7: 종목별 기회·위험도 매트릭스]
<p>리스크 관리·손절선·위험 요인 2문장.</p>
← (전략 섹션이 길면 차트 추가 가능)

<p>(여기에 면책 2문장 — 본문에 맞춤형 표현. 헌법 제5조 적용 — 정보 제공·투자 권유 아님·판단 책임은 독자)</p>

지금 바로 TITLE: 부터 출력. 위 출력 형식 외의 설명·주석·코드블록 절대 금지.
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
    raw = invoke_text("writer", user_msg, timeout=300, system=system_msg)
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
    # tistory / naver: 섹션별 순차 생성 시도 → 폴백
    raw = _gen_economic_ts_nv_parallel(keyword, sector, reason, supreme_block, platform)
    if not raw:
        raw = _gen_economic_ts_nv(keyword, sector, reason, supreme_block, platform)
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


def generate_draft(
    blog_type: str,
    platform: str,
    **kwargs,
) -> str:
    """블로그 텍스트 대본 생성 통합 진입점.

    Args:
        blog_type: "economic" | "theme"
        platform:  "tistory" | "naver"
        **kwargs:  blog_type 에 맞는 파라미터 전달

    Examples:
        generate_draft("economic", "tistory",
            keyword="빌 게이츠", sector="IT·테크", reason="...", supreme_block="...")
        generate_draft("theme", "tistory",
            theme="AI 반도체", sector="반도체", stocks_data={...}, supreme_block="...")
    """
    if blog_type == "economic":
        return generate_economic_draft(
            platform=platform,
            keyword=kwargs["keyword"],
            sector=kwargs["sector"],
            reason=kwargs["reason"],
            supreme_block=kwargs["supreme_block"],
        )
    if blog_type == "theme":
        return generate_theme_draft(
            platform=platform,
            theme=kwargs["theme"],
            sector=kwargs.get("sector", ""),
            stocks_data=kwargs.get("stocks_data", {}),
            supreme_block=kwargs["supreme_block"],
            collection_docs=kwargs.get("collection_docs"),
            evidence_pack=kwargs.get("evidence_pack"),
        )
    raise ValueError(f"알 수 없는 blog_type: {blog_type!r}. 'economic' 또는 'theme' 만 지원.")
