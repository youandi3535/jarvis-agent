"""JARVIS01_MASTER — 마스터 라우터 + 워크플로우 오케스트레이터.

마스터 비전 ("알아서 모든 것을 하는 비서") 의 *중앙 처리부*.
사용자 자유 문장 → 인텐트 분류 → capability 매칭 → 적절 에이전트 디스패치.

설계 원칙:
- 하위 에이전트 (자비스01/02/03...) 의 *코드를 직접 수정하지 않음*. 호출만.
- LangGraph StateGraph 로 워크플로우 표현.
- shared/ 의 5축 인프라 (schemas·tracing·capabilities·tools·bus) 위에 얹음.
"""
