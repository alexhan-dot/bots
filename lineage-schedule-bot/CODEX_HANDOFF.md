# Lineage Schedule Bot — Codex 인수인계 작업 지시서

이 문서만 읽고 이어서 작업할 수 있게 정리함. 사용자(대표)는 한국어, 매니저·플레이어는 영어(필리핀)를 씀.
**비밀값(토큰·키)은 이 저장소에 없음** — 아래 "3. 크레덴셜" 대로 Codex 환경 비밀값으로 받아서 쓴다. 절대 커밋하지 말 것.

---

## 1. 무엇을 하는 봇인가

리니지 클래식 대리육성 운영 봇. FastAPI 한 개 앱이 Cloud Run 에서 돌고, 구글 시트가 DB 역할.

| 채널 | 누가 | 하는 일 |
|---|---|---|
| 텔레그램 봇 **@lineage_schedule_bot** | 대표·영업자 (한국어) | 영업자가 평소 말투로 스케줄 요청 → Claude 가 해석 → 영업자 ✅ → 시트 반영 → 디스코드 카드 → 매니저 확인 → 영업자에게 "확정 완료" |
| 디스코드 서버 **Lineage Ops** | 매니저 (영어, Manager 역할) | 슬래시 명령으로 스케줄·OT·인센티브·페널티 기록, 한 달치 스케줄(/plan), 일별·주별 컨펌 |
| 디스코드 `#shift-reports` | 플레이어 (역할 불필요) | `/shot` 시작·끝 스크린샷 → Claude 비전이 레벨·EXP %·아데나 읽어 바로 기록 |
| 구글 시트 **Lineage Schedule v2** | 모두 | 원장. 보드·급여는 수식으로 자동 |

---

## 2. 현재 상태 (2026-09-25 기준)

- 저장소: `github.com/alexhan-dot/bots`, 브랜치 **`claude/vibrant-albattani-j5mnpo`**, 코드 폴더 `lineage-schedule-bot/`
- **배포된 버전**: v3 직전(설정 스크립트 수정 + /health 추가까지). v4 이후 커밋(아래)은 **아직 배포 안 됨** — 사용자가 Cloud Shell 에서 zip 으로 올리는 중이었음
- 배포 환경: Cloud Shell `~/lineage-schedule-bot` (zip 풀어서 사용, git 아님) + `env.yaml` 이 거기에만 있음
- 첫 배포 때 확인된 것: 텔레그램 `/start` 응답 ✅, 디스코드 `/schedule` ✅, 시트 Settings·Planner 탭 생성 ✅
- 라이브 시트 문제: Client Board 에 **#REF!** — 원인(B5 값이 A5 목록 펼침을 막음) 수정 커밋됨, 배포 필요

### 아직 배포 안 된 주요 변경 (브랜치에는 있음)
1. Today 탭(맨 앞), 지난 시프트 → `Schedule Archive` 매일 이동, Schedule 날짜순 정렬, `All Shifts`(숨김) 합본으로 보드·급여 계산
2. 보드·디스코드에서 OFF·플레이어 없는 파밍 숨김, 보드 B2 기본 = 이번 주
3. **슬롯 규칙**: 8시간 넘는 시간만 8시간씩 나눠 추가 슬롯 (자정 넘는 조각은 다음 날짜)
4. 다음 주 자동 생성(고객 계정은 최근 플레이어 유지) + **일별(07:00)·주별(토 12:00) 컨펌 카드** `#schedule-confirm`, 미컨펌 알림
5. 플레이어 `/shot` 스크린샷 기록 (`Shift Reports` 탭, 끝나면 Schedule KPI·Gold), `/myshifts`
6. TL Board 목록 필터 버그 수정 (I열 → G열 Week)

---

## 3. 크레덴셜

### 3-1. 이미 알고 있는 값 (비밀 아님 — 그대로 사용)
| 항목 | 값 |
|---|---|
| GCP 프로젝트 | `lineage-schedule` (번호 504583925158), 리전 `asia-northeast3` |
| Cloud Run 서비스 | `lineage-schedule-bot` — URL `https://lineage-schedule-bot-jkvuwwpn3q-du.a.run.app` |
| 런타임 서비스계정 | `lineage-bot@lineage-schedule.iam.gserviceaccount.com` (시트 편집자로 공유됨) |
| Firestore | `(default)`, asia-northeast3, Native |
| SHEET_ID | `1fMQDRmGVVtaUR0cVBzzSaowHU38clVuON1PoxGWCT_A` |
| 텔레그램 봇 | `@lineage_schedule_bot`, ADMIN_CHAT_ID `7547886093` |
| 디스코드 App ID / Guild ID | `1553001569379688544` / `1553001202164305960` |
| 디스코드 Public Key | `f061551efbc70eb8c5272eaf3e11441ac7aafcc5025ee7361761c41838bba8b5` |
| Manager 역할 (= 긴급 멘션) | `1553007094138011689` |
| 채널 | #sales-requests `1553007099632554014` · #manager-desk `1553007104397410406` · #bot-log `1553007106473463840` · #schedule-confirm / #shift-reports = `tools/setup_discord.py` 재실행 후 생성·기입 |
| BOT_TZ | `Asia/Seoul` |

### 3-2. 비밀값 — Codex 환경 **Secrets** 에 넣을 것 (값은 사용자의 Cloud Shell `~/lineage-schedule-bot/env.yaml` 에 있음)
| 이름 | 설명 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather 토큰 |
| `TELEGRAM_WEBHOOK_SECRET` | 웹훅 검증 문자열 |
| `DISCORD_BOT_TOKEN` | 디스코드 봇 토큰 (카드 발송·설정 스크립트) |
| `ANTHROPIC_API_KEY` | Claude API |
| `GCP_SA_KEY_B64` | (배포까지 Codex 가 할 때만) 배포용 서비스계정 JSON 키를 base64 로 — 만드는 법 아래 |

사용자가 Cloud Shell 에서 값 확인: `grep -E '^(TELEGRAM_BOT_TOKEN|TELEGRAM_WEBHOOK_SECRET|DISCORD_BOT_TOKEN|ANTHROPIC_API_KEY):' ~/lineage-schedule-bot/env.yaml`
→ 각 값을 Codex 환경 설정(Environment → Secrets)에 붙여넣기. **채팅·커밋·PR 에 붙이지 않는다.**

Codex 에서 env.yaml 만들기 (비밀값은 환경변수로, 나머지는 3-1 값):
```bash
export SHEET_ID=1fMQDRmGVVtaUR0cVBzzSaowHU38clVuON1PoxGWCT_A BOT_TZ=Asia/Seoul CLAUDE_MODEL=claude-sonnet-4-6 \
  ADMIN_CHAT_ID=7547886093 DISCORD_APP_ID=1553001569379688544 DISCORD_GUILD_ID=1553001202164305960 \
  DISCORD_PUBLIC_KEY=f061551efbc70eb8c5272eaf3e11441ac7aafcc5025ee7361761c41838bba8b5 \
  DISCORD_MANAGER_ROLE_IDS=1553007094138011689 DISCORD_URGENT_ROLE_ID=1553007094138011689 \
  DISCORD_REQUESTS_CHANNEL_ID=1553007099632554014 DISCORD_LOG_CHANNEL_ID=1553007106473463840 \
  DISCORD_AI_MODEL=claude-opus-5
# SALES_CHAT_IDS / DISCORD_CONFIRM_CHANNEL_ID / DISCORD_REPORTS_CHANNEL_ID 는 사용자에게 받거나 setup 스크립트 결과로
bash tools/make_env.sh > env.yaml      # env.yaml 은 .gitignore 에 있음
```

### 3-3. (선택) Codex 가 직접 배포하려면 — 배포용 서비스계정 키
사용자가 Cloud Shell 에서 한 번:
```bash
P=lineage-schedule; D=lineage-deployer@$P.iam.gserviceaccount.com
gcloud iam service-accounts create lineage-deployer --display-name "Codex deployer"
for R in roles/run.admin roles/iam.serviceAccountUser roles/cloudbuild.builds.editor roles/artifactregistry.admin \
         roles/storage.admin roles/serviceusage.serviceUsageConsumer roles/logging.viewer; do
  gcloud projects add-iam-policy-binding $P --member serviceAccount:$D --role $R --condition=None -q >/dev/null; done
gcloud iam service-accounts keys create /tmp/deployer.json --iam-account $D && base64 -w0 /tmp/deployer.json; rm /tmp/deployer.json
```
출력된 긴 문자열 → Codex Secret `GCP_SA_KEY_B64`. Codex 에서:
```bash
echo "$GCP_SA_KEY_B64" | base64 -d > /tmp/k.json && gcloud auth activate-service-account --key-file /tmp/k.json \
  && gcloud config set project lineage-schedule && bash deploy.sh
```
(Codex 환경에 gcloud 가 없거나 외부 네트워크가 막혀 있으면 → 코드만 수정·푸시하고, 배포는 사용자가 Cloud Shell 에서 `git pull && bash deploy.sh`)

---

## 4. 코드 구조 (`lineage-schedule-bot/`)
| 파일 | 역할 |
|---|---|
| `main.py` | FastAPI: `/telegram/webhook`, `/discord/interactions`, `/health`, `/confirm` `/answer`(웹훅 폴백), 기동 시 `sheets.ensure_tabs()` → `housekeep()` → 인덱스 워밍 → `confirm.start()` |
| `services/layout.py` | **탭·열·수식 정의 (단일 출처)**. 수식은 봇이 기동 때 USER_ENTERED 로 다시 씀 |
| `services/sheets.py` | gspread. `Schedule` 스냅샷(읽기 1번→쓰기 모아서), `apply(op)`(텔레그램 작업), `rollover`, `housekeep`(보관·정렬), `update_shift_row`(행이 옮겨져도 키로 찾음), `LOCK` |
| `services/shiftutil.py` | 시간 span·8시간 분할 |
| `services/index.py` | 디스코드용 메모리 인덱스 (60초 TTL, 백그라운드 갱신) — 조회·자동완성 1ms |
| `services/manager.py` | 디스코드 매니저 명령 Draft→미리보기→저장 |
| `services/discord_bot.py` | 슬래시 명령·버튼·모달·자동완성 전부 (3초 규칙: 무거운 일은 type 5 후 bg) |
| `services/planner.py` | Planner 탭 / `/plan` 기간 스케줄 |
| `services/confirm.py` | 일별·주별 컨펌 스케줄러(스레드, Firestore 중복 방지) + 카드 |
| `services/reports.py`, `services/vision.py` | `/shot` 스크린샷 → Claude 비전 → Shift Reports |
| `services/parser.py`, `translate.py`, `glossary.py` | 텔레그램 한국어 해석(Claude) + 용어 사전 |
| `services/notify.py` | 디스코드 카드·파일 업로드(봇 토큰) |
| `tools/setup_discord.py` | 새 서버에 역할·채널·명령·고정 안내 생성 + env.yaml 기입 (재실행 안전) |
| `tools/make_env.sh` | 환경변수 → env.yaml |
| `deploy.sh` | Cloud Run 배포 + 텔레그램 웹훅·메뉴 + 디스코드 엔드포인트 등록 |
| `tests/` | 가짜 시트/디스코드 테스트 — `pip install -r requirements.txt openpyxl && bash tests/run_all.sh` |

### 시트 탭 요약
Today(보기) · Client/Farming/TL Board(보기) · **Schedule**(오늘부터, A:O 입력, P:U 계산: Hours/Week/Key/Display/Maint Hrs/Pay Week) · Schedule Archive · All Shifts(숨김) · Accounts · Planner · Settings(정기점검·컨펌 시각) · TL Schedule · Payroll(월~일 2주, 기준 월요일 G2=2026-09-07) · Payroll History · Overtime · Incentives · Death Penalty · Performance Pay · Shift Reports · Staff · Confirmations · Glossary · EventLog

---

## 5. 규칙 (반드시 지킬 것)
- 비밀값을 파일·커밋·로그·채팅에 남기지 않음. `env.yaml` 은 커밋 금지(.gitignore)
- 시트 쓰기 테스트는 사용자 승인 후. 로컬 테스트는 `tests/` 가짜 시트로
- 기존 수기 시트(1dwv88…)는 쓰지 않음, 서비스계정에 공유 금지
- 카카오(TalkBridge) 코드는 보류 — 건드리지 말 것, 빈 값으로도 기동돼야 함
- 수식은 `layout.py` 에서만 정의. ARRAYFORMULA 안의 SUMIFS/MIN/MAX 는 행별로 안 됨 → 행마다 수식이나 IF 로
- 디스코드 응답 3초: 시트/AI 는 항상 백그라운드(type 5/6 후 `_edit`)
- 급여 = 월~일 2주 단위. 슬롯은 8시간 넘을 때만 추가
- 커밋 메시지·PR 에 모델 이름 넣지 않기

---

## 6. 다음 할 일 (우선순위)
1. **v4+ 배포** — 사용자와 함께: Cloud Shell 코드 갱신 → `DISCORD_BOT_TOKEN=… python3 tools/setup_discord.py`(새 채널 2개·명령 등록) → `bash deploy.sh`
2. **라이브 시트 검수** — 배포 후 모든 탭에 `#REF! #ERROR! #N/A #VALUE!` 없는지 (Drive/Sheets API 로 읽기). 특히 Client/Farming/TL Board, Payroll, Today, All Shifts
3. `/check kind:day`·`kind:week` 로 컨펌 카드 확인, Confirmations 탭 기록 확인
4. 실제 게임 스크린샷으로 `/shot` 정확도 확인 (레벨·EXP %·아데나) — 틀리면 `vision.py` SYSTEM 프롬프트 보강 (UI 위치 설명, 예시)
5. 영업자 Chat ID 받아 `SALES_CHAT_IDS` 채우고 재배포, 텔레그램 → 디스코드 카드 → Confirm 전체 흐름 테스트 (WORK_ORDER.md Phase 6 목록 16개)
6. 테스트용 "테스트" 캐릭터 행(Accounts·Schedule) 삭제 확인

## 7. 사용자 요구사항 이력 (의도 파악용)
- 고객/농장 계정 분리, TL 근무표·Payroll·Death Penalty·Performance Pay·Overtime·Incentives 이관
- 매니저는 디스코드(영어)로 기록, AI 가 스케줄을 찾아 확인 후 입력, **빠른 반응**
- 인센티브는 매니저가 금액 입력 → Payroll 자동 합산, 급여는 **월~일 2주**
- 한 달치 스케줄 입력(Planner), 매주 수요일 05:00-09:00 정기점검은 근무시간에서 제외
- 텔레그램 영업 → 디스코드 → 스케줄까지 한 번에, 새 디스코드 서버·새 텔레그램 봇
- 시간은 오전/오후 명확히(20:00-04:00 = 오후 8시~다음날 오전 4시), 리니지 클래식 용어·슬랭 학습(Glossary + /learn, /learn_bulk)
- 오늘 스케줄이 맨 위, 지난 스케줄은 따로, OFF·플레이어 없는 파밍은 안 보이게
- 슬롯은 8시간 넘을 때만, 고객 캐릭은 전주 플레이어 유지하되 **일별·주별 매니저 컨펌**
- 플레이어는 스크린샷만 올리면 됨 (`/iam` 불필요 — 디스코드 ID↔이름은 Staff 탭, 이름 같으면 자동 연결), 읽은 값만 바로 기록
