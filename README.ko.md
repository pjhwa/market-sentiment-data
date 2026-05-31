> English docs: [README.md](./README.md)

# market-sentiment-data

SniperBoard 소셜 심리 파이프라인의 **계층 2 — 공용 데이터 저장소**입니다.

맥미니 cron이 Hermes + Grok을 통해 수집한 소셜 심리 데이터를 표준 JSON 형식으로 보관합니다.
SniperBoard를 비롯한 모든 소비 프로그램은 이 리포의 raw URL만 알면 됩니다.

---

## 리포 구조

```
market-sentiment-data/
├── README.md              # 이 문서
├── schema.json            # 데이터 계약 (JSON Schema draft-07, v1.4)
├── latest.json            # 가장 최근 스냅샷 — 소비측이 주로 읽는 파일
├── history/
│   ├── 2026-05-21_pre_open.json    # 당일 pre_open 슬롯 (13:00 UTC)
│   ├── 2026-05-21_post_close.json  # 당일 post_close 슬롯 (21:00 UTC)
│   └── ...
├── brief/
│   ├── latest.json             # AI Daily Brief 최신 스냅샷
│   └── history/               # YYYY-MM-DD_<slot>.json
└── earnings/
    ├── latest.json             # 어닝 인텔리전스 최신
    └── history/               # YYYY-MM-DD.json
```

- **`latest.json`**: cron 실행마다 덮어쓰기. 항상 최신 상태.
- **`history/YYYY-MM-DD_pre_open.json`**: 미국 장 개장 전(13:00 UTC) 스냅샷.
- **`history/YYYY-MM-DD_post_close.json`**: 미국 장 마감 후(21:00 UTC) 스냅샷. `intraday_shift` 포함.
- **`history/YYYY-MM-DD.json`**: v1.1 이전 구형 파일. 소비측 폴백으로 보존.

> **schema_version 이력:** 1.0 기본 | 1.1 price_context+divergence | 1.2 slot+intraday_shift | 1.3 composite_score | **1.4 top_news 추가 (현재)**

---

## 다른 프로그램에서 소비하는 법

### Public 리포인 경우 (인증 불필요)

```bash
# 최신 스냅샷 가져오기
curl https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json

# 특정 날짜 히스토리
curl https://raw.githubusercontent.com/<user>/market-sentiment-data/main/history/2026-05-21.json
```

### Private 리포인 경우 (PAT 토큰 필요)

```bash
# 환경변수에 토큰 보관
export SENTIMENT_DATA_TOKEN="github_pat_xxxx"

# latest.json 가져오기
curl -H "Authorization: token $SENTIMENT_DATA_TOKEN" \
     https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json

# Python (requests)
import os, requests
resp = requests.get(
    "https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json",
    headers={"Authorization": f"token {os.environ['SENTIMENT_DATA_TOKEN']}"},
    timeout=10
)
data = resp.json()
```

> **토큰을 코드나 이미지에 굽지 마세요.** docker-compose 환경변수 또는 cron 환경으로 주입하세요.

---

## 데이터 스키마 요약

`schema.json` 참고. 핵심 enum:

| 필드 | 허용값 |
|------|--------|
| `sentiment` | `very_fearful` `fearful` `neutral` `optimistic` `euphoric` |
| `sentiment_score` | `-2` `-1` `0` `+1` `+2` (sentiment에서 결정론적 파생) |
| `trend_vs_yesterday` | `cooling` `stable` `heating` |
| `mention_volume` | `low` `normal` `elevated` `surging` |
| `bot_suspected` | `yes` `no` `unclear` |
| `confidence` | `high` `med` `low` |
| `extreme_flag` (market만) | `none` `extreme_fear` `extreme_greed` |
| `slot` | `pre_open` `post_close` |
| `intraday_shift` | `cooling` `stable` `heating` `null` |
| `top_news.headline` | 문자열 — 가장 많이 언급된 뉴스/포스트 원문 제목 |
| `top_news.summary` | 문자열 — 1-2문장 한국어 요약 |
| `top_news.source` | 문자열 — 출처 (Bloomberg, @username 등) |

---

## 수집 주기

| 데이터 | 스크립트 | cron (UTC) | 슬롯 |
|--------|----------|-----------|------|
| 소셜 심리 | `collect_sentiment.py` | `00 6,22 * * *` | pre_open / post_close |
| AI Daily Brief | `collect/collect_brief.py` | `30 6,22 * * *` | pre_open / post_close |
| Earnings Intelligence | `collect/collect_earnings.py` | `30 6 * * *` | 하루 1회 |

- 소셜 심리 수집(06:00/22:00 UTC) 완료 30분 후 Brief/Earnings 수집
- Brief는 소셜 심리 + 기술 지표를 결합하여 Grok으로 분석
- Earnings는 yfinance `.calendar` + `.earnings_history`로 원시 데이터 수집 후 Grok 해석

---

## AI Daily Brief 스키마 (`brief/latest.json`)

```json
{
  "generated_at": "2026-05-24T04:46:56Z",
  "schema_version": "1.0",
  "slot": "pre_open",
  "market_brief": {
    "summary": "...",
    "tone": "bullish | cautious | bearish | neutral",
    "key_themes": ["...", "..."],
    "watch_points": "..."
  },
  "symbol_briefs": [
    {
      "symbol": "TSLA",
      "setup_quality": "A+ | A | B | C | D",
      "brief": "...",
      "key_risk": "...",
      "key_opportunity": "...",
      "action_bias": "buy | hold | watch | avoid"
    }
  ]
}
```

---

## Earnings Intelligence 스키마 (`earnings/latest.json`)

```json
{
  "generated_at": "2026-05-24T04:46:27Z",
  "schema_version": "1.0",
  "upcoming_earnings": [
    {
      "symbol": "NVDA",
      "earnings_date": "2026-05-28",
      "days_until": 4,
      "eps_estimate": 0.89,
      "revenue_estimate_b": 43.1,
      "historical_beat_rate": 0.92,
      "ai_summary": "...",
      "risk_level": "high | med | low",
      "action_note": "..."
    }
  ],
  "recent_results": [
    {
      "symbol": "TSLA",
      "report_date": "2026-04-22",
      "eps_actual": 0.27,
      "eps_estimate": 0.45,
      "surprise_pct": -40.0,
      "ai_reaction": "..."
    }
  ]
}
```

---

## SniperBoard에서 history/ 활용 방식

SniperBoard의 `GET /api/sentiment/history?symbol=TSLA&days=7` 엔드포인트는 이 리포의 `history/` 폴더에서 N일치 파일을 순회하여 심리 추이 포인트를 반환합니다.

- SentimentBoard에서 종목 카드를 클릭하면 주가 라인 + 심리 composite_score 오버레이 차트가 펼쳐집니다.
- **7일 / 30일 토글**: 현재 history는 ~7일치. 30일치가 누적되면 심리 고점/저점과 주가 반전 패턴 분석이 가능해집니다.
- 슬롯 마커: ▲ pre_open (장 전), ● post_close (장 후)
- 파일 명명 규칙 유지가 중요: `YYYY-MM-DD_pre_open.json` / `YYYY-MM-DD_post_close.json`
  - 구형 `YYYY-MM-DD.json`은 폴백으로 처리됨 (pre_open 슬롯으로 간주)

---

## 관련 프로젝트

- **[SniperBoard](https://github.com/pjhwa/sniperboard)** — 이 데이터를 소비하는 트레이딩 대시보드
- 수집 스크립트: `collect_sentiment.py`, `collect/collect_brief.py`, `collect/collect_earnings.py` (맥미니 cron으로 실행)
