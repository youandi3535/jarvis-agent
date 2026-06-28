"""JARVIS06_IMAGE/dynamic_infographic.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
★ LLM이 matplotlib 코드를 매번 직접 창작 → 무한 스타일 인포그래픽 생성

원칙:
1. 팩트 기반  — 실제 수치(재무·시장)를 data= 로 주입, LLM이 그것을 시각화
2. 무한 스타일 — 26종 힌트 pool + LLM 자유 창작 = 이론상 무한 변형
3. 중복 원천차단 — run_id + slot_key 조합 → 같은 글 내 같은 스타일 재선택 차단
4. 텍스트 선명도 — 생성 코드에서 fontsize < 11 를 자동 패치 (제목 ≥ 20 본문 ≥ 11)
5. 3D 지원 — exec namespace에 Axes3D + mpl_toolkits 포함
"""
from __future__ import annotations
import re, os, math, io, base64, hashlib, logging, time
from pathlib import Path

log = logging.getLogger("jarvis")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

from JARVIS06_IMAGE.theme_charts import wrap_img, _FONT_PATH


# ══════════════════════════════════════════════════════════════════
#  1. 스타일 힌트 풀 (26종 + 랜덤 자유창작)
# ══════════════════════════════════════════════════════════════════
_STYLE_POOL = [
    # 관계형
    "허브-스포크 방사형 (다크 배경, 중심 아이콘 박스 + 8개 위성 원, 방사 연결선)",
    "헥사 노드 마인드맵 (밝은 배경, 6방향 컬러 물방울 노드, 이모지 아이콘)",
    "사이클 순환 다이어그램 (원형 화살표, 4~6 단계, 각 단계 다른 색)",
    "벤다이어그램 (2~3개 교집합 원, 교집합 영역에 핵심 내용 텍스트)",
    "조직도 트리 (상하 계층 연결선, 박스 안 아이콘 + 텍스트)",
    "플로우차트 (사각형·다이아몬드·원 노드, 화살표 연결, 프로세스 흐름)",
    # 비교형
    "피라미드 계층 다이어그램 (넓은→좁은 레이어, 각 레이어 다른 색 + 텍스트)",
    "비교 매트릭스 (행렬 격자, 색상 히트맵 + ✓/✗ 체크)",
    "양면 비교 (Left vs Right, 중앙 세로선, 각면 항목 나열)",
    "평가 스코어카드 (항목별 수평 게이지 바, 색상 등급표시)",
    "버블 매트릭스 (XY 사분면, 버블 크기=시총, 색상=섹터)",
    "레이더(스파이더) 차트 (다각형 축, 채워진 반투명 영역, 항목 레이블)",
    # 시계열·진행형
    "수평 타임라인 (배경 스트립, 컬러 원 마일스톤, 교대 위아래 레이블)",
    "수직 로드맵 (세로 라인, 왼오 교대 항목 카드, 연도 표시)",
    "갠트 차트 스타일 (행=항목, 가로 색상 바, 기간 표시)",
    "계단형 성장 다이어그램 (오른쪽 위로 올라가는 계단, 각 계단 레이블)",
    # 데이터 시각화 (팩트 수치 활용)
    "아이소메트릭 3D 바 차트 (경사진 3D 막대, 다중 색상, 그림자)",
    "3D 표면 플롯 (mpl_toolkits.mplot3d, plot_surface, 그라디언트 컬러맵)",
    "3D 산점도 (scatter3D, 점 크기=수치, 색상=카테고리, 회전 시점)",
    "도넛+바 복합 (왼쪽 도넛차트, 오른쪽 수평 바, 두 차트 연동)",
    "폭포 차트 (Waterfall, 누적 증감, 양수=파랑 음수=빨강)",
    "트리맵 (면적=시가총액, 색상=수익률, 중첩 사각형)",
    # 카드/그리드형
    "아이콘 그리드 카드 (3×2 또는 4×2, 각 카드: 이모지+제목+수치+설명)",
    "KPI 대시보드 (4~6개 KPI 카드, 각 카드: 수치 크게+화살표+트렌드라인)",
    "대시보드 목업 (레이어드 카드 3장 겹치기, 각 카드 미니 파이·바·라인)",
    "인포그래픽 타일 (2×3 격자, 각 타일 배경색 다름, 핵심 수치 크게)",
]

_N_STYLES = len(_STYLE_POOL)


def _pick_style(run_id: str, slot_key: str) -> str:
    """run_id + slot_key 조합으로 style 선택 → 같은 글 내 중복 방지."""
    seed = int(hashlib.md5(f"{run_id}|{slot_key}".encode()).hexdigest()[:8], 16)
    return _STYLE_POOL[seed % _N_STYLES]


# ══════════════════════════════════════════════════════════════════
#  2. 안전 실행 환경
# ══════════════════════════════════════════════════════════════════
_FORBIDDEN_PATTERNS = [
    r'\bos\s*\.\s*(system|popen|exec|remove|unlink|rename|mkdir|listdir)\b',
    r'\bsubprocess\b', r'__import__\s*\(',
    r'\bopen\s*\((?!.*mode\s*=\s*[\'"]r[\'"])',  # write-mode open 차단
    r'\beval\s*\(', r'\bexec\s*\(',
    r'import\s+os\b', r'import\s+sys\b', r'import\s+shutil\b',
    r'import\s+socket\b', r'import\s+urllib\b', r'import\s+requests\b',
]

_SAFE_BUILTINS = {
    "print": print, "range": range, "len": len, "int": int, "float": float,
    "str": str, "list": list, "dict": dict, "tuple": tuple, "set": set,
    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
    "sorted": sorted, "reversed": reversed, "isinstance": isinstance,
    "hasattr": hasattr, "getattr": getattr, "repr": repr,
    "True": True, "False": False, "None": None,
    "any": any, "all": all, "type": type,
}


def _is_safe(code: str) -> bool:
    for pat in _FORBIDDEN_PATTERNS:
        if re.search(pat, code):
            log.debug(f"[dynamic_infographic] 위험 패턴: {pat}")
            return False
    return True


def _patch_font_sizes(code: str) -> str:
    """fontsize < 11 → 11로 올림, 제목(title/suptitle) < 20 → 20으로 올림."""
    def _boost_size(m: re.Match) -> str:
        try:
            v = float(m.group(1))
            return m.group(0).replace(m.group(1), str(max(int(v), 11)))
        except Exception:
            return m.group(0)

    def _boost_title_size(m: re.Match) -> str:
        try:
            v = float(m.group(1))
            return m.group(0).replace(m.group(1), str(max(int(v), 20)))
        except Exception:
            return m.group(0)

    code = re.sub(r'\bfontsize\s*=\s*(\d+(?:\.\d+)?)\b', _boost_size, code)
    code = re.sub(r'\bset_title\s*\([^,)]*,\s*fontsize\s*=\s*(\d+(?:\.\d+)?)',
                  _boost_title_size, code)
    # fontweight='normal' → 'bold' for title/suptitle
    code = re.sub(
        r'((?:set_title|suptitle|ax\.set_title)\s*\([^)]*fontweight\s*=\s*)[\'"]normal[\'"]',
        r"\1'bold'",
        code
    )
    return code


def _build_exec_namespace() -> dict:
    """안전 exec namespace — matplotlib 전체 + 3D 포함."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.patheffects as pe
    import matplotlib.font_manager as fm
    from matplotlib.patches import FancyBboxPatch, Circle, FancyArrowPatch, Arc, Wedge
    from matplotlib.path import Path as MPath
    import matplotlib.colors as mcolors
    import matplotlib.cm as cm
    import numpy as np
    import math as _math
    import colorsys
    import hashlib as _hlib
    import random
    try:
        from mpl_toolkits.mplot3d import Axes3D
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        _has3d = True
    except Exception:
        Axes3D = None; Poly3DCollection = None; _has3d = False

    ns = {
        # matplotlib
        "plt": plt,
        "mpatches": mpatches,
        "pe": pe,
        "fm": fm,
        "FancyBboxPatch": FancyBboxPatch,
        "Circle": Circle,
        "FancyArrowPatch": FancyArrowPatch,
        "Arc": Arc,
        "Wedge": Wedge,
        "MPath": MPath,
        "mcolors": mcolors,
        "cm": cm,
        # numpy / math
        "np": np,
        "math": _math,
        "colorsys": colorsys,
        "hashlib": _hlib,
        "random": random,
        # 3D
        "Axes3D": Axes3D,
        "Poly3DCollection": Poly3DCollection,
        # builtins
        "__builtins__": _SAFE_BUILTINS,
    }
    return ns


# ══════════════════════════════════════════════════════════════════
#  3. 데이터 포매터 (팩트 수치 → 프롬프트 문자열)
# ══════════════════════════════════════════════════════════════════

def _format_data_for_prompt(data: dict | None) -> str:
    """COLLECTED_DATA 재무 수치를 LLM이 읽기 좋은 텍스트로 변환.

    지원 필드 (두 형식 모두 허용):
      - 시총: cap (원 단위) 또는 cap_억 (억 단위)
      - 매출: revenue (원 단위) 또는 revenue_억 (억 단위)
      - 순이익: net_income (원 단위) 또는 net_income_억 (억 단위)
      - ROE, op_margin: 이미 % 형태 (8.3 → 8.3%) — ×100 하지 않음
    """
    if not data:
        return "(실제 데이터 없음 — 일반적인 수치 사용)"
    lines = []
    # 종목 재무 데이터
    stocks = data.get("stocks") or []
    if stocks:
        lines.append("━━ 종목별 팩트 데이터 (이 수치를 차트에 그대로 사용할 것) ━━")
        for s in stocks[:8]:
            name = s.get("name", "?")
            # 시총: cap_억 우선 (억 단위), 없으면 cap (원 단위) 환산
            cap_uk = s.get("cap_억")   # 억 단위
            cap_raw = s.get("cap", 0)  # 원 단위
            if cap_uk is not None:
                cap_tr = f"{cap_uk/10000:.1f}조" if cap_uk >= 10000 else f"{cap_uk:,.0f}억"
            elif cap_raw:
                cap_uk2 = cap_raw / 1e8
                cap_tr = f"{cap_uk2/10000:.1f}조" if cap_uk2 >= 10000 else f"{cap_uk2:,.0f}억"
            else:
                cap_tr = "N/A"
            per  = s.get("per") or 0
            roe  = s.get("roe") or 0   # 이미 % 형태
            om   = s.get("op_margin") or 0  # 이미 % 형태
            profit = "흑자" if s.get("is_profit") else "적자"
            # 매출 (revenue_억 우선)
            rv_uk = s.get("revenue_억")
            rv_raw = s.get("revenue") or 0
            if rv_uk is not None:
                rv_tr = f"{rv_uk:,.0f}억"
            elif rv_raw:
                rv_tr = f"{rv_raw/1e8:,.0f}억"
            else:
                rv_tr = "N/A"
            lines.append(
                f"  {name}: 시총={cap_tr}, 매출={rv_tr}, PER={per:.1f}배, "
                f"ROE={roe:.1f}%, 영업이익률={om:.1f}%, {profit}"
            )
    # 요약 통계
    summary = data.get("summary") or {}
    if summary:
        lines.append("━━ 요약 ━━")
        for k, v in summary.items():
            lines.append(f"  {k}: {v}")
    # 테마 트렌드 데이터
    trends = data.get("trends") or []
    if trends:
        lines.append("━━ 시장 트렌드 (시계열) ━━")
        for t in trends[:6]:
            lines.append(f"  {t}")

    return "\n".join(lines) if lines else "(실제 데이터 없음 — 합리적 추정치 사용)"


# ══════════════════════════════════════════════════════════════════
#  4. 프롬프트 빌더
# ══════════════════════════════════════════════════════════════════

_FONT_HELPER = """
# ── 폰트·색상·텍스트 헬퍼 (반드시 사용) ──
_fp = fm.FontProperties(fname=_FONT_PATH) if _FONT_PATH else None
def T(ax, x, y, s, size=12, weight='normal', color='#222', ha='center', va='center', **kw):
    \"\"\"한국어 텍스트 출력 헬퍼 (fontsize 최소 11 보장)\"\"\"
    kws = dict(fontsize=max(size, 11), fontweight=weight, color=color, ha=ha, va=va)
    if _fp: kws['fontproperties'] = _fp
    kws.update(kw)
    return ax.text(x, y, str(s), **kws)
def HUE(h, s=0.70, v=0.80):
    \"\"\"HSV→hex 색상 헬퍼\"\"\"
    r,g,b = colorsys.hsv_to_rgb(h%1.0, min(s,1.0), min(v,1.0))
    return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'
# 기본 팔레트 (seed 기반 동적)
_BASE_HUE = (int(hashlib.md5(_FONT_PATH.encode() if _FONT_PATH else b'x').hexdigest()[:4],16)/0xFFFF + _RUN_SEED/1000) % 1.0
PAL = [HUE(_BASE_HUE + i/7, 0.68, 0.78) for i in range(7)]
"""


def _build_prompt(
    theme: str,
    purpose: str,
    content: str,
    data_str: str,
    style: str,
    run_id: str,
) -> str:
    run_seed = int(hashlib.md5(run_id.encode()).hexdigest()[:4], 16) % 1000

    return f"""당신은 세계 최고 수준의 인포그래픽 디자이너이자 파이썬 데이터 시각화 전문가입니다.
아래 팩트 데이터와 내용을 바탕으로 고품질 matplotlib 인포그래픽 코드를 작성하세요.

═══ 시각화 정보 ═══
테마: {theme}
목적: {purpose}
관련 내용: {content[:700]}

═══ 팩트 데이터 (반드시 이 수치를 차트에 사용할 것 — 숫자 지어내기 절대 금지) ═══
{data_str}

═══ 스타일 지시 ═══
이번 차트 스타일: {style}

★ 품질 기준 (모두 반드시 준수):
1. 실제 팩트 수치를 차트에 그대로 사용 (데이터 없는 항목만 합리적 추정)
2. 제목 fontsize ≥ 20, 본문 fontsize ≥ 11 (T() 헬퍼 사용)
3. FancyBboxPatch·Circle·Wedge 등 패치 클래스로 시각적 풍부함 필수
4. 이모지 아이콘 (🚀📊💡🔬⚡🌐🏆💰📈🔍🛡️🔄🤖💎📌🎯⭐🔥) ax.text() 삽입
5. 반투명 레이어 alpha=0.1~0.3 배경 효과 적용
6. 색상: PAL 팔레트 또는 HUE() 헬퍼로 동적 색상 (hex 하드코딩 금지)
7. 한국어 텍스트 필수 — T() 헬퍼 사용
8. figsize=(13, 9) 이상
9. "전문 디자이너가 만든 인포그래픽" 수준의 최종 결과물
10. 3D 지시면 Axes3D 사용 (plot_surface / scatter3D / bar3d 중 택 1)

★ 금지:
- plt.bar() / plt.pie() / plt.plot() 만으로 끝나는 단순 차트 금지
- 숫자 지어내기 금지 (팩트 데이터 외 수치 사용 시 "(추정)" 표기)
- os·sys·subprocess·open() 사용 금지

★ 코드 구조 (반드시 이 순서):
```python
# 1. 폰트·색상 헬퍼 삽입 (_FONT_PATH, _OUTPUT_PATH, _RUN_SEED 는 이미 주입됨)
{_FONT_HELPER.strip()}

# 2. 실제 차트 코드 (지시된 스타일로 창작)
fig, ax = plt.subplots(figsize=(13, 9), facecolor='#ffffff')
# ... 창작 코드 ...

# 3. 제목 (한국어, fontsize=22, fontweight='black')
ax.set_title(f'{theme} — {purpose[:20]}', fontsize=22, fontweight='black', pad=20)

# 4. 저장 (마지막 두 줄 고정)
plt.savefig(_OUTPUT_PATH, dpi=160, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close(fig)
```

Python 코드만 출력 (```python 블록):
"""


# ══════════════════════════════════════════════════════════════════
#  5. 실행 + 중복 감지
# ══════════════════════════════════════════════════════════════════

import threading as _threading
_exec_lock = _threading.Lock()   # matplotlib thread-safety


def _exec_chart_code(code: str, out_path: str, run_id: str) -> bool:
    """코드 실행. 성공(파일 5KB+) → True."""
    run_seed = int(hashlib.md5(run_id.encode()).hexdigest()[:4], 16) % 1000
    injected = (
        f"_FONT_PATH = {repr(_FONT_PATH)}\n"
        f"_OUTPUT_PATH = {repr(out_path)}\n"
        f"_RUN_SEED = {run_seed}\n"
    ) + code

    # savefig 경로 강제 교체
    injected = re.sub(
        r"(?:plt|fig)\.savefig\s*\([^)]+\)",
        f"plt.savefig({repr(out_path)}, dpi=160, bbox_inches='tight')",
        injected, count=1
    )
    if "savefig" not in injected:
        injected += (f"\nplt.savefig({repr(out_path)}, dpi=160, "
                     f"bbox_inches='tight', facecolor='white')\n")
    if "plt.close" not in injected:
        injected += "\nplt.close('all')\n"

    ns = _build_exec_namespace()
    try:
        with _exec_lock:
            exec(injected, ns)
        return (
            os.path.exists(out_path)
            and os.path.getsize(out_path) > 8_000  # 8KB 이상 = 실질적 이미지
        )
    except Exception as e:
        log.debug(f"[dynamic_infographic] exec 오류: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
#  6. 공개 API
# ══════════════════════════════════════════════════════════════════

def generate_dynamic_infographic(
    theme: str,
    purpose: str,
    content: str = "",
    data: dict | None = None,
    run_id: str = "",
    slot_key: str = "",
    max_retries: int = 3,
) -> str:
    """
    팩트 데이터 기반 무한 스타일 인포그래픽 생성.

    1순위: HTML+CSS (html_infographic) — 원형 게이지·픽토그램·그리드 패널
    2순위: matplotlib (LLM 동적 코드) — 폴백

    Args:
        theme:       테마명 (예: '반도체')
        purpose:     이 차트가 보여줄 것 (예: '종목별 PER 비교')
        content:     관련 블로그 섹션 텍스트
        data:        팩트 수치 dict — {"stocks":[...], "summary":{...}, "trends":[...]}
        run_id:      글 단위 uuid4 hex
        slot_key:    슬롯 ID (img01 등) — run 내 스타일 중복 방지
        max_retries: 실패 시 재시도 횟수

    Returns:
        HTML <img> 문자열. 실패 시 "" 반환.
    """
    from shared.llm import invoke_text

    _rid   = run_id   or hashlib.md5(f"{theme}|{purpose}|{time.time_ns()}".encode()).hexdigest()[:16]
    _slot  = slot_key or purpose[:12]

    # ── 1순위: HTML+CSS 인포그래픽 ──────────────────────────────────
    try:
        from JARVIS06_IMAGE.html_infographic import generate_html_infographic
        html_result = generate_html_infographic(
            theme=theme,
            purpose=purpose,
            data=data,
            run_id=_rid,
            slot_key=_slot,
            max_retries=1,  # 여기서는 1회, 실패 시 matplotlib 폴백
        )
        if html_result:
            log.info(f"[dynamic_infographic] ✅ HTML인포그래픽 성공: {theme}/{_slot}")
            return html_result
    except Exception as _he:
        log.debug(f"[dynamic_infographic] HTML 인포그래픽 실패 → matplotlib 폴백: {_he}")

    # ── 2순위: matplotlib LLM 동적 코드 (폴백) ─────────────────────
    _style = _pick_style(_rid, _slot)
    _data_str = _format_data_for_prompt(data)
    _out   = f"/tmp/jarvis_infog_{_rid[:12]}_{_slot[:6]}.png"

    for attempt in range(max_retries):
        try:
            # 재시도마다 slot_key suffix 변경 → 다른 스타일 선택
            _attempt_slot = _slot + (f"_r{attempt}" if attempt else "")
            _style_a = _pick_style(_rid + f"_{attempt}", _attempt_slot)

            prompt = _build_prompt(
                theme, purpose, content, _data_str, _style_a, _rid + f"_{attempt}"
            )

            raw = invoke_text("writer", prompt, timeout=130)
            if not raw:
                continue

            # 코드 블록 추출
            m = re.search(r'```python\s*\n([\s\S]*?)```', raw)
            if not m:
                m = re.search(r'```\s*\n([\s\S]*?)```', raw)
            code = m.group(1) if m else (raw if 'import matplotlib' in raw or 'plt.' in raw else "")
            if not code:
                continue

            # 안전성 검사
            if not _is_safe(code):
                continue

            # 텍스트 크기 강제 패치
            code = _patch_font_sizes(code)

            # 실행
            if os.path.exists(_out):
                os.remove(_out)

            if not _exec_chart_code(code, _out, _rid):
                log.debug(f"[dynamic_infographic] exec 실패 (시도 {attempt+1})")
                continue

            # 결과 읽기
            with open(_out, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            try:
                os.remove(_out)
            except Exception:
                pass

            log.info(f"[dynamic_infographic] ✅ {theme}/{_slot} [{_style_a[:30]}] (시도 {attempt+1})")
            return wrap_img(b64, f'{theme} — {purpose}', '')

        except Exception as e:
            log.debug(f"[dynamic_infographic] 시도 {attempt+1} 예외: {e}")
            _g_report("writer", e, module=__name__, func_name="generate_dynamic_infographic")

    log.warning(f"[dynamic_infographic] {max_retries}회 실패: {theme}/{_slot}")
    return ""


__all__ = ["generate_dynamic_infographic"]
