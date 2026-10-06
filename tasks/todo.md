# Grok(hermes) 토큰 최적화 — 데이터 품질 유지가 최우선

## 실측 (hermes insights + ~/.hermes/state.db, 최근 30일, 2026-10-03)
- 세션 1,331개 / 약 3,568만 토큰(전부 grok-4.3). 툴 호출의 99%가 x_search.
- 수집기별 (input+output): sentiment Tier1 개별 73% (896세션, 세션당 ~20K) / macro 8% / earnings 8% / MARKET 5% / Tier2 배치 2%
- Tier1은 하루 24회가 정상인데 30일간 896회 = 하루 ~30회 → 재시도 낭비 ~20%
- 세션당 system_prompt = 약 22K자: 스킬 인덱스 ~11K + 페르소나/규칙 ~6K + cwd의 CLAUDE.md 자동주입 ~4K. 수집 작업과 무관하며 LLM 턴마다 재전송됨.
- 기본 toolset 24개 활성 → 툴 스키마도 매 요청에 포함 (수집은 x_search만 필요).

## 원칙 (품질 게이트)
1. 모든 변경은 A/B로 검증: 같은 종목에 baseline vs variant 반복 실행 → enum 필드(sentiment/trend/mention_volume/confidence) 일치율이 baseline끼리의 일치율(노이즈 바닥) 이하로 떨어지면 채택 금지.
2. 오염 방지선(방향 단어 가드), 스키마, validator는 절대 건드리지 않는다.
3. 변경은 env/플래그로 on/off 가능하게 하고, 한 번에 한 가지씩 롤아웃.
4. 롤아웃 후 `hermes insights` 재측정 + sentiment/history 비교로 사후 검증.

## 단계
- [ ] **P1. 호출 오버헤드 제거 (프롬프트 불변 → 품질 영향 최소)**
  - [x] 코드: `HERMES_LEAN=1` opt-in 플래그 추가 (기본 OFF, 테스트 3건, 전체 47 통과)
  - [x] A/B (2026-10-06, NVDA/TSLA/MU): lean은 호출당 토큰 25.9K→8.3K(-68%, 중앙값 -59%), 파싱 실패 0, sentiment 지배값 동일, trend/confidence 변동은 baseline 노이즈 수준. 표본 작음(변형당 ~12) → 단계적 롤아웃 필요
  - [x] 롤아웃 1: sentiment 크론에만 `HERMES_LEAN=1` 추가 (2026-10-06, 백업: crontab.backup.20261006 — 롤백은 해당 env 제거) → 며칠간 history 비교 후 다음 수집기로 확대
  - [x] 실데이터 검증 (2026-10-06, 샌드박스 clone·원격 제거, 운영 무영향): sentiment 전체(14세션 113K토큰, 22종목 중 21개 필드값이 최근 14슬롯 이력 범위, top_news Tier1 9/12·Tier2 5/10 = 이력 범위, 파싱 실패 0) / macro 7.8K·earnings 9.3K·brief 13.3K·briefing 41.6K 토큰, 전부 validator·schema 통과, 필드 구조 100% 동일 / verify_briefing 오류1·경고4 = 정상기간(9-30,10-01)과 동일
  - [x] 롤아웃 2: brief/macro/earnings/morning_briefing 크론에도 `HERMES_LEAN=1` (백업: crontab.backup2.20261006). 코드 기본값은 OFF 유지(크론 env로만 제어)
  - [x] morning briefing 1단계는 web 툴셋 유지 (호출에서 명시 지정 → lean이 덮어쓰지 않음)
- [x] **P2. 재시도 낭비 축소 — 변경 없음으로 종결 (2026-10-06 실측)**
  - 30일 834개 호출 클러스터 중 651개 1회 성공, 177개 3회 실패. 비최종 시도 토큰 합 51.9만/1,843만 = 2.8%이고 대부분(50만)은 이번 세션의 A/B 실험(같은 종목 반복)이라 운영 낭비는 <1%.
  - 실패 시도의 365/393은 0토큰 즉시 실패(크레딧·인증 장애 구간: 9-26~9-28, 10-03~10-05에 sentiment 슬롯 통째로 빔) → 재시도해도 비용 0, 구제된 건은 3건뿐.
  - brief 교정 재시도(전체 프롬프트 재전송)도 월 수십 회 × ~13K = <1%. 재시도를 줄이면 데이터 완결성 리스크만 커지므로 유지.
- [x] **P3. 검색 불필요 호출의 툴셋 제거 — 변경 없음으로 종결 (2026-10-06 실측)**
  - 측정: `--ignore-rules`만=입력 15.7K토큰(스킬 인덱스가 기본 툴셋에 딸려옴) / `-t x_search`=2.4K / `-t clarify`(최소)=1.4K. → 오버헤드 제거의 본체는 `-t` 제한이고 이미 HERMES_LEAN에 반영됨.
  - x_search를 더 빼도 호출당 ~1K, brief+macro 하루 4회 ≈ 4K토큰(전체 ~1%). earnings는 30일 150세션 중 8회 실제 x_search 사용 → 제거 시 품질 리스크. 실익 < 리스크.
- [ ] **P4. (보류·별도 승인) Tier1 소배치화, 주말 슬롯 축소 — 품질 리스크가 있어 P1~P3 결과 본 뒤 결정**

## 검증 결과 / 리뷰
- **2026-10-03 블로커**: Grok 크레딧 소진 (403 `personal-team-blocked:spending-limit`). 06시 실행분부터 brief/macro/briefing 전부 실패, 마지막 정상 데이터는 sentiment 05:51. `hermes -z`는 403에도 rc=0 + 빈 출력이라 수집기에는 '빈 응답'으로 보임.
- 첫 A/B 시도(15회)는 403으로 무효 — 크레딧 복구 후 재실행 필요 (스크립트: scratchpad/ab1.py, 기준: baseline끼리의 enum 일치율).
- 참고: 빈 응답은 Claude fallback을 타지 않음(rc=0). X 접근이 불가능한 Claude로 sentiment를 대체하면 지어낸 데이터가 되므로 현 동작 유지 권장.

- 롤아웃 후 모니터링: 며칠 뒤 `hermes insights --days 3`로 일 토큰 비교, sentiment/history 분포·top_news 비율·verify 오류 수 확인. 이상 시 크론에서 HERMES_LEAN=1 제거(롤백).

## 롤아웃 후 잔여 비용 구조 (추정)
- 일 ~330K토큰 중 sentiment ≈ 66%(Tier1 종목별 호출 24회 + 배치/MARKET). 호출당 ~8K = 고정 ~2.4K + x_search 결과 ~5K + 출력.
- 추가 절감 후보는 P4(Tier1 소배치화 4종목×3호출)뿐: 고정분 ~2K×9호출×2슬롯 ≈ 일 ~36K(≈10%). 종목별 검색 품질 저하 리스크가 있어 별도 A/B 후 결정.
- 장애 이력: sentiment 슬롯이 9-26~9-28, 10-03~10-05에 비어 있음(크레딧/인증). `hermes -z`는 403에도 rc=0+빈 출력이라 수집기가 '빈 응답'으로만 인식 → 모니터링에서 감지 필요.

# 장애 알림 (Grok 가용성 모니터링) — 2026-10-06

## 문제
- 장애(9-26~9-28, 10-03~10-05)가 슬롯 6개 분량 지속됐는데 즉시 알림이 없었음. 기존 health_check는 신선도 한도가 25h라 하루 뒤에야 감지하고, 원인(크레딧/인증)을 모르며, 매번 무관한 FAIL 5~7건(docker 없음 등)이 섞여 알림이 만성 소음.
- `hermes -z`는 403에도 rc=0 + 빈 출력 → 수집기는 '빈 응답'만 인식.
- 메신저(telegram 등)는 hermes에 실제로 설정돼 있지 않음 → macOS 알림 + 로그 + 외부 명령 훅(`GROK_ALERT_CMD`)으로 구성.

## 설계
- `collect/grok_health.py`: 빈 응답 재시도 소진 시 `hermes chat -q` 로 원인 진단(403 spending-limit→credits_exhausted / 401→auth / 429→rate_limited / 네트워크 / unknown). 정상일 때 진단 비용 ~1.4K토큰, 장애 시 0.
- 상태 파일 `monitor/grok_status.json` (gitignore). 알림은 **상태 전환(ok→down, 원인 변경, 복구)과 6h 재알림**에만 → 소음 없음. 진단은 20분에 1회로 제한.
- 알림 채널: macOS 알림 + `monitor/grok_alerts.log` + `GROK_ALERT_CMD`(stdin으로 메시지 전달, 텔레그램/ntfy 등 연결용).
- `grok_utils`: 최종 실패(빈 응답)→report_failure, 성공→report_success. 헬스 코드의 예외는 수집을 절대 막지 않음(try/except). 테스트에서는 비활성(conftest).
- `health_check.py`: grok_status가 down이면 원인과 함께 FAIL.

## 작업
- [x] 분류기/상태 전환 로직 + 단위 테스트 (실제 403 출력 샘플로)
- [x] grok_utils 훅 + conftest 비활성
- [x] health_check 연동
- [x] 실검증: 정상 진단 1회(토큰 측정), 장애 시뮬레이션(HERMES_CMD를 가짜 스크립트로 교체)으로 알림·복구 확인

### 결과
- 신규 테스트 15건 포함 248 통과(기존 실패 2건은 변경 전부터 실패, 무관).
- E2E(가짜 hermes: -z 빈 출력 + chat 403): 연속 실패 호출 3회 → 알림 1회(원인 credits_exhausted), 복구 시 알림 1회. 실제 hermes 정상 호출에서는 오탐·상태파일 생성 없음.
- 한계: cron 환경에서 macOS 알림(osascript)이 화면에 뜨는지는 환경 의존 → 확실한 채널이 필요하면 `GROK_ALERT_CMD`에 텔레그램/ntfy 등 연결 권장(현재 hermes에는 메신저 미설정). 알림 로그 `monitor/grok_alerts.log`는 항상 기록됨.
- 기존 health_check의 무관한 FAIL(docker 없음, API 타임아웃, SignalDB 등)은 이번 범위 밖 — 소음 정리는 별도 과제.
