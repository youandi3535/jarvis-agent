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

def _build_data_catalog(datasets) -> str:
    """수집된 실데이터 → 대본 프롬프트용 카탈로그.

    ★ 1-d (2026-07-02): 제목·단위뿐 아니라 *실제 값(라벨:값)·기준일* 까지 주입한다.
      이전엔 제목만 줘서 본문 프로즈의 구체 수치를 LLM 이 지어냈다 — 이제 본문이
      '있는 실데이터 수치만 그대로' 인용하도록 값을 명시한다.
    """
    if not datasets:
        return ""
    lines = ["[★ 사용 가능한 실데이터 — 차트도 본문 수치도 *이 값만* 인용할 것]"]
    for i, d in enumerate(datasets, 1):
        u = d.get("unit", "")
        src = d.get("source") or {}
        as_of = src.get("as_of", "")
        head = f"D{i}. {d.get('title', '')}{(' (단위 ' + u + ')') if u else ''}"
        if as_of:
            head += f" [기준 {as_of}]"
        lines.append(head)
        for r in (d.get("data") or [])[:8]:
            lbl = str(r.get("label", "")).strip()
            val = r.get("value", "")
            if lbl != "" and val != "":
                lines.append(f"    - {lbl}: {val}{u}")
    lines.append("★ 위 목록에 *없는* 수치는 본문·차트에 절대 쓰지 마라 — 실데이터 없는 수치는 거짓이다.")
    lines.append("★ 본문에서 수치를 언급할 땐 위 값을 *그대로* 인용하라 (임의 반올림·창작 금지).")
    lines.append("★ [CHART_N: <위 목록의 제목 그대로>] 형태로, 글 흐름에 맞는 위치에 배치하라.")
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
#  테마글 텍스트 대본 — 전 플랫폼 (Pass-1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _gen_theme(
    theme: str, sector: str, stocks_data: dict,
    supreme_block: str, platform: str = "tistory",
    collection_docs: list | None = None,
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
- 위 지시문(헌법 조항·"N문장"·"플레이스홀더 포함" 등) 본문에 그대로 출력 금지
- *완성된 HTML 만* 출력. 설명·주석·코드블록 금지.

[문단-이미지 배치 규정 (헌법 제4조 허용 패턴 4가지)]
- 패턴1: 문단 → 이미지 → 문단
- 패턴2: 문단 → 이미지 → 문단 → 이미지 → 문단
- 패턴3: 문단 → 문단 → 이미지 → 문단  (문단 2개 연속 후 이미지 OK)
- 패턴4: 문단 → 이미지 → 문단 → 문단  (이미지 후 문단 2개 연속 OK)
- 금지: 이미지·표 두 개 연속 (예: [CHART_X][CHART_Y]), 문단 3개+ 연속
- 표(<table>)도 시각 요소로 카운트 — 표 뒤에 즉시 차트 금지, 반드시 <p> 1개 삽입 후 차트."""

    # JARVIS09 수집 자료 → 참고 컨텍스트 블록 (상위 5건, 각 300자 이내)
    _ref_block = ""
    if collection_docs:
        _lines = []
        for _i, _doc in enumerate(collection_docs[:5], 1):
            _src  = getattr(_doc, "source_type", "")
            _titl = getattr(_doc, "title", "") or ""
            _body = (getattr(_doc, "cleaned_text", "") or "")[:300]
            _lines.append(f"[참고{_i}] ({_src}) {_titl}\n{_body}")
        _ref_block = "\n\n[참고 자료 — JARVIS09 수집 (사실 확인·최신 동향 반영에 활용)]\n" + "\n---\n".join(_lines)

    user_msg = f"""[오늘 작성 요청 — 테마주 분석 글]
플랫폼: {spec['name']} | 독자: {spec['reader']}
날짜: {_TODAY_KR} ({_TODAY_DOW}요일)
테마: {theme} | 섹터: {sector or '-'}
대장주(시총 1위): {leader} · 부대장주(시총 2위): {second}
나머지 5종목: {others_csv}

[종목 데이터]
{stocks_text}

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
) -> str:
    """테마글 텍스트 대본 생성 (Pass-1).

    Args:
        platform: "tistory" | "naver"
        theme: 테마명 (예: "AI 반도체")
        sector: 섹터
        stocks_data: {"theme", "stocks": [...], "summary": {...}}
        supreme_block: BLOG_SUPREME_LAW.md 헌법 블록
        collection_docs: JARVIS09 수집 자료 리스트 (CollectionResult)

    Returns:
        "TITLE: ...\\nCONTENT: ..." 형식 텍스트. 실패 시 빈 문자열.
    """
    return _gen_theme(theme, sector, stocks_data, supreme_block, platform,
                      collection_docs=collection_docs or [])


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
        )
    raise ValueError(f"알 수 없는 blog_type: {blog_type!r}. 'economic' 또는 'theme' 만 지원.")
