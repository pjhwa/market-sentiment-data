> English docs: [CLAUDE.md](./CLAUDE.md)

# market-sentiment-data — Claude Instructions

## 필수 선행 작업

새 세션 시작 시 반드시 다음 두 파일을 먼저 읽어라:
1. `PROJECT_CONTEXT.md` — 수집기 전체 아키텍처, 스키마, 데이터 흐름, 환경변수, 크론 스케줄
2. `README.md` — 4개 수집기 및 데이터 구조 설명

이 두 파일로 전체 코드를 읽지 않아도 프로젝트를 즉시 파악할 수 있다.

---

## 코드 수정 후 필수 규칙

**코드 파일을 수정한 세션이 끝나기 전, 반드시 아래를 수행하라:**

1. `PROJECT_CONTEXT.md` 업데이트
   - 수정된 수집기 로직·스키마 필드·데이터 흐름·환경변수를 반영
   - "AUTO-GENERATED" 날짜를 오늘 날짜로 갱신

2. `README.md` 업데이트
   - 사용자에게 보이는 변경사항 반영 (신규 수집기, 스키마 필드, 크론 스케줄)

3. 두 파일 모두 git commit에 포함시켜라

**예외**: 테스트·주석만 수정한 경우는 생략 가능.

---

## 프로젝트 핵심 진입점

- **수집기 1**: `collect/collect_sentiment.py` — 소셜 심리, 오염 방지선, divergence, composite_score
- **수집기 2**: `collect/collect_brief.py` — AI 일일 브리프 (기술적 데이터 + 소셜 심리 → Grok)
- **수집기 3**: `collect/collect_earnings.py` — 어닝 인텔리전스 (yfinance + Grok)
- **수집기 4**: `collect/collect_macro_insight.py` — 매크로 인사이트 (SniperBoard `/api/macro` + Grok)
- **가격 맥락**: `collect/price_context.py` — 중립적 가격 단서 fetcher (방향 없음). `fetch_close_direction()`은 후처리 전용 — 절대 프롬프트 빌더로 흘리지 말 것.
- **Git 헬퍼**: `collect/git_utils.py` — 공용 `commit_and_push()`
- **스키마**: `schema.json` — JSON Schema draft-07 v2.0 (심리 데이터 계약)

전체 아키텍처·스키마 레퍼런스·크론 스케줄은 `PROJECT_CONTEXT.md` 참조.

---

## 가장 중요한 원칙 — 오염 방지선

> **가격 방향은 절대 Grok에 전달하지 않는다. 크기·거래량 비율·위치 단서만 허용한다.**

- `price_context.py`는 중립 단서만 반환 — 모든 dict에 `_assert_no_direction()` 기계적 적용
- `collect/collect_sentiment.py`의 `build_prompt()`는 모든 Grok 호출 전 방향 단어 assert
- `fetch_close_direction()` 결과는 **divergence 후처리에만** 흐름 — 프롬프트로 절대 유출 금지

이 원칙을 위반하면 심리 데이터가 가격의 메아리가 되어 분석 가치가 사라진다.

---

## 연관 저장소: sniperboard

이 저장소의 데이터는 SniperBoard가 소비합니다: **`https://github.com/pjhwa/sniperboard`**

| 데이터 종류 | 소스 파일 | SniperBoard 서비스 |
|------------|---------|------------------|
| 소셜 심리 | `sentiment/latest.json` / `sentiment/history/` | `backend/services/sentiment_service.py` |
| AI 일일 브리프 | `brief/latest.json` | `backend/services/brief_service.py` |
| 어닝 인텔리전스 | `earnings/latest.json` | `backend/services/earnings_service.py` |
| 매크로 인사이트 | `macro/latest.json` | `backend/services/macro_insight_service.py` |

- SniperBoard는 raw GitHub URL로 fetch; 토큰은 `SENTIMENT_DATA_TOKEN` 환경변수로 주입.
- **스키마 버전**: 2.0 — 모든 AI 텍스트 필드는 `_en`/`_ko` 접미사 쌍 사용.
- SniperBoard 소비측 구현 상세는 `sniperboard/PROJECT_CONTEXT.md` 참조.
