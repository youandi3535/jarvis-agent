"""
자동 발행 작업 로그 모니터링 및 요약
- 매일 07:45: 경제 브리핑 로그 확인 (발행 07:00)
- 매일 21:45: 테마주 로그 확인   (발행 21:00)

★ 2026-07-25 실행모델 통일 후속: 테마도 subprocess 가 되어 `logs/theme_*.log` 로 나간다.
  종전엔 테마가 데몬 안에서 돌아 출력이 `scheduler.log` 에 섞였고, 이 모듈이 그 파일을 읽었다.
  통일 후에도 그대로 두면 *발행 로그가 아닌 데몬 로그* 를 요약해 보고하는 단절이 된다.
"""
from pathlib import Path
from datetime import datetime
import re

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"

def get_todays_date():
    """오늘 날짜를 YYYYMMDD 형식으로 반환"""
    return datetime.now().strftime("%Y%m%d")


def _latest_log(stem: str):
    """오늘자 `{stem}_YYYYMMDD*.log` 중 최신 — 경제·테마 공통 (복사본 금지)."""
    logs = sorted(LOG_DIR.glob(f"{stem}_{get_todays_date()}*.log"), reverse=True)
    return logs[0] if logs else None


def read_latest_economic_log():
    """최근 경제 브리핑 로그 (logs/economic_YYYYMMDD_HHMMSS.log)"""
    return _latest_log("economic")


def read_latest_theme_log():
    """최근 테마주 로그 (logs/theme_YYYYMMDD_HHMMSS.log — subprocess 통일 후 신규 경로)."""
    return _latest_log("theme")

def summarize_economic_log(log_file):
    """경제 브리핑 로그 요약"""
    if not log_file:
        return "❌ 오늘 경제 브리핑 로그를 찾을 수 없습니다."

    try:
        content = log_file.read_text(encoding='utf-8')

        # 결과 추출
        naver_success = "네이버" in content and ("✅" in content or "성공" in content)
        tistory_success = "티스토리" in content and ("✅" in content or "성공" in content)

        # 에러 확인
        has_error = "❌" in content or "실패" in content or "오류" in content

        # 이미지 개수
        img_count = len(re.findall(r'\.png|\.jpg|\.jpeg', content))

        time_str = datetime.now().strftime("%H:%M")
        status = "✅" if (naver_success or tistory_success) else "⚠️"

        result = f"""📰 아침 경제 브리핑 ({time_str})
━━━━━━━━━━━━━━━━━━
네이버: {'✅' if naver_success else '❌'}
티스토리: {'✅' if tistory_success else '❌'}
이미지: {img_count}개
상태: {status}{'에러 발생' if has_error else '정상'}"""

        return result
    except Exception as e:
        return f"❌ 로그 읽기 오류: {e}"

def summarize_theme_log(log_file):
    """테마주 로그 요약"""
    if not log_file:
        return "❌ 오늘 테마주 로그를 찾을 수 없습니다."

    try:
        content = log_file.read_text(encoding='utf-8', errors='ignore')

        # 테마명 추출 — ★ 종전 `RADAR 선택:` 은 저장소 어디에서도 출력하지 않는 *죽은 패턴* 이라
        #   테마가 항상 "불명" 으로 보고됐다(2026-07-25 발견). 실제 출력 문구로 교체.
        theme = "불명"
        for _pat in (r'\[THEME-(?:NAVER|TISTORY)\]\s*발행 완료[^\n]*?테마:\s*([^\n]+)',
                     r'테마:\s*([^\n]+)',
                     r'\[([^\]]+)\]\s*완료'):
            m = re.search(_pat, content)
            if m:
                theme = m.group(1).strip()
                break

        # 결과 추출 — 발행 완료/실패 문구 기준 (플랫폼별 독립)
        naver_success   = bool(re.search(r'\[THEME-NAVER\]\s*발행 완료', content))
        tistory_success = bool(re.search(r'\[THEME-TISTORY\]\s*발행 완료', content))

        # 에러 확인
        has_error = "❌" in content or "실패" in content

        # 이미지 개수
        img_count = len(re.findall(r'\.png|\.jpg|\.jpeg', content[-5000:]))  # 마지막 5000글자 검사

        time_str = datetime.now().strftime("%H:%M")
        status = "✅" if (naver_success or tistory_success) else "⚠️"

        result = f"""🎯 테마주 발행 ({time_str})
━━━━━━━━━━━━━━━━━━
테마: {theme}
네이버: {'✅' if naver_success else '❌'}
티스토리: {'✅' if tistory_success else '❌'}
이미지: {img_count}개
상태: {status}{'에러 발생' if has_error else '정상'}"""

        return result
    except Exception as e:
        return f"❌ 로그 읽기 오류: {e}"

def job_check_economic_result():
    """APScheduler 콜백: 경제 브리핑 로그 확인"""
    log_file = read_latest_economic_log()
    summary = summarize_economic_log(log_file)
    print(f"\n{summary}\n")

def job_check_theme_result():
    """APScheduler 콜백: 테마주 로그 확인"""
    log_file = read_latest_theme_log()
    summary = summarize_theme_log(log_file)
    print(f"\n{summary}\n")

if __name__ == "__main__":
    import sys
    if "economic" in sys.argv:
        job_check_economic_result()
    elif "theme" in sys.argv:
        job_check_theme_result()
