# SniperBoard yfinance 데이터 정확성 강화 + 최소 연계 개선 설계서

**작성일**: 2026-05-25
**상태**: 브레인스토밍 인터랙티브 승인 완료 (사용자 선택: Approach B + 최소 연계 타이인)
**범위**: 주력 — yfinance에서 파생된 모든 대시보드 값(가격, 지표, Stage 2/RS/52주 고점/진입가, Regime, DD, Macro)의 잠재적 부정확성 제거. 부차 — sniperboard ↔ market-sentiment-data 플라이휠에 대한 최소 투명성 기능으로 투자자 신뢰/통찰 강화.
**관련 쿼리 항목**: 1 (yfinance 일치 검증), 2 (오류값 발견/수정), 3 (연계 분석 + 개선 방안)

---

## 1. 현재 상태 및 문제 분석 (심층 분석 + 체계적 디버깅 결과)

### 저장소 개요
- **sniperboard** (FastAPI + yfinance/pandas 핵심 + Next.js 프론트): Livermore·O'Neil·Minervini 기반 실시간 신호 대시보드. Market Overview, Intraday 신호/RSI/EMA, Daily Stage2(0-7점 + 52주 + RS + R:R + Gaussian), Watchlist, Macro(21종목), Regime(0-100 5요소), Distribution Day 등 **모든 숫자 값**은 `backend/services/data_service.py`(yf.download) → `core/{signal_engine.py, regime_engine.py, distribution_day.py}` → `/api/*` → FE hooks/boards에서 생성.
- **market-sentiment-data**: 데이터 계층 (cron 수집기 → GitHub raw JSON). `latest.json`(심리), `brief/latest.json`, `earnings/latest.json` 제공. **양방향 연계**: collect_*.py가 sniper `/api/regime|daily|watchlist`에서 중립적 `price_context`를 가져와 Grok 프롬프트에 주입(편향 방지 설계); sniper의 `services/{brief,earnings,sentiment}_service.py`가 GitHub raw(30~60분 캐시)에서 AI 카드·SentimentBoard·Earnings를 소비.

### 검증 결과 (실제 파이프라인 재현 + 크로스 체크)
- yf 1.3.0 환경(2026 시뮬 데이터)에서 **전체 프로덕션 파이프라인** (data_service.get_multi_daily/get_ohlcv + 엔진들) 실행: 모든 경로 성공, 주요 계산에서 크래시/NaN 전파 없음.
- 핵심 일치: Stage2 로직으로 계산한 NVDA 52주 고점(raw iloc[-252:].max high) == yf.Ticker.info['fiftyTwoWeekHigh'] (236.54 정확 일치).
- Regime 75.1 CONSTRUCTIVE, SPY DD=6 DANGER, VIX≈16.7, Intraday 390행/컬럼 정상, Macro 3mo 64행 정상.
- **현재 시점에는 yf 소스와 일치하지 않는 "잘못된 값" 없음** (비분할 종목 기준, 설계상 일치).

### 체계적 디버깅으로 발견한 근본 원인 (Phases 1-3 완료)
1. **yf 버전 / MultiIndex 취약성** (`data_service.py:24-38,60-84`): yf 1.3+에서 단일 티커도 MultiIndex(`(field, ticker)`) 또는 group_by 시 `(ticker, field)` 반환. 코드의 `get_level_values(0) + rename` + `levels[0]` 검사 는 현재는 우연히 동작하지만, 미래 yf 변경이나 신규 심볼에서 **데이터 손실 또는 잘못된 OHLCV**를 조용히 초래 → 신호·Stage2·Regime·DD 전체 오염 위험.
2. **장기 지표에 대한 분할 조정 부재** (정확성의 핵심 문제): `/daily`, `/watchlist`, `/regime`에서 사용하는 `get_multi_daily(..., "2y")` + `calculate_stage2_analysis`(252일 윈도우 high/low, 63일 RS 수익률, 20일 ema200 기울기, 20일 피벗 고가 진입가, pullback) 및 regime/macro의 **raw unadjusted** close/high 사용. 워치리스트 종목 중 252일 내 분할 발생 시 (NVDA 2024-06-10 10:1이 과거 경계선, 미래 분할은 필연) 명목 가격 불연속 → **52주 %, RS 점수, Stage2 총점(0-7), 진입/손절/목표가, ema 기울기, breadth_narrow가 전부 잘못됨**. 현재 2026 데이터에서는 미발동이지만 잠재 버그( "실제 시장 데이터 일치" 위반). Intraday 및 최근 DD(25일)는 영향 적음.
3. **Earnings 수집기 취약성** (`market-sentiment-data/collect/collect_earnings.py:42-100+`): `yf.Ticker.calendar` + `.earnings_history`에 대한 `hasattr`/`isinstance`/컬럼명 방어 코드 30줄 이상 (yf 버전별로 악명 높은 불안정 API). None/잘못된 EPS 날짜/추정치/서프라이즈 발생 가능 → sniper Overview/Daily의 Earnings 카드가 불완전하거나 오도. GitHub push 전 스키마 검증 없음.
4. **부차적 문제**: auto_adjust 명시 없음 및 버전 핀 없음; GitHub에서 가져오는 AI 카드에 신선도 메타데이터 없음 (투자자가 sentiment/brief/earnings가 10분 전인지 2시간 전인지 알 수 없음); Intraday 기본 기간 짧음(5d); 테스트에 yf.info ground truth 자동 크로스체크 없음.

이것들은 우연한 오류가 아니라 **아키텍처적 한계**: yf를 안정적인 raw 소스로 취급하면서 splits·컬럼 진화·calendar 형태 변화에 대한 어댑테이션 레이어가 없음.

**성공 기준** (구현 계획에 반영):
- Mag7 + SPY/QQQ/RSP 등 모든 대시보드 숫자가 분할 후 또는 yf 업그레이드 후에도 yf "ground truth"(`.info` 52주 고점, adjusted 시리즈, 수동 계산)와 일치.
- 파이프라인이 조용히 잘못된 데이터를 반환하지 않음 (명시적 에러 또는 폴백 + 로깅).
- Earnings 데이터가 워치리스트에 대해 완전/유효 (또는 명확한 저하 표시).
- 최소 타이인: 투자자가 AI 카드의 데이터 나이(신선도)를 시각적으로 확인 가능 (신뢰 → 깊은 통찰).
- 테스트 통과 + 신규 분할 회귀 테스트; 문서 업데이트 (sniper CLAUDE.md 필수 규칙 준수).
- 현재 비분할 데이터에 대한 동작 변화 없음.

---

## 2. 권장 아키텍처 (Approach B)

### 2.1 데이터 액세스 레이어 (신규 파일 + 업데이트)
- **신규 생성** `backend/core/data_adapter.py` (또는 `services/base.py` + YFinanceDataService 확장):
  - `get_ohlcv_intraday(symbol, tf="5m", period="5d")` → 기존 로직 + 명시적 `auto_adjust=False`, MultiIndex 방향(양쪽 모두) 강건 정규화 (orientation 자동 감지, yf 버전 로깅).
  - `get_daily(symbols: List[str], period="2y", adjusted=True)` → `yf.download(..., auto_adjust=adjusted, group_by='ticker')`, 컬럼을 평평한 `open/high/low/close/volume/adj_close?`로 정규화 후 dict[sym, df] 반환. adjusted=True 시 high/low/close/volume이 분할/배당 조정됨 ( %·레벨·RS·52주 계산에 표준); volume 민감 경로(DD는 최근만 사용)를 위해 raw 옵션도 제공.
  - 필요 시 `get_actions_aware_adjusted` 폴백 헬퍼.
  - `get_ticker_info(symbol)` — .info ground truth(52주, 가격) 래퍼 (테스트 전용).
- 기존 모듈 레벨 `get_ohlcv` / `get_multi_daily`는 프록시 유지 또는 deprecate ( `api/endpoints.py` 4개 호출 사이트 업데이트).
- 시작/첫 fetch 시 `yf_version = yf.__version__` 로깅 추가.

**신규 어댑터를 만드는 이유**: 명확한 경계, 격리된 테스트 가능성, 미래 yf 또는 다른 제공자 교체 용이. "작고 집중된 파일" 원칙 준수.

### 2.2 엔진 / 신호 업데이트 (최소)
- `core/signal_engine.py:calculate_stage2_analysis` (및 endpoints/watchlist/daily 호출부):
  - df에 'adj_close' 또는 adjusted 플래그가 있으면 다음에 우선 사용: 52주 high/low (adj high/low 또는 close 일관성), 63일 stock/spy_ret, ema200_slope (adj close), pullback (adj), 진입가 피벗 high (adj high), breadth_narrow (adj close).
  - 없으면 raw로 폴백 (마이그레이션 중 하위 호환).
  - 반환 dict에 `using_adjusted: bool` 디버그 필드 추가.
- `regime_engine.py`, `distribution_day.py`: 최소 변경 — "최근 윈도우에서는 raw로 충분" 문서화; 필요 시 adjusted 플래그 수용 (DD volume 영향은 25일 내 분할 확률 낮음).
- Intraday 신호는 변경 없음 (단기 윈도우, 분할 영향 없음).

### 2.3 Earnings 경로 강화 (양쪽 저장소)
- `market-sentiment-data/collect/collect_earnings.py`:
  - 구조화된 로깅 추가 (심볼별 성공/실패, raw calendar 형태).
  - 폴백 체인 강화: calendar → earnings_dates → .earnings; 미래 0-30일 날짜 및 numeric EPS 검증.
  - 빌드 후 쓰기 전에 `jsonschema.validate` (확장된 스키마).
  - 부분 실패 시에도 가능한 데이터 + "partial" 플래그 기록.
- `sniperboard/backend/services/earnings_service.py` + 스키마: `generated_at` 노출, 응답에 `age_minutes` 계산 추가.
- sentiment/brief 서비스에도 가벼운 동일 패턴 적용 (이미 generated_at 있음).

### 2.4 최소 연계 타이인 (투명성 = 통찰)
- 백엔드: `/sentiment`, `/brief`, `/earnings` 엔드포인트가 항상 `{"available": bool, "data": {...}, "meta": {"fetched_at": iso, "age_minutes": int, "source": "github-raw", "cache_ttl": 1800}}` 형태로 반환.
- 프론트엔드:
  - `app/types.ts`: 응답 인터페이스에 meta 필드 추가.
  - `components/boards/OverviewBoard.tsx` (AI Insight + Earnings 카드) 및 `SentimentBoard.tsx` (또는 공유 StatCard)에 미묘한 배지 `⏱ ${age}m ago` 추가 (30분 미만 gray, 90분 초과 warn 색상). 기존 `useBrief` 등 staleTime 활용.
- 효과: 투자자가 "이 Grok 브리프는 12분 전 sniper의 regime + daily 컨텍스트 + sentiment로 생성됨"을 즉시 파악 → 플라이휠 이해 + 신뢰(또는 의문) → 새로운 무거운 기능 없이도 깊은 통찰 제공.

### 2.5 테스트·문서·프로세스
- **TDD**: 신규 `backend/tests/test_data_adapter.py` (또는 test_signal_engine 확장): NVDA/TSLA 알려진 분할 기간에 대해 yf 모의 또는 실데이터로 52주/RS/진입가 일치 검증; MultiIndex 변형 테스트.
- 전체 pytest + 수동 `curl /api/daily?symbol=NVDA` 등 배포 전후 실행.
- **문서 (sniper CLAUDE.md 필수)**: `PROJECT_CONTEXT.md` (4,6절 데이터 흐름), `README.md` (조정 가격 설명, 데이터 신선도 노트) 업데이트. market의 `CLAUDE_CODE_INSTRUCTIONS_*.md`도 수집 변경 시 가볍게 업데이트.
- Git: 컴포넌트별 소형 커밋 ("feat(data): yf 1.3 MultiIndex + auto_adjust 지원").
- 프로덕션 무중단: 기능 플래그 또는 단계적 적용 (초기 adjusted=False, 테스트 후 플립).

**제외 범위 (YAGNI)**: 전체 제공자 추상화, 히스토리컬 백테스트 UI, 실시간 푸시, 1-2개 이상의 타이인 배지, 옵션 체인/감마.

---

## 3. 변경될 파일 (정확한 경로)

**sniperboard (주력)**:
- backend/core/data_adapter.py (신규, 약 80 LOC)
- backend/services/data_service.py (얇은 래퍼 또는 deprecate, 정규화 로직 강화)
- backend/core/signal_engine.py (adj 존재 시 3-4곳에서 사용)
- backend/api/endpoints.py (daily 경로 4곳 어댑터 전환; 3개 AI 엔드포인트에 meta 추가)
- backend/tests/test_data_adapter.py (신규) + 기존 테스트 업데이트
- frontend/app/types.ts (meta 필드)
- frontend/components/boards/OverviewBoard.tsx + SentimentBoard.tsx (배지, 약 10 LOC)
- PROJECT_CONTEXT.md, README.md (데이터 흐름·정확성 노트 업데이트 — CLAUDE.md 규칙 필수)
- (선택) docs/ 이미지 또는 claude-code-brief.md

**market-sentiment-data**:
- collect/collect_earnings.py (로깅 + 검증 + 폴백)
- (스키마 변경 시) schema.json 소폭
- CLAUDE_CODE_INSTRUCTIONS_*.md (가볍게)

**변경 없음**: regime/distribution (문서화만), intraday 신호, 대부분 FE, docker 등.

---

## 4. 리스크·트레이드오프·롤아웃

- **auto_adjust volume 영향**: DD/Regime는 최근 25일/60일만 volume 사용 (분할 확률 낮음). 완화: 어댑터가 raw_volume 항상 제공; DD는 volume (또는 raw) 계속 사용.
- **히스토리컬 재계산**: 기존 캐시 없음 (실시간 yf). 배포 후 분할 종목의 52주/RS가 약간 달라짐 (개선, 회귀 아님).
- **FE 배지**: 선택 사항 — UI 폴리시 우려 시 백엔드 meta만으로도 API 소비자에게 충분한 가치.
- **프로덕션 yf 1.3?** 현재 docker는 구버전일 가능성 높음; 어댑터는 양쪽 지원.
- **소요**: TDD 포함 4-6 집중 일. 재현 실행으로 높은 확신.
- **롤백**: 용이 (어댑터 플래그 또는 2개 파일 복원).

---

## 5. 미결 질문 (계획 단계에서 확정)
- 진입가 피벗 고가: adj close만 일관 적용 vs volume만 raw? (권장: volume 외 모든 레벨에 일관 adj 시리즈).
- 배지 문구/위치: 구현 시 최종 폴리시.

---

## 6. 다음 단계
본 설계서 사용자 검토 + 승인 + self-review 완료 후: **writing-plans 스킬 호출** → `docs/superpowers/plans/2026-05-25-sniperboard-yf-accuracy-harden-plan.md` 생성 (체크박스 TDD 태스크, 정확한 코드 스니펫, 테스트 명령, 커밋 메시지, 크로스-레포 조율 포함).

이로써 **증명 가능한 yf 정확성** (쿼리 1+2 충족) + **연계에 대한 가시적 신뢰** (쿼리 3, 최소 범위로) 를 동시에 달성합니다.

**Self-Review (커밋 전)**: TBD/플레이스홀더 없음, 용어 일관 (adjusted vs raw), 파일 경로 정확, 리스크 명시, YAGNI 적용. 모든 사전 승인과 일치.

---

*2026-05-25, ~/dev/sniperboard + ~/dev/market-sentiment-data 심층 분석 결과로부터 생성. 모든 사용자 지시(한국어 답변 포함) 준수.*
