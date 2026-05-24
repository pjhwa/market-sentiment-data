# AI Brief & Earnings Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** market-sentiment-data 파이프라인을 확장해 AI Daily Brief와 Earnings Intelligence를 생성하고, Sniperboard가 이를 소비해 OverviewBoard·DailyBoard·SentimentBoard에 표시한다.

**Architecture:** Mac Mini cron이 collect_brief.py / collect_earnings.py를 실행 → Sniperboard API 호출 + yfinance 수집 → Grok(Hermes) 호출 → GitHub JSON push. Sniperboard 백엔드가 GitHub raw URL을 프록시, 프론트엔드 훅이 소비해 UI에 표시.

**Tech Stack:** Python 3.11, yfinance, requests, Hermes CLI (Grok), FastAPI + Pydantic v2, Next.js 16 + TanStack Query v5, Tailwind v4

**Spec:** `docs/superpowers/specs/2026-05-24-ai-brief-earnings-design.md`

---

## 파일 변경 목록

### market-sentiment-data
| 파일 | 변경 |
|------|------|
| `brief/latest.json` | NEW — 초기 빈 placeholder |
| `earnings/latest.json` | NEW — 초기 빈 placeholder |
| `collect/collect_brief.py` | NEW |
| `collect/collect_earnings.py` | NEW |
| `collect/test_collect_brief.py` | NEW |
| `collect/test_collect_earnings.py` | NEW |
| `README.md` | MODIFIED — 새 섹션 추가 |

### sniperboard/backend
| 파일 | 변경 |
|------|------|
| `backend/api/schemas.py` | MODIFIED — Brief/Earnings Pydantic 모델 추가 |
| `backend/services/brief_service.py` | NEW |
| `backend/services/earnings_service.py` | NEW |
| `backend/api/endpoints.py` | MODIFIED — /brief, /earnings 엔드포인트 추가 |
| `backend/tests/test_brief_service.py` | NEW |
| `backend/tests/test_earnings_service.py` | NEW |
| `docker-compose.yml` | MODIFIED — BRIEF_DATA_URL, EARNINGS_DATA_URL 환경변수 추가 |

### sniperboard/frontend
| 파일 | 변경 |
|------|------|
| `frontend/app/types.ts` | MODIFIED — Brief/Earnings TypeScript 타입 추가 |
| `frontend/hooks/useBrief.ts` | NEW |
| `frontend/hooks/useEarnings.ts` | NEW |
| `frontend/components/boards/OverviewBoard.tsx` | MODIFIED — AI Brief 카드 + Earnings Calendar 카드 |
| `frontend/components/boards/DailyBoard.tsx` | MODIFIED — 어닝 배너 |
| `frontend/components/boards/SentimentBoard.tsx` | MODIFIED — setup_quality 배지 |

---

## Task 1: market-sentiment-data 디렉토리 구조 생성

**Files:**
- Create: `brief/latest.json`
- Create: `earnings/latest.json`

- [ ] **Step 1: 빈 placeholder 파일 생성**

```bash
mkdir -p /Users/jerry/dev/market-sentiment-data/brief
mkdir -p /Users/jerry/dev/market-sentiment-data/earnings
```

`brief/latest.json` 내용:
```json
{
  "generated_at": null,
  "schema_version": "1.0",
  "slot": null,
  "market_brief": null,
  "symbol_briefs": []
}
```

`earnings/latest.json` 내용:
```json
{
  "generated_at": null,
  "schema_version": "1.0",
  "upcoming_earnings": [],
  "recent_results": []
}
```

- [ ] **Step 2: README.md에 새 섹션 추가**

`README.md`의 `## 리포 구조` 섹션에 추가:
```
├── brief/
│   ├── latest.json             # AI Daily Brief 최신 스냅샷
│   └── history/               # YYYY-MM-DD_<slot>.json
└── earnings/
    ├── latest.json             # 어닝 인텔리전스 최신
    └── history/               # YYYY-MM-DD.json
```

- [ ] **Step 3: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add brief/latest.json earnings/latest.json README.md
git commit -m "feat: brief/ earnings/ 디렉토리 구조 초기화"
git push
```

---

## Task 2: collect/collect_brief.py 구현

**Files:**
- Create: `collect/collect_brief.py`

이 스크립트는 collect_sentiment.py와 동일한 패턴을 따른다. Sniperboard API에서 기술적 지표를 수집하고, latest.json에서 소셜 심리를 읽어 Grok에게 Brief 생성을 요청한다.

- [ ] **Step 1: collect_brief.py 파일 생성**

```python
#!/usr/bin/env python3
"""
AI Daily Brief 수집기
① Sniperboard API에서 Regime, DD, 종목별 Stage2/신호 수집
② latest.json에서 소셜 심리 읽기
③ Grok(Hermes)으로 brief JSON 생성
④ brief/latest.json + brief/history/<date>_<slot>.json 저장 → git push
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))
SNIPERBOARD_API = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")

WATCHLIST = [
    ("TSLA", "Tesla"),
    ("AAPL", "Apple"),
    ("NVDA", "Nvidia"),
    ("META", "Meta Platforms"),
    ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet / Google"),
]


# ── 슬롯 감지 ──────────────────────────────────────────────────────────────────

def detect_slot(now: datetime) -> str:
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    if 9 <= now.hour < 18:
        return "pre_open"
    return "post_close"


# ── Sniperboard API 호출 ───────────────────────────────────────────────────────

def _api_get(path: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.get(f"{SNIPERBOARD_API}/api{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[WARN] API {path} 호출 실패: {e}", file=sys.stderr)
        return None


def fetch_technical_context() -> dict:
    """Sniperboard API에서 시장 전체 + 종목별 기술적 데이터 수집."""
    regime = _api_get("/regime") or {}
    dd = _api_get("/distribution-days") or {}
    watchlist = _api_get("/watchlist") or {}

    symbol_data = {}
    for sym, _ in WATCHLIST:
        daily = _api_get("/daily", {"symbol": sym})
        if daily and daily.get("stage2"):
            s2 = daily["stage2"]
            symbol_data[sym] = {
                "stage2_score": s2.get("score", 0),
                "rs_score": round(s2.get("rs_score", 50.0), 1),
                "pct_from_52w_high": round(s2.get("pct_from_52w_high", 0.0), 1),
                "market_structure": s2.get("market_structure", "NEUTRAL"),
                "entry": round(s2.get("entry", 0.0), 2),
                "gc_above": s2.get("gc_above", False),
                "gc_breakout": s2.get("gc_breakout", False),
                "bear_flag": s2.get("bear_flag", False),
                "rsi_divergence_bullish": s2.get("rsi_divergence_bullish", False),
                "rsi_divergence_bearish": s2.get("rsi_divergence_bearish", False),
            }

    return {
        "regime": regime,
        "distribution_days": dd,
        "watchlist": watchlist.get("watchlist", []),
        "symbol_detail": symbol_data,
    }


def load_sentiment() -> dict:
    """latest.json에서 소셜 심리 로드."""
    latest_path = REPO_PATH / "latest.json"
    if not latest_path.exists():
        return {}
    try:
        with open(latest_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] latest.json 읽기 실패: {e}", file=sys.stderr)
        return {}


# ── 프롬프트 빌더 ──────────────────────────────────────────────────────────────

def build_brief_prompt(tech: dict, sentiment: dict, slot: str) -> str:
    regime = tech.get("regime", {})
    dd = tech.get("distribution_days", {})
    spy_dd = dd.get("spy", {})
    qqq_dd = dd.get("qqq", {})
    sym_detail = tech.get("symbol_detail", {})

    # 심리 데이터 색인
    sentiment_by_sym: dict = {}
    for sym_obj in sentiment.get("symbols", []):
        sym_sentiment = sym_obj.get("symbol")
        if sym_sentiment:
            sentiment_by_sym[sym_sentiment] = sym_obj

    # 종목별 요약 구성
    symbol_summaries = []
    for sym, company in WATCHLIST:
        s2 = sym_detail.get(sym, {})
        sent = sentiment_by_sym.get(sym, {})
        symbol_summaries.append(
            f"- {sym} ({company}): Stage2={s2.get('stage2_score', 'N/A')}/7, "
            f"RS={s2.get('rs_score', 'N/A')}, "
            f"52w_from_high={s2.get('pct_from_52w_high', 'N/A')}%, "
            f"structure={s2.get('market_structure', 'N/A')}, "
            f"gc_above={s2.get('gc_above', False)}, "
            f"gc_breakout={s2.get('gc_breakout', False)}, "
            f"bear_flag={s2.get('bear_flag', False)}, "
            f"social_sentiment={sent.get('sentiment', 'N/A')}, "
            f"composite_score={sent.get('composite_score', 'N/A')}, "
            f"social_reason={sent.get('key_reason', 'N/A')}"
        )

    symbols_block = "\n".join(symbol_summaries)
    slot_kor = "장 개장 전" if slot == "pre_open" else "장 마감 후"

    return f"""You are a professional stock market analyst. Based on the following technical and social data, generate a trading brief in JSON format.

MARKET DATA ({slot_kor}):
- Risk Regime: {regime.get('regime', 'N/A')} (score: {regime.get('total', 'N/A')}/100)
- Regime components: Trend={regime.get('components', {}).get('trend', 'N/A')}, Breadth={regime.get('components', {}).get('breadth', 'N/A')}, Credit={regime.get('components', {}).get('credit', 'N/A')}, Volatility={regime.get('components', {}).get('volatility', 'N/A')}, Momentum={regime.get('components', {}).get('momentum', 'N/A')}
- SPY Distribution Days: {spy_dd.get('count', 'N/A')} ({spy_dd.get('level', 'N/A')})
- QQQ Distribution Days: {qqq_dd.get('count', 'N/A')} ({qqq_dd.get('level', 'N/A')})
- Market social sentiment: {sentiment.get('market', {}).get('sentiment', 'N/A')} (score={sentiment.get('market', {}).get('composite_score', 'N/A')})

SYMBOLS:
{symbols_block}

Generate ONE JSON object with this EXACT schema (no prose, no code fences):
{{
  "market_brief": {{
    "summary": "시장 전체 한 문장 요약 (한국어, 30자 이내)",
    "tone": one of ["bullish", "cautious", "bearish", "neutral"],
    "key_themes": ["테마1", "테마2"],
    "watch_points": "오늘 주의할 점 한 문장 (한국어)"
  }},
  "symbol_briefs": [
    {{
      "symbol": "TICKER",
      "setup_quality": one of ["A+", "A", "B", "C", "D"],
      "brief": "2-3문장 설명 (한국어)",
      "key_risk": "핵심 리스크 한 줄 (한국어)",
      "key_opportunity": "핵심 기회 한 줄 (한국어)",
      "action_bias": one of ["buy", "hold", "watch", "avoid"]
    }}
  ]
}}

setup_quality 기준:
- A+: Stage2 6-7점, 소셜 optimistic 이상, GC above/breakout, RS 70+
- A: Stage2 5-6점, 소셜 중립 이상, 구조 UPTREND
- B: Stage2 4-5점, 혼재 신호
- C: Stage2 3점 이하, 소셜 공포 또는 bear_flag
- D: Stage2 2점 이하 또는 downtrend 심화

symbol_briefs에 WATCHLIST 6종목 전부 포함 순서: TSLA, AAPL, NVDA, META, AMZN, GOOGL
Output raw JSON only."""


# ── Hermes 호출 ────────────────────────────────────────────────────────────────

def call_hermes(prompt: str) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT, env=env)
            if result.returncode != 0:
                print(f"[ERROR] hermes 비정상 종료: {result.stderr[:200]}", file=sys.stderr)
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            remaining = HERMES_RETRY - attempt
            if remaining > 0:
                print(f"[WARN] hermes 타임아웃 — 재시도 {remaining}회 남음", file=sys.stderr)
            else:
                print("[ERROR] hermes 타임아웃 — 재시도 소진", file=sys.stderr)
                return None
        except FileNotFoundError:
            print(f"[ERROR] hermes 명령 없음: {HERMES_CMD}", file=sys.stderr)
            return None
    return None


# ── JSON 파싱 / 검증 ──────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 블록 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}", file=sys.stderr)
        return None


VALID_TONES = {"bullish", "cautious", "bearish", "neutral"}
VALID_SETUP_QUALITY = {"A+", "A", "B", "C", "D"}
VALID_ACTION_BIAS = {"buy", "hold", "watch", "avoid"}


def validate_brief(data: dict) -> bool:
    mb = data.get("market_brief")
    if not isinstance(mb, dict):
        print("[WARN] market_brief 누락", file=sys.stderr)
        return False
    if mb.get("tone") not in VALID_TONES:
        print(f"[WARN] tone={mb.get('tone')!r} 허용값 아님", file=sys.stderr)
        return False
    if not isinstance(mb.get("key_themes"), list) or len(mb["key_themes"]) == 0:
        print("[WARN] key_themes 누락 또는 빈 배열", file=sys.stderr)
        return False

    symbol_briefs = data.get("symbol_briefs")
    if not isinstance(symbol_briefs, list) or len(symbol_briefs) == 0:
        print("[WARN] symbol_briefs 누락 또는 빈 배열", file=sys.stderr)
        return False
    for sb in symbol_briefs:
        if sb.get("setup_quality") not in VALID_SETUP_QUALITY:
            print(f"[WARN] {sb.get('symbol')}: setup_quality={sb.get('setup_quality')!r}", file=sys.stderr)
            return False
        if sb.get("action_bias") not in VALID_ACTION_BIAS:
            print(f"[WARN] {sb.get('symbol')}: action_bias={sb.get('action_bias')!r}", file=sys.stderr)
            return False
    return True


# ── git ───────────────────────────────────────────────────────────────────────

def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    def run(args):
        return subprocess.run(args, cwd=repo, capture_output=True, text=True)

    rel_history = str(history_path.relative_to(repo))
    run(["git", "add", "brief/latest.json", rel_history])
    result = run(["git", "commit", "-m", f"brief: {date_str} {time_str} update"])
    if result.returncode != 0:
        if "nothing to commit" in result.stdout + result.stderr:
            print("[INFO] 커밋할 변경사항 없음", file=sys.stderr)
            return True
        print(f"[ERROR] git commit 실패: {result.stderr[:300]}", file=sys.stderr)
        return False
    result = run(["git", "push"])
    if result.returncode != 0:
        print(f"[ERROR] git push 실패: {result.stderr[:300]}", file=sys.stderr)
        return False
    return True


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slot = detect_slot(now)
    print(f"[INFO] 슬롯: {slot}, 시각: {now_iso}")

    print("[INFO] 기술적 데이터 수집 중...")
    tech = fetch_technical_context()

    print("[INFO] 심리 데이터 로드 중...")
    sentiment = load_sentiment()

    prompt = build_brief_prompt(tech, sentiment, slot)
    print("[INFO] Grok 호출 중...")
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate_brief(parsed):
        print("[ERROR] Brief 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "1.0",
        "slot": slot,
        "market_brief": parsed["market_brief"],
        "symbol_briefs": parsed["symbol_briefs"],
    }

    latest_path = REPO_PATH / "brief" / "latest.json"
    history_dir = REPO_PATH / "brief" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"{date_str}_{slot}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}, {history_path}")

    push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
    print(f"{'[OK]' if push_ok else '[WARN]'} Brief 수집 완료")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: collect_brief.py 실행 테스트 (dry-run)**

```bash
cd /Users/jerry/dev/market-sentiment-data
SENTIMENT_SLOT=pre_open python collect/collect_brief.py
```

Expected: `[OK] Brief 수집 완료` 출력, `brief/latest.json` 갱신 확인

- [ ] **Step 3: Commit**

```bash
git add collect/collect_brief.py
git commit -m "feat: collect_brief.py — AI Daily Brief 수집기 추가"
git push
```

---

## Task 3: collect/collect_earnings.py 구현

**Files:**
- Create: `collect/collect_earnings.py`

- [ ] **Step 1: collect_earnings.py 파일 생성**

```python
#!/usr/bin/env python3
"""
Earnings Intelligence 수집기
① yfinance .calendar + .earnings_history로 어닝 데이터 수집
② Grok(Hermes)으로 어닝 리스크 해석 생성
③ earnings/latest.json + earnings/history/<date>.json 저장 → git push
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))

WATCHLIST = ["TSLA", "AAPL", "NVDA", "META", "AMZN", "GOOGL"]
UPCOMING_WINDOW_DAYS = 60
RECENT_QUARTERS = 8


# ── yfinance 수집 ──────────────────────────────────────────────────────────────

def fetch_earnings_data(symbols: list[str], today: datetime) -> tuple[list[dict], list[dict]]:
    """워치리스트 전체 어닝 데이터 수집. (upcoming_raw, recent_raw) 반환."""
    upcoming_raw = []
    recent_raw = []

    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)

            # 다음 어닝 날짜
            cal = ticker.calendar
            earnings_date = None
            if cal is not None and not cal.empty:
                # calendar는 DataFrame 또는 dict
                if hasattr(cal, 'columns') and 'Earnings Date' in cal.columns:
                    ed_val = cal['Earnings Date'].iloc[0] if len(cal) > 0 else None
                elif isinstance(cal, dict) and 'Earnings Date' in cal:
                    ed_val = cal['Earnings Date']
                    if isinstance(ed_val, list):
                        ed_val = ed_val[0] if ed_val else None
                else:
                    ed_val = None

                if ed_val is not None:
                    try:
                        if hasattr(ed_val, 'date'):
                            earnings_date = ed_val.date()
                        else:
                            earnings_date = datetime.strptime(str(ed_val)[:10], "%Y-%m-%d").date()
                    except Exception:
                        earnings_date = None

            # EPS/Revenue 예상치
            eps_estimate = None
            rev_estimate_b = None
            if cal is not None:
                try:
                    if hasattr(cal, 'columns'):
                        if 'EPS Estimate' in cal.columns:
                            eps_estimate = float(cal['EPS Estimate'].iloc[0])
                        if 'Revenue Estimate' in cal.columns:
                            rev_estimate_b = round(float(cal['Revenue Estimate'].iloc[0]) / 1e9, 2)
                    elif isinstance(cal, dict):
                        if 'EPS Estimate' in cal:
                            val = cal['EPS Estimate']
                            eps_estimate = float(val[0] if isinstance(val, list) else val)
                        if 'Revenue Estimate' in cal:
                            val = cal['Revenue Estimate']
                            rev_raw = float(val[0] if isinstance(val, list) else val)
                            rev_estimate_b = round(rev_raw / 1e9, 2)
                except Exception:
                    pass

            # 과거 어닝 히스토리
            hist = ticker.earnings_history
            beat_count = 0
            total_count = 0
            last_result = None

            if hist is not None and not hist.empty:
                hist = hist.sort_index(ascending=False)
                recent = hist.head(RECENT_QUARTERS)
                for _, row in recent.iterrows():
                    actual = row.get('epsActual') if hasattr(row, 'get') else row.get('EPS Actual')
                    estimate = row.get('epsEstimate') if hasattr(row, 'get') else row.get('EPS Estimate')
                    if actual is not None and estimate is not None:
                        total_count += 1
                        if float(actual) > float(estimate):
                            beat_count += 1

                # 가장 최근 결과
                if len(hist) > 0:
                    last_row = hist.iloc[0]
                    actual = last_row.get('epsActual') or last_row.get('EPS Actual')
                    estimate_last = last_row.get('epsEstimate') or last_row.get('EPS Estimate')
                    report_date = hist.index[0]
                    if actual is not None and estimate_last is not None:
                        actual_f = float(actual)
                        estimate_f = float(estimate_last)
                        surprise_pct = ((actual_f - estimate_f) / abs(estimate_f) * 100) if estimate_f != 0 else 0.0
                        last_result = {
                            "symbol": sym,
                            "report_date": str(report_date)[:10],
                            "eps_actual": round(actual_f, 2),
                            "eps_estimate": round(estimate_f, 2),
                            "surprise_pct": round(surprise_pct, 2),
                        }

            beat_rate = round(beat_count / total_count, 2) if total_count >= 4 else None

            # 60일 이내 어닝만 upcoming에 포함
            if earnings_date is not None:
                days_until = (earnings_date - today.date()).days
                if 0 <= days_until <= UPCOMING_WINDOW_DAYS:
                    upcoming_raw.append({
                        "symbol": sym,
                        "earnings_date": str(earnings_date),
                        "days_until": days_until,
                        "eps_estimate": round(eps_estimate, 2) if eps_estimate is not None else None,
                        "revenue_estimate_b": rev_estimate_b,
                        "historical_beat_rate": beat_rate,
                    })

            if last_result is not None:
                recent_raw.append(last_result)

            print(f"[OK]   {sym}: earnings_date={earnings_date}, beat_rate={beat_rate}")

        except Exception as e:
            print(f"[WARN] {sym}: 수집 실패 — {e}", file=sys.stderr)

    upcoming_raw.sort(key=lambda x: x["days_until"])
    return upcoming_raw, recent_raw


# ── 프롬프트 빌더 ──────────────────────────────────────────────────────────────

def build_earnings_prompt(upcoming_raw: list[dict], recent_raw: list[dict]) -> str:
    if not upcoming_raw and not recent_raw:
        return ""

    upcoming_block = "\n".join([
        f"- {u['symbol']}: {u['days_until']}일 후 ({u['earnings_date']}), "
        f"EPS estimate={u['eps_estimate']}, revenue_estimate={u['revenue_estimate_b']}B, "
        f"historical_beat_rate={u['historical_beat_rate']}"
        for u in upcoming_raw
    ]) or "없음"

    recent_block = "\n".join([
        f"- {r['symbol']}: {r['report_date']}, "
        f"EPS actual={r['eps_actual']} vs estimate={r['eps_estimate']} "
        f"(surprise {r['surprise_pct']:+.1f}%)"
        for r in recent_raw
    ]) or "없음"

    return f"""You are a professional earnings analyst. Based on the following data, generate earnings intelligence in JSON format.

UPCOMING EARNINGS (60일 이내):
{upcoming_block}

RECENT RESULTS (지난 분기):
{recent_block}

Generate ONE JSON object with this EXACT schema (no prose, no code fences):
{{
  "upcoming_earnings": [
    {{
      "symbol": "TICKER",
      "earnings_date": "YYYY-MM-DD",
      "days_until": <int>,
      "eps_estimate": <float|null>,
      "revenue_estimate_b": <float|null>,
      "historical_beat_rate": <float|null>,
      "ai_summary": "2-3문장 어닝 맥락 설명 (한국어)",
      "risk_level": one of ["high", "med", "low"],
      "action_note": "트레이더를 위한 한 줄 조언 (한국어)"
    }}
  ],
  "recent_results": [
    {{
      "symbol": "TICKER",
      "report_date": "YYYY-MM-DD",
      "eps_actual": <float>,
      "eps_estimate": <float>,
      "surprise_pct": <float>,
      "ai_reaction": "시장 반응 및 트레이더 시사점 한 줄 (한국어)"
    }}
  ]
}}

risk_level 기준:
- high: 어닝 3일 이내, historical_beat_rate < 0.7, 혹은 가이던스 불확실성 높음
- med: 어닝 4-14일, beat_rate 0.7-0.85
- low: 어닝 15일 이상, beat_rate > 0.85

Output raw JSON only."""


# ── Hermes 호출 ────────────────────────────────────────────────────────────────

def call_hermes(prompt: str) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT, env=env)
            if result.returncode != 0:
                print(f"[ERROR] hermes 비정상 종료: {result.stderr[:200]}", file=sys.stderr)
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            remaining = HERMES_RETRY - attempt
            if remaining > 0:
                print(f"[WARN] hermes 타임아웃 — 재시도 {remaining}회 남음", file=sys.stderr)
            else:
                print("[ERROR] hermes 타임아웃 — 재시도 소진", file=sys.stderr)
                return None
        except FileNotFoundError:
            print(f"[ERROR] hermes 명령 없음: {HERMES_CMD}", file=sys.stderr)
            return None
    return None


# ── JSON 파싱 / 검증 ──────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 블록 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}", file=sys.stderr)
        return None


VALID_RISK_LEVELS = {"high", "med", "low"}


def validate_earnings(data: dict) -> bool:
    upcoming = data.get("upcoming_earnings")
    recent = data.get("recent_results")
    if not isinstance(upcoming, list) or not isinstance(recent, list):
        print("[WARN] upcoming_earnings 또는 recent_results 누락", file=sys.stderr)
        return False
    for item in upcoming:
        if item.get("risk_level") not in VALID_RISK_LEVELS:
            print(f"[WARN] {item.get('symbol')}: risk_level={item.get('risk_level')!r}", file=sys.stderr)
            return False
    return True


# ── git ───────────────────────────────────────────────────────────────────────

def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    def run(args):
        return subprocess.run(args, cwd=repo, capture_output=True, text=True)

    rel_history = str(history_path.relative_to(repo))
    run(["git", "add", "earnings/latest.json", rel_history])
    result = run(["git", "commit", "-m", f"earnings: {date_str} {time_str} update"])
    if result.returncode != 0:
        if "nothing to commit" in result.stdout + result.stderr:
            print("[INFO] 커밋할 변경사항 없음", file=sys.stderr)
            return True
        print(f"[ERROR] git commit 실패: {result.stderr[:300]}", file=sys.stderr)
        return False
    result = run(["git", "push"])
    if result.returncode != 0:
        print(f"[ERROR] git push 실패: {result.stderr[:300]}", file=sys.stderr)
        return False
    return True


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    print(f"[INFO] 어닝 수집 시작: {now_iso}")

    upcoming_raw, recent_raw = fetch_earnings_data(WATCHLIST, now)
    print(f"[INFO] upcoming={len(upcoming_raw)}, recent={len(recent_raw)}")

    if not upcoming_raw and not recent_raw:
        print("[INFO] 어닝 데이터 없음 — 빈 스냅샷 저장")
        parsed = {"upcoming_earnings": [], "recent_results": []}
    else:
        prompt = build_earnings_prompt(upcoming_raw, recent_raw)
        print("[INFO] Grok 호출 중...")
        raw_text = call_hermes(prompt)
        if raw_text is None:
            print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
            sys.exit(1)
        parsed = extract_json(raw_text)
        if parsed is None or not validate_earnings(parsed):
            print("[ERROR] 어닝 검증 실패 — 종료", file=sys.stderr)
            sys.exit(1)

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "1.0",
        "upcoming_earnings": parsed["upcoming_earnings"],
        "recent_results": parsed["recent_results"],
    }

    latest_path = REPO_PATH / "earnings" / "latest.json"
    history_dir = REPO_PATH / "earnings" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"{date_str}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}")

    push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
    print(f"{'[OK]' if push_ok else '[WARN]'} 어닝 수집 완료")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: collect_earnings.py 실행 테스트 (dry-run)**

```bash
cd /Users/jerry/dev/market-sentiment-data
python collect/collect_earnings.py
```

Expected: `[OK] 어닝 수집 완료`, `earnings/latest.json` 갱신 확인

- [ ] **Step 3: Commit**

```bash
git add collect/collect_earnings.py
git commit -m "feat: collect_earnings.py — Earnings Intelligence 수집기 추가"
git push
```

---

## Task 4: Sniperboard 백엔드 — schemas.py 모델 추가

**Files:**
- Modify: `sniperboard/backend/api/schemas.py` (마지막 줄에 추가)

- [ ] **Step 1: Brief/Earnings Pydantic 모델을 schemas.py 끝에 추가**

```python
# --- AI Brief ---

class MarketBrief(BaseModel):
    summary: str
    tone: str  # "bullish" | "cautious" | "bearish" | "neutral"
    key_themes: List[str]
    watch_points: str

class SymbolBrief(BaseModel):
    symbol: str
    setup_quality: str  # "A+" | "A" | "B" | "C" | "D"
    brief: str
    key_risk: str
    key_opportunity: str
    action_bias: str  # "buy" | "hold" | "watch" | "avoid"

class BriefData(BaseModel):
    generated_at: Optional[str] = None
    schema_version: Optional[str] = None
    slot: Optional[str] = None
    market_brief: Optional[MarketBrief] = None
    symbol_briefs: Optional[List[SymbolBrief]] = None

class BriefResponse(BaseModel):
    available: bool
    data: Optional[BriefData] = None
    error: Optional[str] = None

# --- Earnings Intelligence ---

class UpcomingEarning(BaseModel):
    symbol: str
    earnings_date: str
    days_until: int
    eps_estimate: Optional[float] = None
    revenue_estimate_b: Optional[float] = None
    historical_beat_rate: Optional[float] = None
    ai_summary: str
    risk_level: str  # "high" | "med" | "low"
    action_note: str

class RecentResult(BaseModel):
    symbol: str
    report_date: str
    eps_actual: float
    eps_estimate: float
    surprise_pct: float
    ai_reaction: str

class EarningsData(BaseModel):
    generated_at: Optional[str] = None
    schema_version: Optional[str] = None
    upcoming_earnings: Optional[List[UpcomingEarning]] = None
    recent_results: Optional[List[RecentResult]] = None

class EarningsResponse(BaseModel):
    available: bool
    data: Optional[EarningsData] = None
    error: Optional[str] = None
```

- [ ] **Step 2: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add backend/api/schemas.py
git commit -m "feat: Brief/Earnings Pydantic 모델 추가"
```

---

## Task 5: Sniperboard 백엔드 — services 추가

**Files:**
- Create: `sniperboard/backend/services/brief_service.py`
- Create: `sniperboard/backend/services/earnings_service.py`

`sentiment_service.py`와 동일한 구조 — GitHub raw URL fetch + 인메모리 캐시.

- [ ] **Step 1: brief_service.py 생성**

```python
"""AI Daily Brief 서비스 — GitHub raw URL fetch + 인메모리 캐시."""

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

BRIEF_DATA_URL = os.environ.get("BRIEF_DATA_URL", "")
SENTIMENT_DATA_TOKEN = os.environ.get("SENTIMENT_DATA_TOKEN", "")

CACHE_TTL = 1800  # 30분
_cache: dict[str, Any] = {"data": None, "ts": 0.0}


def _auth_headers() -> dict:
    if SENTIMENT_DATA_TOKEN:
        return {"Authorization": f"token {SENTIMENT_DATA_TOKEN}"}
    return {}


def fetch_brief() -> dict:
    """brief/latest.json 반환. 30분 TTL 인메모리 캐시."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]

    if not BRIEF_DATA_URL:
        return {"available": False, "error": "BRIEF_DATA_URL 환경변수가 설정되지 않았습니다."}

    try:
        resp = requests.get(BRIEF_DATA_URL, headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"brief fetch 실패: {e}")
        return {"available": False, "error": f"GitHub raw fetch 실패: {e}"}

    if data.get("generated_at") is None:
        return {"available": False, "error": "Brief 데이터가 아직 생성되지 않았습니다."}

    result = {"available": True, "data": data}
    _cache["data"] = result
    _cache["ts"] = now
    return result
```

- [ ] **Step 2: earnings_service.py 생성**

```python
"""Earnings Intelligence 서비스 — GitHub raw URL fetch + 인메모리 캐시."""

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

EARNINGS_DATA_URL = os.environ.get("EARNINGS_DATA_URL", "")
SENTIMENT_DATA_TOKEN = os.environ.get("SENTIMENT_DATA_TOKEN", "")

CACHE_TTL = 3600  # 60분
_cache: dict[str, Any] = {"data": None, "ts": 0.0}


def _auth_headers() -> dict:
    if SENTIMENT_DATA_TOKEN:
        return {"Authorization": f"token {SENTIMENT_DATA_TOKEN}"}
    return {}


def fetch_earnings() -> dict:
    """earnings/latest.json 반환. 60분 TTL 인메모리 캐시."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]

    if not EARNINGS_DATA_URL:
        return {"available": False, "error": "EARNINGS_DATA_URL 환경변수가 설정되지 않았습니다."}

    try:
        resp = requests.get(EARNINGS_DATA_URL, headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"earnings fetch 실패: {e}")
        return {"available": False, "error": f"GitHub raw fetch 실패: {e}"}

    if data.get("generated_at") is None:
        return {"available": False, "error": "Earnings 데이터가 아직 생성되지 않았습니다."}

    result = {"available": True, "data": data}
    _cache["data"] = result
    _cache["ts"] = now
    return result
```

- [ ] **Step 3: 서비스 단위 테스트 작성 — `backend/tests/test_brief_service.py`**

```python
"""brief_service 단위 테스트
cd /Users/jerry/dev/sniperboard/backend && python -m pytest tests/test_brief_service.py -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import services.brief_service as svc


SAMPLE_BRIEF = {
    "generated_at": "2026-05-24T13:00:00Z",
    "schema_version": "1.0",
    "slot": "pre_open",
    "market_brief": {
        "summary": "SPY EMA200 위 유지, DD 경고권",
        "tone": "cautious",
        "key_themes": ["Fed 동결 기대"],
        "watch_points": "QQQ DD 증가 주시",
    },
    "symbol_briefs": [
        {
            "symbol": "NVDA",
            "setup_quality": "A+",
            "brief": "VCP 패턴 형성 중",
            "key_risk": "어닝 근접",
            "key_opportunity": "돌파 시 +18%",
            "action_bias": "watch",
        }
    ],
}


def _make_resp(data: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


class TestFetchBrief(unittest.TestCase):
    def setUp(self):
        # 각 테스트 전 캐시 초기화
        svc._cache["data"] = None
        svc._cache["ts"] = 0.0

    def test_returns_unavailable_when_no_url(self):
        with patch.object(svc, "BRIEF_DATA_URL", ""):
            result = svc.fetch_brief()
        self.assertFalse(result["available"])
        self.assertIn("BRIEF_DATA_URL", result["error"])

    def test_returns_data_on_success(self):
        with patch.object(svc, "BRIEF_DATA_URL", "http://fake.url"), \
             patch("requests.get", return_value=_make_resp(SAMPLE_BRIEF)):
            result = svc.fetch_brief()
        self.assertTrue(result["available"])
        self.assertEqual(result["data"]["slot"], "pre_open")
        self.assertEqual(result["data"]["market_brief"]["tone"], "cautious")

    def test_returns_unavailable_on_request_error(self):
        with patch.object(svc, "BRIEF_DATA_URL", "http://fake.url"), \
             patch("requests.get", side_effect=Exception("network error")):
            result = svc.fetch_brief()
        self.assertFalse(result["available"])
        self.assertIn("fetch 실패", result["error"])

    def test_cache_hit_skips_request(self):
        import time
        svc._cache["data"] = {"available": True, "data": SAMPLE_BRIEF}
        svc._cache["ts"] = time.monotonic()
        with patch("requests.get") as mock_get:
            result = svc.fetch_brief()
        mock_get.assert_not_called()
        self.assertTrue(result["available"])

    def test_returns_unavailable_for_placeholder_json(self):
        placeholder = {"generated_at": None, "schema_version": "1.0", "slot": None,
                       "market_brief": None, "symbol_briefs": []}
        with patch.object(svc, "BRIEF_DATA_URL", "http://fake.url"), \
             patch("requests.get", return_value=_make_resp(placeholder)):
            result = svc.fetch_brief()
        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: brief_service 테스트 실행**

```bash
cd /Users/jerry/dev/sniperboard/backend
python -m pytest tests/test_brief_service.py -v
```

Expected:
```
tests/test_brief_service.py::TestFetchBrief::test_returns_unavailable_when_no_url PASSED
tests/test_brief_service.py::TestFetchBrief::test_returns_data_on_success PASSED
tests/test_brief_service.py::TestFetchBrief::test_returns_unavailable_on_request_error PASSED
tests/test_brief_service.py::TestFetchBrief::test_cache_hit_skips_request PASSED
tests/test_brief_service.py::TestFetchBrief::test_returns_unavailable_for_placeholder_json PASSED
```

- [ ] **Step 5: earnings_service 테스트 작성 — `backend/tests/test_earnings_service.py`**

```python
"""earnings_service 단위 테스트
cd /Users/jerry/dev/sniperboard/backend && python -m pytest tests/test_earnings_service.py -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import services.earnings_service as svc


SAMPLE_EARNINGS = {
    "generated_at": "2026-05-24T13:00:00Z",
    "schema_version": "1.0",
    "upcoming_earnings": [
        {
            "symbol": "NVDA",
            "earnings_date": "2026-05-28",
            "days_until": 4,
            "eps_estimate": 0.89,
            "revenue_estimate_b": 43.1,
            "historical_beat_rate": 0.92,
            "ai_summary": "8분기 연속 beat",
            "risk_level": "high",
            "action_note": "신규 진입 자제",
        }
    ],
    "recent_results": [
        {
            "symbol": "AAPL",
            "report_date": "2026-05-02",
            "eps_actual": 1.65,
            "eps_estimate": 1.62,
            "surprise_pct": 1.85,
            "ai_reaction": "소폭 beat, 가이던스 보수적",
        }
    ],
}


def _make_resp(data: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


class TestFetchEarnings(unittest.TestCase):
    def setUp(self):
        svc._cache["data"] = None
        svc._cache["ts"] = 0.0

    def test_returns_unavailable_when_no_url(self):
        with patch.object(svc, "EARNINGS_DATA_URL", ""):
            result = svc.fetch_earnings()
        self.assertFalse(result["available"])

    def test_returns_data_on_success(self):
        with patch.object(svc, "EARNINGS_DATA_URL", "http://fake.url"), \
             patch("requests.get", return_value=_make_resp(SAMPLE_EARNINGS)):
            result = svc.fetch_earnings()
        self.assertTrue(result["available"])
        self.assertEqual(result["data"]["upcoming_earnings"][0]["symbol"], "NVDA")
        self.assertEqual(result["data"]["upcoming_earnings"][0]["risk_level"], "high")

    def test_returns_unavailable_on_request_error(self):
        with patch.object(svc, "EARNINGS_DATA_URL", "http://fake.url"), \
             patch("requests.get", side_effect=Exception("timeout")):
            result = svc.fetch_earnings()
        self.assertFalse(result["available"])

    def test_cache_hit_skips_request(self):
        import time
        svc._cache["data"] = {"available": True, "data": SAMPLE_EARNINGS}
        svc._cache["ts"] = time.monotonic()
        with patch("requests.get") as mock_get:
            result = svc.fetch_earnings()
        mock_get.assert_not_called()
        self.assertTrue(result["available"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: earnings_service 테스트 실행**

```bash
cd /Users/jerry/dev/sniperboard/backend
python -m pytest tests/test_earnings_service.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 7: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add backend/services/brief_service.py backend/services/earnings_service.py \
        backend/tests/test_brief_service.py backend/tests/test_earnings_service.py
git commit -m "feat: brief_service, earnings_service 추가 (GitHub raw URL 프록시)"
```

---

## Task 6: Sniperboard 백엔드 — endpoints.py + docker-compose 추가

**Files:**
- Modify: `sniperboard/backend/api/endpoints.py`
- Modify: `sniperboard/docker-compose.yml`

- [ ] **Step 1: endpoints.py — import 추가**

`endpoints.py` 상단 import 블록에 추가:
```python
from api.schemas import (
    OHLCVResponse, LatestSignalResponse, DailyResponse, WatchlistResponse,
    MacroResponse, RegimeResponse, DistributionDayResponse, SentimentResponse,
    BriefResponse, EarningsResponse,  # ← 추가
)
from services.sentiment_service import fetch_latest, enrich_with_delta, fetch_today_slots
from services.brief_service import fetch_brief      # ← 추가
from services.earnings_service import fetch_earnings  # ← 추가
```

- [ ] **Step 2: endpoints.py — /brief 엔드포인트 추가**

`/distribution-days` 엔드포인트 아래에 추가:
```python
@router.get("/brief", response_model=BriefResponse)
async def get_brief_endpoint():
    """AI Daily Brief 최신 스냅샷. 실패 시 available:false로 200 반환."""
    try:
        result = fetch_brief()
        if not result.get("available"):
            return {"available": False, "error": result.get("error", "데이터 없음")}
        return {"available": True, "data": result["data"]}
    except Exception as e:
        logger.error(f"Error in /brief endpoint: {e}", exc_info=True)
        return {"available": False, "error": "Brief 데이터 처리 중 오류 발생"}


@router.get("/earnings", response_model=EarningsResponse)
async def get_earnings_endpoint():
    """Earnings Intelligence 최신 스냅샷. 실패 시 available:false로 200 반환."""
    try:
        result = fetch_earnings()
        if not result.get("available"):
            return {"available": False, "error": result.get("error", "데이터 없음")}
        return {"available": True, "data": result["data"]}
    except Exception as e:
        logger.error(f"Error in /earnings endpoint: {e}", exc_info=True)
        return {"available": False, "error": "Earnings 데이터 처리 중 오류 발생"}
```

- [ ] **Step 3: docker-compose.yml — 환경변수 추가**

`docker-compose.yml`의 backend environment 블록에 추가:
```yaml
      BRIEF_DATA_URL: "https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/brief/latest.json"
      EARNINGS_DATA_URL: "https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/earnings/latest.json"
```

(기존 `SENTIMENT_DATA_URL` 줄 바로 아래에 추가)

- [ ] **Step 4: 엔드포인트 동작 확인**

```bash
cd /Users/jerry/dev/sniperboard/backend
uvicorn main:app --reload --port 8000 &
curl -s http://localhost:8000/api/brief | python3 -m json.tool | head -20
curl -s http://localhost:8000/api/earnings | python3 -m json.tool | head -20
```

Expected: `{"available": false, "error": "BRIEF_DATA_URL 환경변수가 설정되지 않았습니다."}` (로컬 환경변수 없음 — 정상)

BRIEF_DATA_URL을 직접 설정하면 실제 데이터 반환:
```bash
BRIEF_DATA_URL="https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/brief/latest.json" \
uvicorn main:app --reload --port 8000
```

- [ ] **Step 5: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add backend/api/endpoints.py docker-compose.yml
git commit -m "feat: /brief, /earnings API 엔드포인트 추가"
```

---

## Task 7: Sniperboard 프론트엔드 — types.ts 타입 추가

**Files:**
- Modify: `sniperboard/frontend/app/types.ts`

- [ ] **Step 1: types.ts 끝에 Brief/Earnings 타입 추가**

```typescript
// --- AI Brief ---

export interface MarketBrief {
  summary: string;
  tone: 'bullish' | 'cautious' | 'bearish' | 'neutral';
  key_themes: string[];
  watch_points: string;
}

export interface SymbolBrief {
  symbol: string;
  setup_quality: 'A+' | 'A' | 'B' | 'C' | 'D';
  brief: string;
  key_risk: string;
  key_opportunity: string;
  action_bias: 'buy' | 'hold' | 'watch' | 'avoid';
}

export interface BriefData {
  generated_at?: string | null;
  schema_version?: string | null;
  slot?: string | null;
  market_brief?: MarketBrief | null;
  symbol_briefs?: SymbolBrief[] | null;
}

export interface BriefResponse {
  available: boolean;
  data?: BriefData | null;
  error?: string | null;
}

// --- Earnings Intelligence ---

export interface UpcomingEarning {
  symbol: string;
  earnings_date: string;
  days_until: number;
  eps_estimate?: number | null;
  revenue_estimate_b?: number | null;
  historical_beat_rate?: number | null;
  ai_summary: string;
  risk_level: 'high' | 'med' | 'low';
  action_note: string;
}

export interface RecentResult {
  symbol: string;
  report_date: string;
  eps_actual: number;
  eps_estimate: number;
  surprise_pct: number;
  ai_reaction: string;
}

export interface EarningsData {
  generated_at?: string | null;
  schema_version?: string | null;
  upcoming_earnings?: UpcomingEarning[] | null;
  recent_results?: RecentResult[] | null;
}

export interface EarningsResponse {
  available: boolean;
  data?: EarningsData | null;
  error?: string | null;
}

// setup_quality 색상 매핑
export const SETUP_QUALITY_META: Record<string, { color: string; label: string }> = {
  'A+': { color: 'bull',   label: 'A+' },
  'A':  { color: 'teal',  label: 'A'  },
  'B':  { color: 'warn',  label: 'B'  },
  'C':  { color: 'bear',  label: 'C'  },
  'D':  { color: 'bear',  label: 'D'  },
};

// earnings risk_level 색상
export const EARNINGS_RISK_META: Record<string, { color: string; dot: string }> = {
  high: { color: 'bear', dot: '●' },
  med:  { color: 'warn', dot: '●' },
  low:  { color: 'teal', dot: '●' },
};
```

- [ ] **Step 2: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/app/types.ts
git commit -m "feat: Brief/Earnings TypeScript 타입 추가"
```

---

## Task 8: Sniperboard 프론트엔드 — useBrief.ts, useEarnings.ts 훅

**Files:**
- Create: `sniperboard/frontend/hooks/useBrief.ts`
- Create: `sniperboard/frontend/hooks/useEarnings.ts`

`useSentiment.ts` 패턴을 그대로 따른다.

- [ ] **Step 1: useSentiment.ts 패턴 확인**

```bash
cat /Users/jerry/dev/sniperboard/frontend/hooks/useSentiment.ts
```

- [ ] **Step 2: useBrief.ts 생성**

```typescript
'use client';

import { useQuery } from '@tanstack/react-query';
import { BriefResponse } from '@/app/types';
import { API_BASE } from '@/app/types';

async function fetchBrief(): Promise<BriefResponse> {
  const res = await fetch(`${API_BASE}/api/brief`);
  if (!res.ok) return { available: false, error: `HTTP ${res.status}` };
  return res.json();
}

export function useBrief() {
  const { data, isLoading, error } = useQuery<BriefResponse>({
    queryKey: ['brief'],
    queryFn: fetchBrief,
    staleTime: 30 * 60 * 1000,   // 30분
    refetchInterval: 30 * 60 * 1000,
  });

  return {
    briefData: data?.available ? data.data : null,
    isLoading,
    error: data?.error ?? (error ? String(error) : null),
  };
}
```

- [ ] **Step 3: useEarnings.ts 생성**

```typescript
'use client';

import { useQuery } from '@tanstack/react-query';
import { EarningsResponse } from '@/app/types';
import { API_BASE } from '@/app/types';

async function fetchEarnings(): Promise<EarningsResponse> {
  const res = await fetch(`${API_BASE}/api/earnings`);
  if (!res.ok) return { available: false, error: `HTTP ${res.status}` };
  return res.json();
}

export function useEarnings() {
  const { data, isLoading, error } = useQuery<EarningsResponse>({
    queryKey: ['earnings'],
    queryFn: fetchEarnings,
    staleTime: 60 * 60 * 1000,   // 60분
    refetchInterval: 60 * 60 * 1000,
  });

  return {
    earningsData: data?.available ? data.data : null,
    isLoading,
    error: data?.error ?? (error ? String(error) : null),
  };
}
```

- [ ] **Step 4: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/hooks/useBrief.ts frontend/hooks/useEarnings.ts
git commit -m "feat: useBrief, useEarnings 훅 추가"
```

---

## Task 9: OverviewBoard — AI Brief 카드 + Earnings Calendar 카드

**Files:**
- Modify: `sniperboard/frontend/components/boards/OverviewBoard.tsx`

현재 AI Insight 카드(line 108~143)의 hardcoded regime 텍스트를 실제 Brief 데이터로 교체하고, Earnings Calendar 카드를 새로 추가한다.

- [ ] **Step 1: import 추가 — OverviewBoard.tsx 상단**

기존 import들 아래에 추가:
```typescript
import { useBrief } from '@/hooks/useBrief';
import { useEarnings } from '@/hooks/useEarnings';
import { SymbolBrief, UpcomingEarning, SETUP_QUALITY_META, EARNINGS_RISK_META } from '@/app/types';
```

- [ ] **Step 2: OverviewBoard 함수 내 훅 호출 추가**

기존 `const { dailyData } = useDaily(symbol);` 줄 아래에 추가:
```typescript
  const { briefData } = useBrief();
  const { earningsData } = useEarnings();
```

- [ ] **Step 3: AI Insight 카드 본문 교체 (line 116~142)**

기존 `<div className="ai-card__body">` 블록 내부를 교체:

```tsx
          <div className="ai-card__body">
            {briefData?.market_brief ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span className={`badge ${
                    briefData.market_brief.tone === 'bullish' ? 'bull' :
                    briefData.market_brief.tone === 'bearish' ? 'bear' :
                    briefData.market_brief.tone === 'cautious' ? 'warn' : 'neutral'
                  }`}>{
                    briefData.market_brief.tone === 'bullish' ? '강세' :
                    briefData.market_brief.tone === 'bearish' ? '약세' :
                    briefData.market_brief.tone === 'cautious' ? '주의' : '중립'
                  }</span>
                  <span style={{ fontSize: 13 }}>{briefData.market_brief.summary}</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                  {briefData.market_brief.key_themes.map((theme, i) => (
                    <span key={i} className="badge neutral" style={{ fontSize: 10.5 }}>{theme}</span>
                  ))}
                </div>
                <div style={{ color: 'var(--fg-muted)', fontSize: 11.5 }}>
                  주시: {briefData.market_brief.watch_points}
                </div>
                <div style={{ color: 'var(--fg-subtle)', fontSize: 10, marginTop: 4 }}>
                  AI 의견 — 매매 신호 아님 · {briefData.slot === 'pre_open' ? '장 전' : '장 후'} 기준
                </div>
              </>
            ) : regimeData ? (
              <>
                <div style={{ marginBottom: 6 }}>
                  현재 Risk Regime은{' '}
                  <strong>{REGIME_LABELS[regimeData.regime]?.[0] ?? regimeData.regime}</strong>
                  {' '}({regimeData.total ?? '—'}점) —{' '}
                  {regimeData.regime === 'RISK_ON' && '추세 추종 전략이 유효한 강세 환경입니다.'}
                  {regimeData.regime === 'CONSTRUCTIVE' && '선별적 진입이 가능한 우호적 환경입니다.'}
                  {regimeData.regime === 'MIXED' && '신호가 혼재합니다. 포지션 사이즈를 축소하세요.'}
                  {regimeData.regime === 'DEFENSIVE' && '약세 신호 우세. 현금 비중을 늘리세요.'}
                  {regimeData.regime === 'RISK_OFF' && '리스크 오프 국면. 신규 매수를 자제하세요.'}
                  {regimeData.regime === 'UNKNOWN' && '데이터 부족으로 판단이 어렵습니다.'}
                </div>
                <div style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
                  Trend {(regimeData.components.trend ?? 0).toFixed(1)} ·
                  Breadth {(regimeData.components.breadth ?? 0).toFixed(1)} ·
                  Credit {(regimeData.components.credit ?? 0).toFixed(1)} ·
                  Volatility {(regimeData.components.volatility ?? 0).toFixed(1)} ·
                  Momentum {(regimeData.components.momentum ?? 0).toFixed(1)}
                </div>
              </>
            ) : (
              <div style={{ color: 'var(--fg-muted)' }}>AI Brief 로딩 중...</div>
            )}
          </div>
```

- [ ] **Step 4: Earnings Calendar 카드 추가**

`{/* Regime gauge */}` 카드 바로 위에 새 카드 삽입:

```tsx
      {/* Earnings Calendar */}
      <Card title="Earnings Calendar" action="60일 이내">
        {earningsData?.upcoming_earnings && earningsData.upcoming_earnings.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {earningsData.upcoming_earnings.map((e: UpcomingEarning) => {
              const rm = EARNINGS_RISK_META[e.risk_level] ?? EARNINGS_RISK_META.med;
              return (
                <div key={e.symbol} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                  <span style={{ fontWeight: 600, width: 40, fontFamily: 'var(--mono)' }}>{e.symbol}</span>
                  <span style={{ color: 'var(--fg-muted)', flex: 1 }}>
                    {e.earnings_date.slice(5)} · {e.days_until}일 후
                  </span>
                  <span className={`badge ${rm.color}`} style={{ fontSize: 10 }}>
                    {rm.dot} {e.risk_level.toUpperCase()}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
            {earningsData === null ? 'Earnings 데이터 로딩 중...' : '60일 이내 어닝 없음'}
          </div>
        )}
      </Card>
```

- [ ] **Step 5: 프론트엔드 빌드 확인**

```bash
cd /Users/jerry/dev/sniperboard/frontend
npm run build 2>&1 | tail -20
```

Expected: `✓ Compiled successfully` (타입 오류 없음)

- [ ] **Step 6: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/components/boards/OverviewBoard.tsx
git commit -m "feat: OverviewBoard — AI Brief 카드 + Earnings Calendar 카드 추가"
```

---

## Task 10: DailyBoard — 어닝 배너 추가

**Files:**
- Modify: `sniperboard/frontend/components/boards/DailyBoard.tsx`

선택 종목에 60일 이내 어닝이 있을 때 차트 위 배너와 action_note를 표시한다.

- [ ] **Step 1: import 추가 — DailyBoard.tsx 상단**

```typescript
import { useEarnings } from '@/hooks/useEarnings';
import { UpcomingEarning } from '@/app/types';
```

- [ ] **Step 2: 훅 호출 추가**

`const { dailyData, isLoading } = useDaily(symbol);` 줄 아래에 추가:
```typescript
  const { earningsData } = useEarnings();
  const symbolEarning: UpcomingEarning | undefined = earningsData?.upcoming_earnings?.find(
    (e: UpcomingEarning) => e.symbol === symbol
  );
```

- [ ] **Step 3: 차트 카드 헤더에 배너 추가**

DailyBoard.tsx에서 `<div className="card__hd">` 블록을 찾아 `{/* Daily chart */}` 카드 안의 `card__bd` 시작 부분 바로 앞에 배너 삽입:

```tsx
        <div className="card__bd" style={{ paddingTop: 0 }}>
          {/* 어닝 배너 */}
          {symbolEarning && (
            <div style={{
              background: symbolEarning.risk_level === 'high' ? 'var(--warn)' : 'var(--border)',
              color: symbolEarning.risk_level === 'high' ? '#000' : 'var(--fg)',
              padding: '4px 12px',
              fontSize: 11.5,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              opacity: 0.9,
            }}>
              <span style={{ fontWeight: 700 }}>⚡ EARNINGS IN {symbolEarning.days_until}D</span>
              <span style={{ opacity: 0.8 }}>{symbolEarning.action_note}</span>
            </div>
          )}
          {isLoading ? (
```

- [ ] **Step 4: 빌드 확인**

```bash
cd /Users/jerry/dev/sniperboard/frontend
npm run build 2>&1 | tail -10
```

Expected: `✓ Compiled successfully`

- [ ] **Step 5: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/components/boards/DailyBoard.tsx
git commit -m "feat: DailyBoard — 어닝 배너 추가 (60일 이내 어닝 종목)"
```

---

## Task 11: SentimentBoard — setup_quality 배지 추가

**Files:**
- Modify: `sniperboard/frontend/components/boards/SentimentBoard.tsx`

- [ ] **Step 1: import 추가 — SentimentBoard.tsx 상단**

```typescript
import { useBrief } from '@/hooks/useBrief';
import { SymbolBrief, SETUP_QUALITY_META } from '@/app/types';
```

- [ ] **Step 2: 훅 호출 추가**

`const { sentimentData } = useSentiment();` 줄 아래에 추가:
```typescript
  const { briefData } = useBrief();
  const briefBySymbol = (briefData?.symbol_briefs ?? []).reduce(
    (acc: Record<string, SymbolBrief>, sb: SymbolBrief) => { acc[sb.symbol] = sb; return acc; },
    {}
  );
```

- [ ] **Step 3: 종목별 카드에 setup_quality 배지 추가**

SentimentBoard.tsx에서 종목별 카드를 렌더링하는 부분(symbol, sentiment 표시하는 카드)을 찾아, 종목명 옆에 setup_quality 배지 추가:

종목 헤더(`{sym.symbol}` 표시 부분) 찾아서:
```tsx
{/* 기존 symbol 표시 */}
<span style={{ fontWeight: 700, ... }}>{sym.symbol}</span>

{/* 아래 추가 */}
{briefBySymbol[sym.symbol] && (() => {
  const sq = briefBySymbol[sym.symbol].setup_quality;
  const meta = SETUP_QUALITY_META[sq] ?? SETUP_QUALITY_META['B'];
  return (
    <span className={`badge ${meta.color}`} style={{ fontSize: 10, marginLeft: 4 }}>
      {meta.label}
    </span>
  );
})()}
```

> **참고:** SentimentBoard의 종목 카드 구조를 확인한 후 정확한 삽입 위치를 찾을 것. `{sym.symbol}` 또는 `{item.symbol}` 패턴으로 grep해서 위치 파악.

- [ ] **Step 4: 빌드 확인**

```bash
cd /Users/jerry/dev/sniperboard/frontend
npm run build 2>&1 | tail -10
```

Expected: `✓ Compiled successfully`

- [ ] **Step 5: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/components/boards/SentimentBoard.tsx
git commit -m "feat: SentimentBoard — setup_quality 배지 추가"
```

---

## Task 12: Mac Mini cron 설정 업데이트

**Files:**
- Mac Mini crontab (문서화 목적)

- [ ] **Step 1: crontab 확인 및 추가**

Mac Mini에서 실행:
```bash
crontab -l
```

기존 sentiment cron 줄 아래에 추가:
```bash
# AI Daily Brief (pre_open + post_close)
30 6 * * * cd /path/to/market-sentiment-data && python collect/collect_brief.py >> /tmp/collect_brief.log 2>&1
30 22 * * * cd /path/to/market-sentiment-data && python collect/collect_brief.py >> /tmp/collect_brief.log 2>&1

# Earnings Intelligence (pre_open only, 일 1회)
30 6 * * * cd /path/to/market-sentiment-data && python collect/collect_earnings.py >> /tmp/collect_earnings.log 2>&1
```

`/path/to/market-sentiment-data`는 실제 절대경로로 교체.

- [ ] **Step 2: 수동 실행으로 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data
python collect/collect_brief.py
python collect/collect_earnings.py
cat brief/latest.json | python3 -m json.tool | head -20
cat earnings/latest.json | python3 -m json.tool | head -20
```

---

## Task 13: 전체 통합 확인

- [ ] **Step 1: Docker Compose 재빌드 (환경변수 반영)**

```bash
cd /Users/jerry/dev/sniperboard
docker-compose down && docker-compose up -d --build
```

- [ ] **Step 2: API 엔드포인트 확인**

```bash
curl -s http://localhost:5001/api/brief | python3 -m json.tool | head -30
curl -s http://localhost:5001/api/earnings | python3 -m json.tool | head -30
```

Expected:
- brief 데이터가 있으면: `"available": true, "data": {...}`
- 아직 없으면: `"available": false, "error": "Brief 데이터가 아직 생성되지 않았습니다."`

- [ ] **Step 3: 프론트엔드 UI 확인**

브라우저에서 `http://localhost:4000` 열기:
1. OverviewBoard → AI Brief 카드: brief 데이터 또는 fallback regime 텍스트 표시 확인
2. OverviewBoard → Earnings Calendar 카드: upcoming 어닝 또는 "없음" 표시 확인
3. DailyBoard → 어닝 있는 종목 선택 시 배너 표시 확인
4. SentimentBoard → 각 종목 setup_quality 배지 표시 확인

- [ ] **Step 4: 전체 테스트 실행**

```bash
cd /Users/jerry/dev/sniperboard/backend
python -m pytest tests/ -v
```

Expected: 모든 테스트 PASSED

---

## 자가 검토 메모

**Spec 커버리지:**
- ✅ collect_brief.py — Task 2
- ✅ collect_earnings.py — Task 3
- ✅ brief_service, earnings_service — Task 5
- ✅ /brief, /earnings 엔드포인트 — Task 6
- ✅ docker-compose 환경변수 — Task 6
- ✅ TypeScript 타입 — Task 7
- ✅ useBrief, useEarnings 훅 — Task 8
- ✅ OverviewBoard AI Brief 카드 — Task 9
- ✅ OverviewBoard Earnings Calendar 카드 — Task 9
- ✅ DailyBoard 어닝 배너 — Task 10
- ✅ SentimentBoard setup_quality 배지 — Task 11
- ✅ Mac Mini cron 설정 — Task 12

**타입 일관성:**
- `SymbolBrief`, `UpcomingEarning`, `SETUP_QUALITY_META`, `EARNINGS_RISK_META` — Task 7에서 정의, Task 9/10/11에서 동일 이름으로 사용 ✅
- `BriefData`, `EarningsData` — Task 7 정의 → Task 8 훅 반환 타입 ✅
- `fetch_brief()`, `fetch_earnings()` — Task 5 정의 → Task 6 endpoints에서 동일 이름 import ✅

**면책 문구:** Task 9 Step 3에서 "AI 의견 — 매매 신호 아님" 텍스트 포함 ✅
