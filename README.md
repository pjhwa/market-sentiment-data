# market-sentiment-data

SniperBoard 소셜 심리 파이프라인의 **계층 2 — 공용 데이터 저장소**입니다.

맥미니 cron이 Hermes + Grok을 통해 수집한 소셜 심리 데이터를 표준 JSON 형식으로 보관합니다.
SniperBoard를 비롯한 모든 소비 프로그램은 이 리포의 raw URL만 알면 됩니다.

---

## 리포 구조

```
market-sentiment-data/
├── README.md              # 이 문서
├── schema.json            # 데이터 계약 (JSON Schema draft-07)
├── latest.json            # 가장 최근 스냅샷 — 소비측이 주로 읽는 파일
└── history/
    ├── 2026-05-21.json    # 일별 스냅샷 (UTC 기준 날짜)
    └── ...
```

- **`latest.json`**: cron 실행마다 덮어쓰기. 항상 최신 상태.
- **`history/YYYY-MM-DD.json`**: 일별 아카이브. "어제 대비 변화" 계산에 활용.
- **`schema.json`**: 모든 필드의 enum·타입을 정의. 수집·소비 양쪽에서 검증에 재사용.

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

---

## 수집 주기

하루 1~2회 (KST 06:30, 22:30). 미국장 마감 후 + 장중 1회.
보조 지표이므로 분 단위 폴링은 불필요합니다.

---

## 관련 프로젝트

- **[SniperBoard](https://github.com/pjhwa/sniperboard)** — 이 데이터를 소비하는 트레이딩 대시보드
- 수집 스크립트: `collect_sentiment.py` (맥미니 cron으로 실행)
