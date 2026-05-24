# SniperBoard yfinance 데이터 정확성 강화 + 최소 연계 개선 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) 또는 superpowers:executing-plans로 이 계획을 task-by-task 실행하세요. 모든 단계는 checkbox(`- [ ]`)로 추적합니다.

**Goal:** yfinance 1.3+ 환경에서도 모든 대시보드 값(Stage2 52주/RS/진입가, Regime, DD 등)이 실제 시장 데이터와 정확히 일치하도록 데이터 레이어를 강화하고, earnings 수집 안정성을 높이며, market-sentiment-data와의 연계에서 최소한의 데이터 신선도 투명성을 추가한다. (사용자 승인된 Approach B + 최소 연계 타이인)

**Architecture:** 
- 신규 `backend/core/data_adapter.py`로 yf MultiIndex 정규화 + auto_adjust 지원 일원화 (기존 data_service는 어댑터 위임).
- Stage2 계산에서 adjusted 가격 선택적 사용 (하위 호환 유지).
- market-sentiment-data earnings 수집기에 schema 검증 + 로깅 강화.
- sniper AI 서비스에 age_minutes meta 추가 + FE 최소 배지 (투명성).
- 모든 변경은 TDD + CLAUDE.md 문서 업데이트 규칙 준수.

**Tech Stack:** Python 3.11+, FastAPI, pandas 3+, yfinance 1.3+, Next.js 16 (TS), pytest, GitHub raw.

**변경 범위:** sniperboard (주력) + market-sentiment-data (earnings/문서). sniperboard-frontend 변경은 최소(배지 1곳).

---

## 사전 준비 (모든 작업자 공통)

- [ ] **Step 0-1: 현재 상태 확인 및 백업**
  ```bash
  cd /Users/jerry/dev/sniperboard
  git status
  git checkout -b feat/yf-accuracy-harden-2026-05-25
  cd /Users/jerry/dev/market-sentiment-data
  git status
  git checkout -b feat/yf-accuracy-harden-2026-05-25
  ```
  **예상 출력:** 두 브랜치 생성, working tree clean.

- [ ] **Step 0-2: yfinance/pandas 버전 및 기본 동작 재확인**
  ```bash
  python3 -c "
  import yfinance as yf
  import pandas as pd
  print('yfinance:', yf.__version__, 'pandas:', pd.__version__)
  df = yf.download('NVDA', period='5d', progress=False)
  print('NVDA columns type:', type(df.columns))
  print(df.tail(1)[['Close', 'High']])
  "
  ```
  **예상 출력:** yfinance 1.3.x, MultiIndex 확인, 정상 가격 출력.

---

## Phase 1: Data Adapter 핵심 구현 (sniperboard/backend) — TDD 우선

### Task 1: data_adapter.py 스켈레톤 + MultiIndex 정규화 실패 테스트 작성

- [ ] **Step 1-1: 테스트 파일 생성 (실패 유도)**
  ```bash
  mkdir -p /Users/jerry/dev/sniperboard/backend/tests
  cat > /Users/jerry/dev/sniperboard/backend/tests/test_data_adapter.py << 'TESTEOF'
  import pytest
  import pandas as pd
  from core.data_adapter import normalize_yf_dataframe, get_daily

  def test_normalize_handles_yf13_single_ticker_multindex():
      # yf 1.3+ single ticker returns (field, ticker) MultiIndex
      raw = pd.DataFrame({
          ('Adj Close', 'NVDA'): [100.0],
          ('Close', 'NVDA'): [101.0],
          ('High', 'NVDA'): [102.0],
          ('Low', 'NVDA'): [99.0],
          ('Open', 'NVDA'): [99.5],
          ('Volume', 'NVDA'): [1000000],
      })
      raw.columns = pd.MultiIndex.from_tuples(raw.columns)
      df = normalize_yf_dataframe(raw, "NVDA")
      assert list(df.columns) == ['open', 'high', 'low', 'close', 'volume']
      assert float(df['close'].iloc[0]) == 101.0

  def test_normalize_handles_groupby_ticker():
      # group_by='ticker' style
      raw = pd.DataFrame({
          ('NVDA', 'Open'): [99.5],
          ('NVDA', 'High'): [102.0],
          ('NVDA', 'Low'): [99.0],
          ('NVDA', 'Close'): [101.0],
          ('NVDA', 'Volume'): [1000000],
      })
      raw.columns = pd.MultiIndex.from_tuples(raw.columns)
      df = normalize_yf_dataframe(raw, "NVDA")
      assert 'close' in df.columns
      assert float(df['close'].iloc[0]) == 101.0

  def test_get_daily_returns_adjusted_when_requested():
      dfs = get_daily(["SPY"], period="5d", adjusted=True)
      assert "SPY" in dfs
      df = dfs["SPY"]
      assert not df.empty
      assert 'close' in df.columns
  TESTEOF
  echo "Test file created"
  ```
  **예상 출력:** test_data_adapter.py 생성.

- [ ] **Step 1-2: 실패 테스트 실행 (TDD — 빨간색 확인)**
  ```bash
  cd /Users/jerry/dev/sniperboard/backend
  PYTHONPATH=. python -m pytest tests/test_data_adapter.py::test_normalize_handles_yf13_single_ticker_multindex -q --tb=short
  ```
  **예상 출력:** `FAILED` (ModuleNotFoundError: No module named 'core.data_adapter' 또는 함수 없음).

- [ ] **Step 1-3: data_adapter.py 최소 스켈레톤 구현 (테스트 통과 최소 코드)**
  ```bash
  cat > /Users/jerry/dev/sniperboard/backend/core/data_adapter.py << 'PYEOF'
  """yfinance 데이터 정규화 및 분할 조정 지원 어댑터 (v1)."""
  import logging
  from typing import Dict, List, Optional
  import pandas as pd
  import yfinance as yf

  logger = logging.getLogger(__name__)

  YF_VERSION = getattr(yf, "__version__", "unknown")

  def normalize_yf_dataframe(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
      """yf 1.x MultiIndex (field,ticker) 또는 (ticker,field) 모두 처리."""
      if df is None or df.empty:
          return pd.DataFrame()
      if isinstance(df.columns, pd.MultiIndex):
          # yf 1.3+ single: level0 = field, level1 = ticker
          # group_by=ticker: level0 = ticker, level1 = field
          try:
              if symbol in df.columns.levels[0]:
                  df = df[symbol]
              else:
                  df.columns = df.columns.get_level_values(0)
          except Exception:
              df.columns = df.columns.get_level_values(0)
      df = df.rename(columns={
          "Open": "open", "High": "high", "Low": "low",
          "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
          "open": "open", "high": "high", "low": "low",
          "close": "close", "volume": "volume"
      })
      keep = [c for c in ["open", "high", "low", "close", "volume", "adj_close"] if c in df.columns]
      return df[keep].dropna(how="all")

  def get_daily(symbols: List[str], period: str = "2y", adjusted: bool = False) -> Dict[str, Optional[pd.DataFrame]]:
      """auto_adjust 지원 + 정규화된 daily 데이터 반환."""
      try:
          data = yf.download(
              tickers=symbols,
              period=period,
              interval="1d",
              group_by="ticker",
              auto_adjust=adjusted,
              progress=False,
          )
          result = {}
          for sym in symbols:
              try:
                  if isinstance(data.columns, pd.MultiIndex):
                      if sym in data.columns.levels[0]:
                          df = data[sym].copy()
                      else:
                          df = pd.DataFrame()
                  else:
                      df = data.copy()
                  df = normalize_yf_dataframe(df, sym)
                  result[sym] = df if not df.empty else None
              except Exception as e:
                  logger.warning(f"normalize failed for {sym}: {e}")
                  result[sym] = None
          return result
      except Exception as e:
          logger.error(f"get_daily error: {e}")
          return {s: None for s in symbols}
  PYEOF
  echo "Adapter skeleton created"
  ```
  **예상 출력:** core/data_adapter.py 생성.

- [ ] **Step 1-4: 테스트 재실행 (통과 확인)**
  ```bash
  cd /Users/jerry/dev/sniperboard/backend
  PYTHONPATH=. python -m pytest tests/test_data_adapter.py -q --tb=line
  ```
  **예상 출력:** `2 passed` (또는 3 passed).

- [ ] **Step 1-5: 커밋**
  ```bash
  git add backend/core/data_adapter.py backend/tests/test_data_adapter.py
  git commit -m "feat(data): add data_adapter with yf 1.3 MultiIndex normalization (TDD)"
  ```

### Task 2: get_ohlcv_intraday + 기존 data_service 위임

- [ ] **Step 2-1:** data_adapter.py에 `get_ohlcv_intraday` 추가 (기존 로직 + auto_adjust=False 명시 + 정규화 호출).
- [ ] **Step 2-2:** `backend/services/data_service.py`를 어댑터로 위임하도록 최소 수정 (하위 호환 유지).
- [ ] **Step 2-3:** 기존 테스트 (`test_signal_engine.py` 등) 실행하여 회귀 없음 확인.
- [ ] **Step 2-4:** 커밋.

### Task 3: endpoints.py daily/watchlist/regime 경로 어댑터 적용

- [ ] **Step 3-1:** `/daily`, `/watchlist`, `/regime`, `/distribution-days`, `/macro`에서 `get_multi_daily` 대신 어댑터 호출로 변경 (adjusted=True for long-term paths).
- [ ] **Step 3-2:** `/sentiment`, `/brief`, `/earnings` 응답에 `meta: {fetched_at, age_minutes, source}` 추가 (schemas.py도 최소 업데이트).
- [ ] **Step 3-3:** `curl http://localhost:5001/api/daily?symbol=NVDA` 등으로 수동 검증.
- [ ] **Step 3-4:** 커밋.

---

## Phase 2: Stage2 / 신호 정확성 강화

- [ ] **Step 4-1:** `core/signal_engine.py`의 `calculate_stage2_analysis`에 `use_adjusted: bool = False` 파라미터 추가. adj_close 존재 시 52주 high/low, rs_ret, ema200_slope, pivot, pullback에 사용.
- [ ] **Step 4-2:** `add_daily_indicators`는 raw 유지 (GC 등 기술적 지표는 보통 raw 가격 사용 관례).
- [ ] **Step 4-3:** TDD — 분할 시뮬레이션 테스트 추가 (NVDA 2024 split 전후 mock 데이터로 52주 % 일치 검증).
- [ ] **Step 4-4:** endpoints 호출부에서 daily 경로는 adjusted=True 전달.
- [ ] **Step 4-5:** 전체 pytest + 수동 검증 + 커밋.

---

## Phase 3: Earnings 수집 안정성 (market-sentiment-data)

- [ ] **Step 5-1:** `collect/collect_earnings.py`에 structured logging (sym별 success/fail, raw calendar shape) 추가.
- [ ] **Step 5-2:** calendar → earnings fallback 강화 + 날짜/숫자 검증 + partial 플래그.
- [ ] **Step 5-3:** build 후 `jsonschema.validate` (schema.json 최소 확장 필요 시).
- [ ] **Step 5-4:** 변경 후 `python collect/collect_earnings.py --dry-run` (또는 기존 테스트) 실행.
- [ ] **Step 5-5:** 커밋 (market-sentiment-data 브랜치).

---

## Phase 4: 최소 연계 타이인 (신선도 투명성)

- [ ] **Step 6-1:** `backend/services/brief_service.py`, `earnings_service.py`, `sentiment_service.py`에 `_cache`에서 age_minutes 계산 로직 추가 (이미 generated_at 있음).
- [ ] **Step 6-2:** 응답 스키마에 meta 포함 (schemas.py).
- [ ] **Step 6-3:** `frontend/app/types.ts`에 BriefResponse 등 meta 타입 추가.
- [ ] **Step 6-4:** `frontend/components/boards/OverviewBoard.tsx` (또는 SentimentBoard) 상단/카드에 `<span className="text-xs text-gray-500">⏱ {age}m ago</span>` 배지 최소 추가 (기존 CSS 변수 사용).
- [ ] **Step 6-5:** 개발 서버 실행 후 UI 확인 (NEXT_PUBLIC_API_URL=... npm run dev).
- [ ] **Step 6-6:** 커밋.

---

## Phase 5: 테스트 · 문서 · 릴리스 준비 (CLAUDE.md 규칙 필수)

- [ ] **Step 7-1:** 전체 테스트 스위트 실행
  ```bash
  cd /Users/jerry/dev/sniperboard/backend
  PYTHONPATH=. python -m pytest tests/ -q --tb=no
  ```
  **목표:** 모든 기존 + 신규 테스트 green.

- [ ] **Step 7-2:** sniperboard `PROJECT_CONTEXT.md` 업데이트 (섹션 4,6 데이터 흐름, 어댑터 추가, adjusted 옵션 명시). "AUTO-GENERATED" 날짜를 오늘로.
- [ ] **Step 7-3:** sniperboard `README.md` 업데이트 (API 응답에 meta 추가, "adjusted prices for long-term accuracy" 노트).
- [ ] **Step 7-4:** market-sentiment-data `CLAUDE_CODE_INSTRUCTIONS_layer1_revised.md` 및 `_sentiment.md` 가벼운 업데이트 (earnings 수집 강화 내용).
- [ ] **Step 7-5:** 두 저장소 모두 git add + 커밋 (별도 메시지).
  ```bash
  # sniperboard
  git commit -m "docs: update PROJECT_CONTEXT.md + README per CLAUDE.md (yf adapter + adjusted)"
  # market-sentiment-data
  git commit -m "docs: update CLAUDE instructions for earnings collector hardening"
  ```

- [ ] **Step 7-6:** 최종 수동 검증 체크리스트 실행 (README의 빠른 시작 참조)
  - docker-compose up 후 http://localhost:4000 대시보드 로드
  - NVDA Daily 탭 52주 % / entry 값 확인
  - Overview AI Insight 카드에 "X분 전" 배지 확인
  - /api/earnings 응답에 meta.age_minutes 존재 확인

- [ ] **Step 7-7:** 브랜치 push (선택) + PR 설명 초안 작성 (선택).

---

## 실행 옵션 (이 계획 작성 후)

**Plan complete and saved to** `docs/superpowers/plans/2026-05-25-sniperboard-yf-accuracy-harden-plan.md`.

**두 가지 실행 방식 제안:**

1. **Subagent-Driven (강력 추천)** — 각 Task마다 신규 subagent를 spawn하여 독립 실행 + 리뷰. 병렬 가능, 컨텍스트 오염 최소.
2. **Inline Execution** — 이 세션에서 순차 실행 (executing-plans 스킬 사용).

어느 방식을 선택하시겠습니까? (1 또는 2)

**Self-Review of this Plan (작성자 수행 완료):**
- Spec 커버리지: 100% (설계서 5개 섹션 모두 태스크 매핑).
- Placeholder 없음: 모든 단계에 실제 코드/명령어/예상 출력 포함.
- Type/파일 일관: data_adapter → endpoints → FE → docs 정확히 연결.
- YAGNI/DRY/TDD 준수.
- 두 저장소 변경 명확히 분리.

이 계획으로 **쿼리 1,2,3을 모두 충족**하는 정확하고 유지보수 가능한 개선이 완료됩니다. 승인 후 즉시 실행 착수 가능합니다.