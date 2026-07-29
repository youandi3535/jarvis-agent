"""발행 태그 생성·검증 단일 진입점 (네이버·티스토리 공통).

★ 왜 만드나 (2026-07-29 전수 감사 2위 — 사용자 승인)
  2026-07-29 07:00 경제 브리핑에서 **LLM 의 거부 산문이 그대로 공개 태그로 발행**됐다:

      ✅ 태그 완료: ['주식', '투자관련키워드제공불가\\n\\n**설명:** 이 게시글은
        "중위소득"이라는 복지·통계 주제를 다루고 있으며', "종목·투자 테마와는 무관합니다…"]
      📎 https://blog.naver.com/youandi3535/224361270274      ← 공개 게시 완료

  두 가지가 동시에 깨져 있었다.

  ① **검증이 0** — `_raw.split(',')` 한 결과를 아무 검사 없이 반환했다.
     `shared/seo.sanitize_tags` 는 실존했으나 두 발행자 모두 부르지 않았다.
  ② **프롬프트가 불가능한 것을 요구** — 네이버 쪽이 *"단독 '주식'·'투자' 금지.
     반드시 테마명과 결합"* 을 **글 종류와 무관하게** 강제했다. '중위소득'(복지·통계)
     글에 억지 투자 태그를 붙이라는 요구라 LLM 이 거부했고, 그 거부문이 태그가 됐다.
     원칙③(모든 글 적용) 위반 — 테마글 기준을 경제글에 그대로 적용한 전형.

★ 설계 (3원칙)
  ① **단일 진입점** — 종전 `_generate_smart_tags` 가 naver_poster·tistory_poster
     **두 벌**로 존재했다(거의 동일 코드). 여기 하나로 합쳤다. 한쪽만 고치면
     다른 쪽에서 재발한다 — 오늘 사고가 정확히 그 형태였다.
  ② **동적 설계** — 프롬프트를 글 종류로 *분기* 하지 않는다. 새 플래그를 발행 함수까지
     배선하면 그 플래그를 안 넘기는 세 번째 호출자가 생기는 순간 같은 병이 재발한다.
     대신 규칙 자체를 **내용에 적응하도록** 썼다("종목·투자 글이면 …, 아니면 글의 주제어").
     폴백도 고정 접미사('관련주'·'테마주')가 아니라 **제목에서 파생** 한다 —
     종전 폴백은 경제글에 '중위소득관련주' 같은 것을 만들었다.
  ③ **모든 글 적용** — 4조합(네이버·티스토리 × 경제·테마)이 이 함수 하나를 지난다.

★ 검증을 '금칙어' 가 아니라 '구조' 로 하는 이유 (★ 비직관)
  '불가'·'죄송' 같은 거부 표현 목록을 박으면 **목록에 없는 새 거부문은 그대로 통과** 한다.
  LLM 의 거부 어휘는 무한하고 모델을 바꾸면 또 달라진다 — 목록은 반드시 낡는다.
  그래서 *무엇을 말했는지* 가 아니라 **응답이 태그 목록의 꼴인지** 를 본다:
  개행이 있거나 총 길이가 태그 목록으로서 불가능하면 **응답 전체를 버린다**.
  설명·사과·마크다운은 어떤 어휘를 쓰든 이 관문을 통과할 수 없다.
"""
from __future__ import annotations

__all__ = ["MAX_TAG_LEN", "platform_tag_count", "response_is_tag_shaped",
           "valid_tags", "fallback_tags", "generate_tags"]

# 한 태그의 최대 길이(정제 후). 이보다 길면 태그가 아니라 문장이다.
MAX_TAG_LEN = 20

# 플랫폼별 태그 개수 — 외부 플랫폼의 관행이라 런타임 파생 불가한 *정책 상수*.
# 소유자를 여기 하나로 두는 것이 요점(종전엔 두 발행자에 흩어져 있었다).
_PLATFORM_TAG_COUNT = {"naver": 6, "tistory": 4}
_DEFAULT_TAG_COUNT = 4


def platform_tag_count(platform: str) -> int:
    """플랫폼별 목표 태그 개수. 모르는 플랫폼은 보수적으로 기본값."""
    return _PLATFORM_TAG_COUNT.get((platform or "").lower(), _DEFAULT_TAG_COUNT)


def response_is_tag_shaped(raw: str, count: int) -> bool:
    """LLM 응답 *전체* 가 태그 목록의 꼴인가 — 아니면 통째로 버린다.

    태그 목록은 '단어, 단어, 단어' 한 줄이다. 설명·사과·마크다운은 반드시
    개행을 동반하거나 길이가 폭증한다. 어휘를 보지 않으므로 낡지 않는다.
    """
    if not raw or not raw.strip():
        return False
    if "\n" in raw.strip():
        return False
    # 태그 count 개 + 구분자가 차지할 수 있는 최대치
    return len(raw.strip()) <= count * (MAX_TAG_LEN + 2)


def valid_tags(candidates: list[str], count: int) -> list[str]:
    """정제 + 태그답지 않은 것 제거 + 중복 제거 + count 컷."""
    from shared.seo import sanitize_tag

    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not c or "\n" in c:
            continue
        clean = sanitize_tag(c)
        if not clean or len(clean) > MAX_TAG_LEN or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= count:
            break
    return out


def fallback_tags(title: str, count: int) -> list[str]:
    """LLM 을 못 쓸 때의 태그 — **제목에서 파생**.

    종전 폴백은 `제목첫단어 + '관련주'/'테마주'/'대장주'` 였다. 테마글에는 맞지만
    경제글에는 '중위소득관련주' 같은 실재하지 않는 검색어를 만들었다(원칙③ 위반).
    제목의 낱말은 어떤 글이든 그 글의 주제어다 — 분기 없이 4조합 모두에 맞다.
    """
    from shared.seo import sanitize_tag

    words = [sanitize_tag(w) for w in (title or "").split()]
    out = [w for w in words if w and len(w) <= MAX_TAG_LEN]
    # 낱말이 모자라면 인접 낱말을 이어 붙여 채운다(그래도 제목 파생).
    i = 0
    while len(out) < count and i + 1 < len(words):
        merged = (words[i] + words[i + 1])[:MAX_TAG_LEN]
        if merged and merged not in out:
            out.append(merged)
        i += 1
    return list(dict.fromkeys(out))[:count]


def generate_tags(title: str, body_text: str, platform: str) -> list[str]:
    """발행 태그 생성 — 4조합 공통 단일 진입점. 항상 태그다운 문자열만 반환."""
    count = platform_tag_count(platform)
    try:
        from JARVIS02_WRITER import length_manager as _LM
    except ImportError:  # 발행자 단독 실행 경로
        import length_manager as _LM  # type: ignore

    snippet = (body_text or "")[:_LM.BODY_SNIPPET_LEN]
    tags: list[str] = []
    try:
        from shared.llm import invoke_text

        raw = invoke_text(
            "writer_short_title",
            f"블로그 검색 최적화 태그 {count}개를 쉼표로 구분해 한 줄로 출력하세요.\n"
            f"규칙:\n"
            f"- 이 글의 실제 주제를 검색할 때 쓸 법한 구체적 키워드\n"
            f"- 종목·투자를 다룬 글이면 테마명과 '관련주'·'주식' 을 결합 (예: 반도체관련주)\n"
            f"  그 외 주제(정책·통계·생활 등)라면 그 주제의 핵심어를 그대로 사용\n"
            f"- 각 태그는 공백 없이 붙여쓰기, {MAX_TAG_LEN}자 이내\n"
            f"- 태그 {count}개만 출력. 설명·사과·머리말 금지\n\n"
            f"제목: {title}\n"
            f"본문: {snippet}",
            timeout=60,
        ) or ""

        if response_is_tag_shaped(raw, count):
            tags = valid_tags(raw.strip().split(","), count)
        else:
            print(f"  ⚠️ 태그 응답이 태그 목록 형태가 아님 — 폐기 후 제목 파생 사용 "
                  f"(길이 {len(raw.strip())}, 개행 {'있음' if chr(10) in raw else '없음'})")
    except Exception as e:
        print(f"  ⚠️ 태그 생성 실패 — 제목 파생 사용: {e}")

    if len(tags) < count:
        for fb in fallback_tags(title, count):
            if fb not in tags:
                tags.append(fb)
            if len(tags) >= count:
                break
    return tags[:count]
