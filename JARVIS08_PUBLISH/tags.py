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

__all__ = ["MAX_TAG_LEN", "MIN_TAG_LEN", "platform_tag_count", "split_candidates",
           "response_is_tag_shaped", "valid_tags", "fallback_tags", "generate_tags"]

# ★ 태그 길이·개수는 `length_manager` 가 소유자다 (분량 도메인 단일 진입점 — 루트 CLAUDE.md).
#   초판은 여기에 20·6·4 를 새로 박아 **3벌째 사본**을 만들었다(재감사 19위 지적).
#   `TAG_MAX`(태그 한 개 한도) · `NAVER_HASHTAG_MIN`(네이버 해시태그 최솟값)에서 파생한다.
def _L():
    from JARVIS02_WRITER import length_manager as _lm
    return _lm


try:
    MAX_TAG_LEN = int(_L().TAG_MAX) * 2      # 단어 한도 → 글자 한도(공백 제거 붙여쓰기 기준)
except Exception:
    MAX_TAG_LEN = 20
MIN_TAG_LEN = 2                              # 1글자는 조사·관형사라 검색어가 되지 않는다


def platform_tag_count(platform: str) -> int:
    """플랫폼별 목표 태그 개수 — `length_manager` 에서 파생.

    네이버는 `NAVER_HASHTAG_MIN`(=5) 을 목표로 한다(최솟값을 채우는 것이 규정 취지).
    소유자 상수가 없는 플랫폼은 그 절반 수준으로 보수 적용하되, **모르는 플랫폼이면
    로그로 알린다** — 조용한 기본값은 새 플랫폼이 감시 밖에 남는 길이다.
    """
    p = (platform or "").lower()
    try:
        naver_n = int(_L().NAVER_HASHTAG_MIN)
    except Exception:
        naver_n = 5
    if p == "naver":
        return naver_n
    if p == "tistory":
        return max(MIN_TAG_LEN, naver_n - 1)
    print(f"  ⚠️ 태그 개수 기준이 없는 플랫폼 '{platform}' — 네이버 기준을 임시 적용")
    return naver_n


def split_candidates(raw: str) -> list[str]:
    """LLM 응답을 태그 후보로 쪼갠다 — **쉼표와 줄바꿈 둘 다 구분자**.

    ★ 초판은 개행이 있으면 응답 전체를 버렸다. 그런데 '한 줄에 하나씩' 은 태그 목록의
      **정상적인 형태** 다 — 실측 `response_is_tag_shaped("태그1\\n태그2\\n태그3\\n태그4",4)`
      → False. 즉 멀쩡한 응답을 통째로 폐기하고 폴백을 상시화하고 있었다.
    """
    import re
    return [p.strip() for p in re.split(r"[,\n]+", raw or "") if p.strip()]


def response_is_tag_shaped(raw: str, count: int) -> bool:
    """응답이 태그 목록의 *꼴* 인가 — 아니면 통째로 버린다.

    ★ 왜 어휘(금칙어)가 아니라 꼴을 보는가: '불가'·'죄송' 같은 목록을 박으면
      목록에 없는 새 거부문은 그대로 통과한다. 거부 어휘는 무한하고 모델을 바꾸면
      또 달라진다 — 목록은 반드시 낡는다.

    판정 3단 (전부 구조):
      ① 조각 중 하나라도 태그 한도의 2배를 넘으면 → 산문이 섞였다. 통째로 폐기.
      ② 태그다운 조각이 2개 미만이면 → 목록이 아니다. (짧은 거부문이 여기서 걸린다 —
         초판은 `'죄송하지만 … 만들 수 없습니다'` 를 **통과**시켰다.)
      ③ 태그다운 비율이 절반 미만이면 → 설명이 주인공인 응답이다.
    """
    pieces = split_candidates(raw)
    if not pieces:
        return False
    if any(len(p) > MAX_TAG_LEN * 2 for p in pieces):
        return False
    good = len(valid_tags(pieces, count=len(pieces)))
    return good >= 2 and good * 2 >= len(pieces)


def _is_tag_like(clean: str) -> bool:
    """정제된 문자열이 검색어로 쓸 만한가 — 길이·숫자단독만 본다(불용어 목록 없음)."""
    return bool(clean) and MIN_TAG_LEN <= len(clean) <= MAX_TAG_LEN and not clean.isdigit()


def valid_tags(candidates: list[str], count: int) -> list[str]:
    """정제 + 태그답지 않은 것 제거 + 중복 제거 + count 컷."""
    from shared.seo import sanitize_tag

    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        clean = sanitize_tag(c)
        if not _is_tag_like(clean) or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= count:
            break
    return out


def fallback_tags(title: str, count: int) -> list[str]:
    """LLM 을 못 쓸 때의 태그 — **제목에서 파생**.

    종전 폴백은 `제목첫단어 + '관련주'/'테마주'/'대장주'` 라 경제글에 '중위소득관련주'
    같은 실재하지 않는 검색어를 만들었다(원칙③ 위반). 제목의 낱말은 어떤 글이든
    그 글의 주제어다 — 분기 없이 4조합 모두에 맞다.

    ★ 단, 제목을 그냥 쪼개면 '내'·'3'·'어디쯤' 이 공개 태그로 나간다(재감사 7위 실측).
      → `_is_tag_like` 로 거르고 **긴 낱말 우선**(제목에서 정보량이 큰 쪽)으로 고른다.
      모자라면 억지로 채우지 않는다 — **빈 자리가 쓰레기 태그보다 낫다**.
    """
    from shared.seo import sanitize_tag

    words = [sanitize_tag(w) for w in (title or "").split()]
    cands = [w for w in words if _is_tag_like(w)]
    cands.sort(key=len, reverse=True)          # 조사·짧은 어절보다 주제어가 앞에
    return list(dict.fromkeys(cands))[:count]


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
            tags = valid_tags(split_candidates(raw), count)
        else:
            print(f"  ⚠️ 태그 응답이 태그 목록 형태가 아님 — 폐기 후 제목 파생 사용 "
                  f"(조각 {len(split_candidates(raw))}개, 최장 "
                  f"{max((len(p) for p in split_candidates(raw)), default=0)}자)")
    except Exception as e:
        print(f"  ⚠️ 태그 생성 실패 — 제목 파생 사용: {e}")

    if len(tags) < count:
        for fb in fallback_tags(title, count):
            if fb not in tags:
                tags.append(fb)
            if len(tags) >= count:
                break
    # ★ 모자라도 억지로 채우지 않는다 — 빈 자리가 쓰레기 태그보다 낫다.
    return tags[:count]
