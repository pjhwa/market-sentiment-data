# Watchlist Tier 선정 결과 — 2026-06-01

> **목적:** X 멘션 볼륨 프로브(169개 후보) 결과를 바탕으로 센티먼트 수집 대상 20개 종목을 Tier1/Tier2로 선정한다.

- **프로브 실행:** `2026-06-01T10:03:30Z`
- **스캔 종목:** 169개 (실패 0개)
- **결과 파일:** `sentiment/probe/latest.json`

---

## 최종 선정 결과

### TIER1 — 10개 (개별 심층 분석 · 하루 2회)

> probe_score 상위 + `mention_volume` surging/elevated + `consistency` steady 우선 선정.
> 신호가 일관적으로 풍부해 매일 2회 수집 가치가 있는 종목.

| 심볼 | 회사 | 점수 | 볼륨 | 일관성 | 선정 근거 |
|------|------|-----:|------|--------|-----------|
| **RKLB** | Rocket Lab | 100.0 | surging | steady | 최고점. 수주잔고·인수·운영성과로 X 상시 활성 |
| **TSM** | TSMC | 95.0 | surging | steady | AI 파운드리 리더십, 공격적 팹 확장 지속 논의 |
| **CEG** | Constellation Energy | 95.0 | surging | steady | AI 데이터센터 핵전력 공급 계약 — 핵에너지 대표주 |
| **VST** | Vistra Energy | 95.0 | surging | steady | 핵자산·하이퍼스케일러 PPA·주가 상승 지속 화제 |
| **PLTR** | Palantir | 92.0 | surging | event_driven | Jensen Huang AI 에이전트 강조 + 기존 WATCHLIST |
| **META** | Meta Platforms | 90.0 | elevated | steady | AI 광고 효율 복합기업 재평가 — 기존 WATCHLIST |
| **APP** | AppLovin | 90.0 | elevated | steady | 소프트웨어 마진·AI 광고 플랫폼 성장 기대 지속 |
| **NVO** | Novo Nordisk | 90.0 | elevated | steady | Medicare 접근 확대·GLP-1 시장 주도권 논의 활발 |
| **NVDA** | Nvidia | 87.0 | surging | event_driven | AI PC·RTX Spark·로보틱스 — AI 핵심주 기존 WATCHLIST |
| **TSLA** | Tesla | 83.0 | elevated | steady | 로보택시·FSD v14·자율주행 촉매 — 기존 WATCHLIST |

```python
TIER1_WATCHLIST = [
    ("RKLB",  "Rocket Lab"),
    ("TSM",   "TSMC"),
    ("CEG",   "Constellation Energy"),
    ("VST",   "Vistra Energy"),
    ("PLTR",  "Palantir"),
    ("META",  "Meta Platforms"),
    ("APP",   "AppLovin"),
    ("NVO",   "Novo Nordisk"),
    ("NVDA",  "Nvidia"),
    ("TSLA",  "Tesla"),
]
```

---

### TIER2 — 10개 (배치 묶음 분석 · 하루 1회 post_close)

> probe_score 고점이지만 `consistency` event_driven이거나 볼륨이 약간 낮은 종목.
> 5개씩 묶어 단일 Grok 호출로 처리. 비용 절감 효과 큼.

| 심볼 | 회사 | 점수 | 볼륨 | 일관성 | 선정 근거 |
|------|------|-----:|------|--------|-----------|
| **ALAB** | Astera Labs | 92.0 | surging | event_driven | CXL 연결성·AI 인프라 이벤트 때 급등 |
| **CRWD** | CrowdStrike | 92.0 | surging | event_driven | Jensen Huang 공개 지지 이후 급등 — 기존 WATCHLIST |
| **OKLO** | Oklo | 92.0 | surging | event_driven | DOE 플루토늄 연료 프로그램 선정 + SMR 테마 |
| **MU** | Micron | 92.0 | surging | event_driven | AI 메모리 수요·실적 — 기존 WATCHLIST |
| **ANET** | Arista Networks | 90.0 | elevated | steady | AI 인프라 수요·실적 후 지속 논의 |
| **SOFI** | SoFi | 90.0 | elevated | steady | 리테일 강세 확신·스테이블코인 진출 — 기존 WATCHLIST |
| **VRT** | Vertiv Holdings | 85.0 | elevated | steady | 데이터센터 냉각·전력 인프라 수요 지속 |
| **CLSK** | CleanSpark | 85.0 | elevated | steady | AI/HPC 데이터센터 전환 + BTC 채굴 테마 |
| **IONQ** | IonQ | 81.0 | elevated | steady | 인수·수주잔고·정부 계약 강세 — 기존 WATCHLIST |
| **MARA** | Marathon Digital | 78.0 | elevated | event_driven | 전력 파이프라인 자산·AI 전략 전환 논의 |

```python
TIER2_WATCHLIST = [
    ("ALAB",  "Astera Labs"),
    ("CRWD",  "CrowdStrike"),
    ("OKLO",  "Oklo"),
    ("MU",    "Micron"),
    ("ANET",  "Arista Networks"),
    ("SOFI",  "SoFi"),
    ("VRT",   "Vertiv Holdings"),
    ("CLSK",  "CleanSpark"),
    ("IONQ",  "IonQ"),
    ("MARA",  "Marathon Digital Holdings"),
]
```

---

## 기존 WATCHLIST 변경 사항

기존 7개 종목 중 3개가 이번 20개 목록에서 제외되었다.

| 심볼 | 점수 | 제외 이유 | 비고 |
|------|-----:|-----------|------|
| **AAPL** | 56.1 | `normal` 볼륨, 신뢰도 `med` — X 토론 활발하지 않음 | 메가캡이나 현재 X 신호 빈약 |
| **AMZN** | 75.0 | `normal` 볼륨 — 신호 존재하나 신규 고점 종목에 밀림 | 상위 20위권 밖 |
| **GOOGL** | 43.4 | 최하위권. `normal` + `event_driven` — 도쿄 매장 오픈 외 토론 부재 | 오늘 X 활동 극히 낮음 |

> **주의:** AAPL·AMZN·GOOGL 제외는 오늘(2026-06-01) 하루 X 스냅샷 기준이다. 실적 발표·제품 이벤트·뉴스 발생 시 점수가 급등할 수 있다. 월 1회 재프로브로 재평가 권장.

---

## 점수 분포 요약

| 구간 | 종목 수 | 대표 종목 |
|------|---------|-----------|
| 90~100 (최상위) | 18개 | RKLB, TSM, CEG, VST, PLTR, ALAB, CRWD, OKLO, MU, META, APP, NVO, ANET, DUOL, LYFT, RBLX, SOFI, QBTS |
| 80~89 | 20개 | NVDA, TSLA, AFRM, BBAI, DELL, CLSK, CORZ, VRT, IONQ 등 |
| 70~79 | 17개 | MARA, UPST, CRM, LLY, AMZN, GME, MSTR, ENPH 등 |
| 60~69 | 14개 | MSFT, AAPL, ORCL, SNAP, NKE, LYFT 등 |
| 60 미만 | 100개 | GOOGL, JNJ, KO, PG 등 전통 블루칩 다수 |

**주요 발견:**
- **핵에너지·전력 인프라** (CEG, VST, VRT, OKLO) 가 AI 테마와 결합해 최상위권 진입
- **우주** (RKLB) 가 전 종목 1위 — 수주잔고와 사업 성과로 X 상시 활성화
- **전통 블루칩** (JNJ, KO, PG, GOOGL) 은 X 토론 빈도 낮아 하위권
- **기존 WATCHLIST** 중 NVDA·PLTR·META·TSLA는 충분히 경쟁력 있음

---

## 비용 추정 (선정 후 월간)

| 구분 | 방식 | 일 호출 수 | 월 비용 추정 |
|------|------|-----------|-------------|
| TIER1 10개 | 개별 × 2회/일 | 20회 | ~$82 |
| TIER2 10개 | 배치(5개) × 1회/일 | 2회 | ~$8 |
| MARKET | 개별 × 2회/일 | 2회 | ~$8 |
| **합계** | | **24회** | **~$98/월** |
| xAI 무료크레딧 | — | — | -$175 (초기 상쇄) |

기존 7개 개별 수집 대비 **종목 3배 확장에 비용 동결** 달성.

---

## collect_sentiment.py 수정 방법

`sentinel/probe/latest.json`의 코드 블록을 복사해 `collect_sentiment.py`에 적용:

```python
# 기존 WATCHLIST 를 아래 두 목록으로 교체

TIER1_WATCHLIST = [
    ("RKLB",  "Rocket Lab"),
    ("TSM",   "TSMC"),
    ("CEG",   "Constellation Energy"),
    ("VST",   "Vistra Energy"),
    ("PLTR",  "Palantir"),
    ("META",  "Meta Platforms"),
    ("APP",   "AppLovin"),
    ("NVO",   "Novo Nordisk"),
    ("NVDA",  "Nvidia"),
    ("TSLA",  "Tesla"),
]

TIER2_WATCHLIST = [
    ("ALAB",  "Astera Labs"),
    ("CRWD",  "CrowdStrike"),
    ("OKLO",  "Oklo"),
    ("MU",    "Micron"),
    ("ANET",  "Arista Networks"),
    ("SOFI",  "SoFi"),
    ("VRT",   "Vertiv Holdings"),
    ("CLSK",  "CleanSpark"),
    ("IONQ",  "IonQ"),
    ("MARA",  "Marathon Digital Holdings"),
]
```

이후 `main()` 루프를 Tier1/Tier2 분리 구조로 변경해야 한다 (별도 구현 태스크).

---

## 재선정 주기

- **월 1회** 프로브 재실행 권장
- `PROBE_BATCH_SIZE=3 HERMES_TIMEOUT=240 python3 -m collect.probe_mention_volume`
- 재실행 후 `sentiment/probe/latest.json` 결과를 본 문서 형식으로 업데이트
