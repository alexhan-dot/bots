# 작업지시서 — 리니지 스케줄 봇 셋업 & 배포 (Claude Code용)

> 이 파일을 Claude Code에서 열고 "WORK_ORDER.md 대로 진행해줘"라고 하면 됩니다.
> 작업 원칙: **각 Phase 시작 전에 할 일 목록을 먼저 보여주고 승인받은 뒤 실행.** 비밀값은 절대 파일에 하드코딩하거나 커밋하지 않는다.

---

## 0. 프로젝트 개요 (컨텍스트)

리니지 클래식 부스팅 운영 자동화 봇. 영업자가 텔레그램으로 보내는 한글 요청(스케줄 변경·사냥터 변경·고객 질문 등)을 AI가 파싱 → 캐릭터 마스터와 대조 → 텔레그램 버튼으로 컨펌 → 구글시트 반영 → 디스코드 알림 → 매니저 컨펌 링크 → 영업자에게 확정 회신.

- 런타임: Python 3.12 / FastAPI / Cloud Run (기존 GCP 프로젝트 재사용)
- 상태 저장: Firestore (Native)
- AI: Anthropic API (`claude-sonnet-4-6`), 이미지=Vision, 음성=Google Speech-to-Text
- **현재 채널: 텔레그램(영업자, 한글) + 디스코드(알림 웹훅 + 매니저 봇, 영어: OT·인센티브·페널티·스케줄 기록).**
  카카오 상담톡(TalkBridge)은 코드에 있으나 한국 사업자 인증 문제로 **보류** — `TALKBRIDGE_*` 환경변수 비워둠. WhatsApp은 **제거됨**.

### 파일 구조
```
main.py                 FastAPI 진입점, 텔레그램 웹훅, 컨펌/답변 엔드포인트, 카카오 웹훅(보류)
services/telegram.py    Bot API 헬퍼
services/parser.py      한글 → op JSON (Claude), 용어사전 주입, 다중 작업 추출
services/matcher.py     캐릭명 매칭 (정확→별칭→한글명→유사도)
services/translate.py   KR↔EN 번역, 트레이너 초안
services/glossary.py    Glossary 시트 탭 로드/추가, 학습 루프
services/sheets.py      Accounts, Schedule(주 자동 생성), op별 반영, EventLog
services/layout.py      v2 시트 열·수식 정의 (봇·이관 스크립트 공유)
tools/build_v2_sheet.py 수기 시트 → v2 이관
services/notify.py      디스코드 웹훅
services/media.py       이미지/음성 → 텍스트 (ffmpeg 변환 포함)
services/state.py       Firestore pending 상태
services/kakao.py       TalkBridge 어댑터 (보류)
services/discord_bot.py 디스코드 매니저 봇 (Interactions, 영어)
services/manager.py     매니저 기록 로직 (스케줄 매칭·미리보기·저장)
services/index.py       메모리 인덱스 (빠른 조회·자동완성)
services/ai_parse.py    /log 자유 입력 → 양식 (Claude API)
services/clock.py       로컬 시간대 today/now
data/accounts_seed.csv      Accounts 시드 (63계정, Type 포함)
data/schedule_seed.csv      Schedule 시드 (Week 39 이관 + Week 40)
data/character_master.csv   (구) 캐릭터 마스터 — 이관 입력용
data/glossary.csv           용어 사전 시드 (28행)
env.yaml.example / deploy.sh / Dockerfile / requirements.txt / README.md
```

### 시트 — `Lineage Schedule v2` (SHEET_ID=1fMQDRmGVVtaUR0cVBzzSaowHU38clVuON1PoxGWCT_A)
- 고객(Client)/농장(Farming) 분리 구조. 탭: `Client Board`, `Farming Board`, `Schedule`, `Accounts`, `Planner`, `Settings`, `Payroll`, `Glossary`, `EventLog` 등 (README.md 참고)
- **급여 = 월~일 기준 2주 단위** (기준 월요일 Payroll!G2 = 2026-09-07 → 9/21~10/4, 10/5~10/18 …). 보드의 주간 보기(일~토)와 별개로 각 탭 `Pay Week`(월요일) 열로 합산
- **정기점검**: Settings 탭 (기본 수요일 05:00-09:00) — 겹치는 시프트 시간은 Hours에서 자동 차감
- 봇 기동(ensure_tabs) 시: Settings·Planner 탭 생성, Schedule T·U / TL K / 로그 탭 Pay Week 열 추가, Payroll 기준일을 월요일로 교정
- 기존 수기 시트(1dwv88…)는 봇이 사용하지 않음 — **서비스계정에 공유하지 말 것** (Login Credentials 탭 포함)
- Schedule 데이터는 봇 첫 기동 때 `data/schedule_seed.csv`(Week 39 이관분 + Week 40)로 자동 채워짐

### 작업 유형 (parser → sheets.apply)
| type | 시트 동작 | 시간 규칙 |
|---|---|---|
| NEW_CHARACTER | Accounts 추가(Type) + Schedule 슬롯 행 생성 | 요일별 시간 전부 필수 |
| SCHEDULE_LEDGER | "24. 수 9/23 10:00~18:00 -> 삭제 / 추가 목 …" 장부 → 추가/OFF | 줄 단위 |
| STOP | 해당 일자 셀 OFF | 불변 |
| PLAYER_SWAP | 셀 2행(플레이어)만 교체 | 불변 |
| EXTEND | 해당 일자·시프트 셀 시간만 변경 (24:00-08:00→24:00-09:30) | 해당 셀만 |
| HUNTING_GROUND | 셀 3행 `@사냥터` (고정/번갈아/오늘/양시프트) | — |
| RELOGIN | EventLog + 🔴 긴급 디스코드 | — |
| QUESTION | EventLog(open) + 디스코드 답변 요청 → 답변 오면 영업자에게 한글 전달 | — |
| INFO | EventLog만, 알림 없음 | — |

Schedule 행: Time = `HH:MM-HH:MM` 또는 `OFF`, Player / Hunting Ground 별도 열. 이번 주 전체 OFF면 Accounts Status=Inactive (행은 기록 보존).

---

## 1. Phase 1 — 로컬 환경 점검

**할 일**
1. `gcloud --version`, `gcloud config list` 로 SDK·계정·프로젝트 확인. 없으면 사용자에게 설치 안내(https://cloud.google.com/sdk/docs/install-sdk) 후 대기.
2. `python --version` (3.12 권장, 로컬 실행은 선택), `git --version`.
3. 현재 디렉토리에 `main.py`, `services/`, `data/` 있는지 확인.
4. `env.yaml`이 없으면 `env.yaml.example` 복사해 생성. `.gitignore`에 `env.yaml` 있는지 확인.

**완료 기준**: gcloud가 올바른 프로젝트를 가리키고, `env.yaml`이 존재.

---

## 2. Phase 2 — GCP 리소스

**사용자에게 먼저 물을 것**
- 리전: `asia-northeast3`(서울) 또는 `australia-southeast1`(시드니) 중 선택 (기본 서울)

**할 일** (PROJECT_ID는 `gcloud config get-value project`로 얻기)
```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com speech.googleapis.com sheets.googleapis.com

gcloud iam service-accounts create lineage-bot --display-name "Lineage Schedule Bot" || true
SA=lineage-bot@${PROJECT_ID}.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding $PROJECT_ID --member serviceAccount:$SA --role roles/datastore.user
gcloud projects add-iam-policy-binding $PROJECT_ID --member serviceAccount:$SA --role roles/speech.client
gcloud projects add-iam-policy-binding $PROJECT_ID --member serviceAccount:$SA --role roles/logging.logWriter

# Firestore (default) DB 없으면 생성
gcloud firestore databases describe --database="(default)" 2>/dev/null || \
  gcloud firestore databases create --location=$REGION --type=firestore-native
```
5. 서비스계정 이메일을 사용자에게 보여주고 **`Lineage Schedule v2` 시트만 공유(편집자)** 하도록 안내 → 완료 답 받을 때까지 대기.

**완료 기준**: API 6개 활성, SA 존재, Firestore 존재, 사용자가 시트 공유 완료 확인.

---

## 3. Phase 3 — 시트 적용 (사용자)

1. `sheet/Lineage_Schedule_v2.xlsx` 를 `Lineage Schedule v2` 시트에서 *파일 → 가져오기 → 업로드 → "스프레드시트 바꾸기"* (시트 ID 유지)
   → Schedule(Week 39·40), TL 근무표(Week 16~39), Payroll History, Overtime, Death Penalty, Performance Pay 포함
2. `Accounts` 탭에서 README 탭 하단 "⚠️ 확인 필요" 6개 계정(Kangaroo, Kyoryu, Pele, Sarim, Sudden, Taejo)의 Type 확정
3. (선택) Class 빈 칸, Customer, SalesRep 입력

**검증**: Client/Farming/TL Board에 이번 주가 보이고 Payroll 탭에 이번 급여 기간(2026-09-20 ~ 10-03)의 직원별 시간·OT·페널티·인센티브가 집계되는지.
Board/Payroll A열 목록은 봇 첫 기동(ensure_tabs) 때 동적 수식으로 교체됨 — 배포 후 한 번 더 확인.

## 4. Phase 4 — 새 텔레그램 봇 · 새 디스코드 서버 (사용자와 하나씩)

값은 사용자가 **env.yaml에 직접 입력**하거나 스크립트가 채움. 채팅 로그·커밋에 남기지 않는다. (`cp env.yaml.example env.yaml` 먼저)

### 4-1. 텔레그램 봇 (새로)
1. 텔레그램 **@BotFather** → `/newbot` → 표시 이름(예: Lineage Schedule) → 유저네임(`..._bot` 으로 끝) → 토큰 → `TELEGRAM_BOT_TOKEN`
2. BotFather `/setprivacy` → 봇 선택 → **Disable** (단체방에서도 메시지 읽기)
3. `TELEGRAM_WEBHOOK_SECRET` = `openssl rand -hex 16` 결과
4. 대표가 **새 봇에게 `/start`** → 답장에 나오는 Chat ID → `ADMIN_CHAT_ID` *(배포 후에 가능 — 배포 전이면 @userinfobot 으로 확인)*
5. 영업자들도 새 봇에게 `/start` → 각자 Chat ID → `SALES_CHAT_IDS` (쉼표). 미등록 사람이 메시지를 보내면 봇이 대표에게 Chat ID를 알려줌 → 추가 후 재배포

### 4-2. 디스코드 (새 서버)
1. 디스코드 앱 → 서버 추가(+) → **직접 만들기** → 이름 (예: Lineage Ops)
2. https://discord.com/developers/applications → **New Application** (이름 예: Lineage Bot)
3. 왼쪽 **Bot** → *Reset Token* → 토큰 복사 (한 번만 보임)
4. 로컬/Cloud Shell: `pip3 install httpx` 후
   `DISCORD_BOT_TOKEN=<토큰> python3 tools/setup_discord.py` → 출력된 **초대 링크**를 열어 새 서버에 봇 추가
5. 같은 명령을 한 번 더 실행 → 역할 `Manager`, 채널 `#sales-requests` `#manager-desk` `#bot-log`, 슬래시 명령, 사용법 고정 메시지 생성 + **env.yaml 의 DISCORD_* 자동 기입**
6. 서버 설정 > 멤버 → 매니저들(본인 포함)에게 **Manager** 역할 부여. 매니저들을 서버에 초대 (서버 이름 우클릭 > 초대하기)

### 4-3. 나머지
| env 키 | 얻는 방법 |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com > API Keys > Create Key |
| `BOT_TZ` | "오늘" 기준 시간대 (기본 Asia/Seoul) — 사용자에게 확인 |
| `TALKBRIDGE_*` | **비워둠 (보류)** |

**완료 기준**: env.yaml에 TELEGRAM_BOT_TOKEN · ADMIN_CHAT_ID · ANTHROPIC_API_KEY · DISCORD_* (스크립트) 채워짐.

---

## 5. Phase 5 — 배포

```bash
bash deploy.sh            # REGION 환경변수로 리전 변경 가능: REGION=australia-southeast1 bash deploy.sh
```
- 첫 배포 5~8분. 실패 시 로그 읽고 원인 수정 (흔한 원인: API 미활성, SA 권한, Dockerfile ffmpeg 설치 실패)
- deploy.sh 가 자동으로: 텔레그램 웹훅·메뉴(/start /week /glossary) 등록, **디스코드 Interactions Endpoint 등록**
- `curl $URL/healthz` → `{"ok":true}`
- 대표·영업자가 새 봇에 `/start` → Chat ID 확인 → env.yaml 에 넣고 `bash deploy.sh` 한 번 더 (4-1의 4·5)
- 디스코드 `#manager-desk` 에서 `/schedule` → 오늘 보드가 뜨면 연결 완료

**완료 기준**: healthz OK, 텔레그램 `/start` 응답, 디스코드 `/schedule` 응답.

---

## 6. Phase 6 — 테스트 (사용자와 함께, 순서대로)

각 단계마다 사용자에게 텔레그램에서 보낼 메시지를 알려주고, 결과를 `gcloud run services logs read lineage-schedule-bot --region $REGION --limit 50`으로 확인.

1. 대표 → 봇: `/glossary` → "용어 N개 등록됨" 회신 (시트 연결 OK)
2. 대표 → 봇: `/learn 테스트 = 테스트 / test / unknown` → Glossary 탭에 행 추가 확인 → 확인 후 그 행 삭제
3. 영업자(또는 SALES에 등록된 대표) → 봇: `아덴서버 돌 케릭 상아탑 6층 고정` → 캐릭 후보/신규 버튼 도착
4. `➕ 신규 캐릭터` 누르면 양식 안내 → 양식대로 전송(구분: 고객/농장 포함) → 컨펌 버튼 → ✅ → Accounts에 행 추가 + Schedule에 이번 주(오늘부터)·다음 주 행 추가 + 해당 Board에 표시 + 디스코드 알림
5. `#sales-requests` 카드의 **✅ Confirm** → 영업자 텔레그램에 "확정 완료", 카드 회색으로 바뀜 · **💬 Reply** 로 영어 입력 → 영업자에게 한글 전달
6. 대표 → 봇: 카톡 스크린샷 1장 → 한/영 초안 + 승인 버튼 → ✅ → 디스코드에 Notice
7. 영업자 → 봇: `24. 수 9월 23일 : 10:00 ~ 18:00 (8시간) -> 삭제\n추가 목 9월 24일 : 24:00 ~ 08:00(8시간)` → "어느 캐릭터 건인가요?" 버튼 → 선택 → 시트 반영
8. 영업자 → 봇: `매니저님 버땅보다 버땅심연이 경치 더 주나요?` → QUESTION 분류 → `#sales-requests` 카드 **💬 Answer** → `Abyss gives more EXP` → 영업자에게 한글 답변 도착

9. 매니저(디스코드) → `/schedule` → 오늘 보드(빈 자리 먼저) · `/schedule account:Alex` → 오늘 시프트 즉시 표시
10. 매니저 → `/ot staff:<오늘 근무자> hours:1 reason:test` → 매칭된 시프트 미리보기 → Confirm → Overtime 탭에 행 + 채널 로그 → 확인 후 행 삭제
11. 매니저 → `/log <근무자> 1h OT today test` → 같은 미리보기가 뜨는지 (AI) → Cancel
12. 매니저 → Planner 탭에 테스트 규칙 1줄(다음 주 수요일 하루, 04:00-10:00) → `/plan` → 미리보기에 ⚙️ 점검 4h 표시 → Apply → Schedule 행 Hours = 2, Status "Applied" → 확인 후 행 원복
13. Payroll 탭 B2~D2가 월요일~일요일 2주로 표시되는지 (예: 2026-09-21 / 09-28 / 10-04)

**완료 기준**: 13개 모두 통과. 실패한 항목은 로그 기반으로 코드 수정 후 재배포·재테스트.

---

## 7. Phase 7 — 인수인계

1. README.md의 설정 순서가 실제 진행과 다르면 갱신
2. `/learn_bulk` 사용법을 사용자에게 안내: 카톡 그룹방 "대화 내보내기" 텍스트를 붙여넣으면 용어 후보 일괄 추출
3. 운영 명령 요약을 사용자에게 전달:
   - 로그: `gcloud run services logs read lineage-schedule-bot --region $REGION --limit 50`
   - 재배포: `bash deploy.sh`
   - 영업자 추가: env.yaml `SALES_CHAT_IDS` 수정 → 재배포

---

## 8. 금지·주의 사항

- `env.yaml` 커밋 금지, 비밀값 채팅에 출력 금지 (마스킹)
- 기존 수기 시트(1dwv88…)는 봇에 공유·수정 금지 (Login Credentials 탭 포함)
- 사용자 승인 없이 시트에 쓰기 테스트 금지 (Phase 6에서만, 사용자와 함께)
- Cloud Run `--allow-unauthenticated`는 웹훅 수신을 위해 필요. 대신 `/confirm`, `/answer`는 sid(랜덤 10자)로만 보호됨 → 운영 안정화 후 서명 토큰 추가 검토
- 카카오(TalkBridge) 관련 코드는 건드리지 않되, 환경변수가 비어 있어도 앱이 정상 기동해야 함 (현재 `os.environ.get` 처리됨)

## 9. 알려진 개선 후보 (사용자가 요청할 때만)
- 디스코드 봇(버튼) 도입해 링크 클릭 대신 버튼 컨펌
- `/confirm`, `/answer`에 HMAC 서명
- 다음 주 자동 생성 스케줄러(Cloud Scheduler로 매주 토요일 `/week` 상당 호출)
- Firestore `seen`/`pending` 컬렉션 TTL 정리
