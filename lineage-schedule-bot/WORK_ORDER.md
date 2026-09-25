# 작업지시서 — 리니지 스케줄 봇 셋업 & 배포 (Claude Code용)

> 이 파일을 Claude Code에서 열고 "WORK_ORDER.md 대로 진행해줘"라고 하면 됩니다.
> 작업 원칙: **각 Phase 시작 전에 할 일 목록을 먼저 보여주고 승인받은 뒤 실행.** 비밀값은 절대 파일에 하드코딩하거나 커밋하지 않는다.

---

## 0. 프로젝트 개요 (컨텍스트)

리니지 클래식 부스팅 운영 자동화 봇. 영업자가 텔레그램으로 보내는 한글 요청(스케줄 변경·사냥터 변경·고객 질문 등)을 AI가 파싱 → 캐릭터 마스터와 대조 → 텔레그램 버튼으로 컨펌 → 구글시트 반영 → 디스코드 알림 → 매니저 컨펌 링크 → 영업자에게 확정 회신.

- 런타임: Python 3.12 / FastAPI / Cloud Run (기존 GCP 프로젝트 재사용)
- 상태 저장: Firestore (Native)
- AI: Anthropic API (`claude-sonnet-4-6`), 이미지=Vision, 음성=Google Speech-to-Text
- **현재 채널: 텔레그램(수신·컨펌) + 디스코드(알림·매니저 컨펌)만 사용.**
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
data/accounts_seed.csv      Accounts 시드 (63계정, Type 포함)
data/schedule_seed.csv      Schedule 시드 (Week 39 이관 + Week 40)
data/character_master.csv   (구) 캐릭터 마스터 — 이관 입력용
data/glossary.csv           용어 사전 시드 (28행)
env.yaml.example / deploy.sh / Dockerfile / requirements.txt / README.md
```

### 시트 — `Lineage Schedule v2` (SHEET_ID=1fMQDRmGVVtaUR0cVBzzSaowHU38clVuON1PoxGWCT_A)
- 고객(Client)/농장(Farming) 분리 구조. 탭: `Client Board`, `Farming Board`, `Schedule`, `Accounts`, `Glossary`, `EventLog` (README.md 참고)
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

## 3. Phase 3 — 시트 확인 (사용자)

새 시트 `Lineage Schedule v2`는 이미 만들어져 있음 (탭 준비 불필요).
1. `Accounts` 탭에서 README 탭 하단 "⚠️ 확인 필요" 6개 계정(Kangaroo, Kyoryu, Pele, Sarim, Sudden, Taejo)의 Type(Client/Farming) 확정
2. Class 빈 칸 채우기, Customer(고객명)·SalesRep(담당 영업) 입력 (선택)

**검증**: 배포 후 봇 첫 기동 시 Schedule 탭이 채워지고 Client/Farming Board에 이번 주가 표시되는지 확인

## 4. Phase 4 — 외부 키 수집 (사용자에게 하나씩 요청)

값은 사용자가 **env.yaml에 직접 입력**하게 하거나, 채팅으로 받으면 Claude Code가 env.yaml에만 기록. 채팅 로그·커밋에 남기지 않는다.

| env 키 | 얻는 방법 |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com > API Keys > Create Key |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 @BotFather > /newbot > 이름·유저네임(bot으로 끝남) > 토큰. 이어서 /setprivacy > 봇 선택 > Disable |
| `ADMIN_CHAT_ID` | 대표가 @userinfobot에게 메시지 → 숫자 ID |
| `SALES_CHAT_IDS` | 영업자 각자 @userinfobot → 쉼표로 나열 (없으면 일단 ADMIN과 동일하게) |
| `DISCORD_WEBHOOK_URL` | 디스코드 채널 설정 > 연동 > 웹훅 > 새 웹훅 > URL 복사 |
| `BOT_BASE_URL` | Phase 5 첫 배포 후 채움 |
| `TALKBRIDGE_*` | **비워둠 (보류)** |

대표와 영업자 모두 봇에게 `/start` 한 번 눌러두도록 안내.

**완료 기준**: env.yaml에 위 5개 값 채워짐 (BOT_BASE_URL 제외).

---

## 5. Phase 5 — 배포

```bash
bash deploy.sh            # REGION 환경변수로 리전 변경 가능: REGION=australia-southeast1 bash deploy.sh
```
- 첫 배포 5~8분. 실패 시 로그 읽고 원인 수정 (흔한 원인: API 미활성, SA 권한, Dockerfile ffmpeg 설치 실패)
- 출력된 서비스 URL을 `env.yaml`의 `BOT_BASE_URL`에 기록 → `bash deploy.sh` 한 번 더
- `curl $URL/healthz` → `{"ok":true}` 확인
- `curl "https://api.telegram.org/bot$TOKEN/getWebhookInfo"` → url이 `$URL/telegram/webhook`인지 확인

**완료 기준**: healthz OK, 텔레그램 웹훅 등록 확인.

---

## 6. Phase 6 — 테스트 (사용자와 함께, 순서대로)

각 단계마다 사용자에게 텔레그램에서 보낼 메시지를 알려주고, 결과를 `gcloud run services logs read lineage-schedule-bot --region $REGION --limit 50`으로 확인.

1. 대표 → 봇: `/glossary` → "용어 N개 등록됨" 회신 (시트 연결 OK)
2. 대표 → 봇: `/learn 테스트 = 테스트 / test / unknown` → Glossary 탭에 행 추가 확인 → 확인 후 그 행 삭제
3. 영업자(또는 SALES에 등록된 대표) → 봇: `아덴서버 돌 케릭 상아탑 6층 고정` → 캐릭 후보/신규 버튼 도착
4. `➕ 신규 캐릭터` 누르면 양식 안내 → 양식대로 전송(구분: 고객/농장 포함) → 컨펌 버튼 → ✅ → Accounts에 행 추가 + Schedule에 이번 주(오늘부터)·다음 주 행 추가 + 해당 Board에 표시 + 디스코드 알림
5. 디스코드 알림의 Confirm 링크 클릭 → 영업자 텔레그램에 "확정 완료"
6. 대표 → 봇: 카톡 스크린샷 1장 → 한/영 초안 + 승인 버튼 → ✅ → 디스코드에 Notice
7. 영업자 → 봇: `24. 수 9월 23일 : 10:00 ~ 18:00 (8시간) -> 삭제\n추가 목 9월 24일 : 24:00 ~ 08:00(8시간)` → "어느 캐릭터 건인가요?" 버튼 → 선택 → 시트 반영
8. 영업자 → 봇: `매니저님 버땅보다 버땅심연이 경치 더 주나요?` → QUESTION 분류 → 디스코드 답변 요청 링크 → 링크 뒤 `&text=Abyss gives more EXP` 붙여 열기 → 영업자에게 한글 답변 도착

**완료 기준**: 8개 모두 통과. 실패한 항목은 로그 기반으로 코드 수정 후 재배포·재테스트.

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
