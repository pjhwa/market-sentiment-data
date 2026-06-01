# Probe Mention Volume — 설계 문서

> **목적:** X(트위터) 멘션 볼륨 프로브를 통해 다종목 센티먼트 수집 시 Tier1/Tier2 종목을 데이터 기반으로 선별한다.

---

## 1. 배경 및 목적

SniperBoard 센티먼트 수집기(`collect_sentiment.py`)는 현재 7개 종목을 개별 Grok 호출로 수집한다. 수집 범위를 20개로 확장할 때 비용 통제를 위해 **티어링(Tiering) + 배치 프롬프트** 전략을 적용한다.

- **Tier1** (약 10개): 개별 심층 분석 · 하루 2회 (pre_open + post_close)
- **Tier2** (약 10개): 배치 묶음 분석 · 하루 1회 (post_close만)

Tier 분류의 핵심 기준은 **X 멘션 볼륨의 풍부도와 신호 품질**이다. `probe_mention_volume.py`는 후보 종목 169개를 Grok으로 사전 스캔하여 이 분류를 데이터 기반으로 결정한다.

---

## 2. 파일 위치

```
collect/probe_mention_volume.py   # 프로브 스크립트 (1회성 실행)
sentiment/probe/
  latest.json                     # 최신 프로브 결과 (항상 덮어씀)
  YYYY-MM-DD_HHmm.json            # 실행별 누적 보관
```

---

## 3. 후보 종목 구성 (169개)

| 차수 | 종목 수 | 구성 |
|------|---------|------|
| 1차 | 47개 | 기존 WATCHLIST 7개 + 메가캡·AI반도체·크립토·EV·양자·클라우드·소비재 |
| 2차 | 100개 | 상위 시총 누락분(UNH·XOM·JNJ 등) + 섹터 확장(제약·에너지·방산·금융 등) |
| 3차 | 22개 | 2025~2026 신규 테마 (핵에너지·BTC채굴·GLP-1·AI인프라 등) |

섹터 분류 (25개 섹터):

```
기존 WATCHLIST / 메가캡 Tech / AI·반도체 / 크립토·핀테크 / EV·자동차
양자컴퓨팅 / 클라우드·SaaS / 소비재·엔터 / 제약·바이오 / 클린에너지
우주·방산 / 금융 / AI소프트웨어 / 반도체(추가) / 커뮤니케이션·SaaS
소비재 / 게임·모빌리티 / 밈주·기타 / 상위시총Top15~25 / 상위시총Top25~40
통신 / 반도체·하드웨어 / 헬스케어 / 소비재·리테일 / 핵에너지·AI전력인프라
비트코인채굴 / AI인프라 / GLP-1·비만치료 / 방산테크 / 중국테크
```

---

## 4. 프롬프트 설계 (7개 필드)

Grok에게 각 종목의 X 토론 현황을 7개 차원으로 측정하도록 요청한다.

| 필드 | 열거값 | 의미 |
|------|--------|------|
| `mention_volume` | low / normal / elevated / surging | 현재 언급 빈도 (자기 기준선 대비) |
| `consistency` | sporadic / event_driven / steady | 토론의 일관성 (매일 활성 vs 뉴스 때만) |
| `retail_dominance` | low / med / high | 개인투자자 토론 비중 |
| `sentiment_clarity` | noisy / mixed / clear | 심리 신호 추출 용이성 |
| `bot_ratio` | high / med / low | 봇·스팸 계정 비율 |
| `confidence` | low / med / high | 위 평가에 대한 신뢰도 |
| `note` | 문자열 | 현재 토론 동향 한 문장 요약 |

---

## 5. 복합 점수 산출 (probe_score, 0~100)

```
raw_score = mention_volume점수 + consistency점수 + retail점수 + clarity점수 + bot점수
probe_score = raw_score × confidence_multiplier
```

| 필드 | surging/steady/high/clear/low | elevated/event_driven/med/mixed/med | normal/sporadic/low/noisy/high |
|------|-------------------------------|-------------------------------------|-------------------------------|
| mention_volume (max 40) | 40 | 30 | 15 / 5 |
| consistency (max 20) | 20 | 12 | 5 |
| retail_dominance (max 15) | 15 | 10 | 5 |
| sentiment_clarity (max 15) | 15 | 8 | 3 |
| bot_ratio (max 10) | 10 | 6 | 2 |
| **합계 (max 100)** | | | |

confidence 배수: high=1.0 · med=0.85 · low=0.6

**설계 의도:**
- `mention_volume`이 가장 큰 가중치(40%)를 가진다 — 데이터가 없으면 센티먼트 신호 자체가 불가능.
- `consistency`(20%)가 두 번째 — 이벤트 때만 활성인 종목은 일상 수집 가치가 낮다.
- `bot_ratio`와 `sentiment_clarity`로 신호 품질을 보정한다.

---

## 6. 출력 및 저장 구조

### 6-1. 화면 출력

```
# 전체 169개를 probe_score 내림차순으로 정렬한 테이블
# TOP20 이후 Tier1/Tier2 요약 + collect_sentiment.py 복사용 코드 출력
```

### 6-2. JSON 저장 (`latest.json` / `YYYY-MM-DD_HHmm.json`)

```jsonc
{
  "generated_at": "2026-06-01T08:30:00Z",
  "probe_batch_size": 5,
  "provider": "default",
  "total_scanned": 169,
  "failed_symbols": [],

  "score_schema": {
    // 점수 산출 기준 — 재현성 보장을 위해 결과파일에 함께 저장
  },

  "selection": {
    "top20": [ /* probe_score 상위 20개 상세 */ ],
    "tier1_top10": {
      "symbols": ["TSLA", "NVDA", ...],
      "code": "TIER1_WATCHLIST = [\n    ...\n]",  // 바로 붙여넣기용
      "entries": [ /* 상세 데이터 */ ]
    },
    "tier2_top10": {
      "symbols": [...],
      "code": "TIER2_WATCHLIST = [\n    ...\n]",
      "entries": [...]
    }
  },

  "ranked_results": [ /* 전체 169개, probe_score 내림차순 */ ]
}
```

`selection.tier1_top10.code`와 `selection.tier2_top10.code` 값을 `collect_sentiment.py`에 그대로 붙여넣으면 된다.

---

## 7. 실행 방법

```bash
cd ~/tmp/market-sentiment-data

# 기본 실행 (배치 5종목, 타임아웃 240초 권장)
PROBE_BATCH_SIZE=5 HERMES_TIMEOUT=240 python3 -m collect.probe_mention_volume

# 모델 명시
HERMES_PROVIDER=grok-3 PROBE_BATCH_SIZE=5 HERMES_TIMEOUT=240 \
  python3 -m collect.probe_mention_volume
```

**환경변수:**

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `HERMES_CMD` | 자동 탐색 | hermes 바이너리 경로 |
| `HERMES_PROVIDER` | (기본값) | Grok 모델 지정 |
| `HERMES_TIMEOUT` | 120 | 호출 타임아웃(초). 배치당 240 권장 |
| `PROBE_BATCH_SIZE` | 10 | 배치당 종목 수. 5 권장 |

**hermes 자동 탐색 순서:**
1. `HERMES_CMD` 환경변수
2. `PATH`에서 `shutil.which("hermes")`
3. `~/.local/bin/hermes` (Linux pip install)
4. `/opt/homebrew/bin/hermes` (macOS Apple Silicon)
5. `/usr/local/bin/hermes` (macOS Intel / Linux 시스템)

---

## 8. 결과 활용: collect_sentiment.py 수정

프로브 완료 후 `sentiment/probe/latest.json`에서 코드를 복사한다:

```bash
# 결과 확인
cat sentiment/probe/latest.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d['selection']['tier1_top10']['code'])
print()
print(d['selection']['tier2_top10']['code'])
"
```

출력된 `TIER1_WATCHLIST`와 `TIER2_WATCHLIST`를 `collect_sentiment.py` 상단 설정 섹션에 붙여넣고, `main()` 루프를 아래 구조로 변경한다:

```python
# Tier1: 개별 심층 분석 (기존 방식, 하루 2회)
for symbol, company in TIER1_WATCHLIST:
    prompt = build_prompt(symbol, company, ctx)
    ...

# Tier2: 배치 묶음 (post_close만, 5종목씩)
if slot == "post_close":
    for batch in chunks(TIER2_WATCHLIST, 5):
        prompt = build_batch_prompt(batch)
        ...
```

---

## 9. 주기적 재실행 권장

X 멘션 볼륨은 시장 환경에 따라 변한다. **월 1회** 재실행하여 Tier 분류를 재검토한다.

- 평소 조용하다가 이벤트(실적·CEO 발언·규제 뉴스)로 급등하는 종목은 `event_driven`으로 분류됨 → Tier2가 적절
- 지속적으로 `surging/elevated`인 종목만 Tier1으로 유지하는 것이 비용 효율적

---

## 10. 비용 추정 (프로브 1회 실행)

169개 ÷ 5종목/배치 = 34배치 × 약 15 Live Search 소스/배치 × $0.025 = **약 $12.75**

xAI 무료 크레딧($175/월)으로 월 최대 13회 실행 가능. 실제로는 월 1~2회면 충분하다.
