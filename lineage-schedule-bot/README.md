# 리니지 스케줄 자동화 봇

## 워크플로우
- **Flow A (스케줄)**: 영업자 텔레그램(한글) → 파싱·번역 → 캐릭명 매칭(마스터 기준, 애매하면 후보 버튼)
  → 영업자 컨펌 → 시트 반영 → 디스코드 알림 → 매니저 컨펌(링크 클릭) → 영업자에게 확정 회신
- **Flow B (카카오톡, ⏸ 보류 — 사업자 인증 문제로 TALKBRIDGE_* 비워둠)**: 영업자가 카카오톡 채널로 메시지(텍스트/이미지/음성) 발송
  → TalkBridge 웹훅 신호 → 봇이 본문 조회 → 첨부는 Vision/STT 변환 → 영업자에게 "접수" 자동 회신
  → 트레이너용 한/영 초안을 대표 텔레그램으로 → 승인/수정 → 트레이너 채널 발송 + 영업자에게 카톡 확정 회신
  - 텔레그램으로 직접 전달(복붙/공유)해도 동일하게 처리됨 (백업 경로)

## 작업 유형 규칙
| 유형 | 동작 | 시간 변경 |
|---|---|---|
| NEW_CHARACTER | 캐릭명+클래스+**고객/농장** 구분 입력, MON~SUN 전 요일 시간 필수 | 신규 정의 |
| STOP | 해당 일자만 OFF | ❌ 불변 |
| PLAYER_SWAP | 플레이어명만 교체 | ❌ 불변 |
| EXTEND | 해당 일자·해당 시프트만 24:00-08:00 → 24:00-09:30 식 변경 | 해당 셀만 |

## 시트 구조 — `Lineage Schedule v2` (고객/농장 분리)
기존 수기 시트(`TargetWeekNN` 탭, 고객·농장이 한 그리드에 좌우로 섞임)는 더 이상 봇이 읽거나 쓰지 않음.
새 시트: https://docs.google.com/spreadsheets/d/1fMQDRmGVVtaUR0cVBzzSaowHU38clVuON1PoxGWCT_A

| 탭 | 용도 | 편집 |
|---|---|---|
| `Client Board` / `Farming Board` | 고객 / 농장 계정 주간 그리드 (셀 = 시간⏎플레이어⏎@사냥터). B2에 일요일 날짜 넣으면 다른 주 조회 | 보기 전용 (수식) |
| `Schedule` | 원장. 1행 = 계정 × 날짜 × 시프트(Slot). A:O 입력, P:S(Hours/Week/Key/Display) 자동 계산 | 사람 + 봇 |
| `Accounts` | 계정 마스터. **Type = Client(고객) / Farming(농장)**, Status = Active/Paused/Inactive, Customer, SalesRep | 사람 + 봇 |
| `TL Board` / `TL Schedule` | 팀 리더 근무표 (주간 보기 / 입력: 근무시간·출근). Week 16~39 이관. J열 Hours = "8am-4pm" 형식에서 자동 계산 (OFF·CANCEL OFF·Absent = 0) | 사람 (+ `/week` 복사) |
| `Payroll` | **2주 단위 급여 기간** 직원별 집계: Week 1/2 Hrs(플레이어 시프트 + TL 근무) · OT · Death Penalty 차감 · Payable Hrs · Incentives(매니저 입력 금액 합계) · Shifts · Characters. **급여 기간 = 월~일 × 2주** (예: 9/21~10/4). B2 = 기간 시작 월요일(기본: 오늘이 속한 기간, 기준 월요일 G2 = 2026-09-07), 다른 기간은 B2에 월요일 날짜 입력. 각 탭의 `Pay Week` 열(월요일)로 합산 | 보기 전용 (수식) |
| `Payroll History` | 기존 수기 Payroll (W19·20·33·34) | 기록 |
| `Planner` | **기간 스케줄 입력** — 1행 = 반복 규칙 (Account · Slot · Days · Time · Player · Hunting Ground · From · To). 디스코드 `/plan` 으로 미리보기 → Apply 하면 Schedule에 반영, Status에 "Applied" 기록 | 매니저 |
| `Settings` | 정기점검 요일·시작·끝 (기본 **수요일 05:00-09:00**). Schedule `Maint Hrs`(T열)가 겹친 시간을 계산해 Hours에서 자동 차감, 보드 셀에 "⚙ maint -4h" 표시 | 사람 |
| `Overtime` | OT 로그 (직원·계정·시프트·OT 시간·사유·매니저). TL OT 이관분 포함 | 디스코드 봇 + 사람 |
| `Incentives` | 인센티브 로그 | 디스코드 봇 + 사람 |
| `Death Penalty` | 데스 페널티 로그 (7개 탭 → 1개로 통합, 중복 제거) | 디스코드 봇 + 사람 |
| `Performance Pay` | 인센티브 기준표 (원본 그대로) | 사람 |
| `Glossary` / `EventLog` | 용어 사전 / 봇 기록 | |

**적용 방법**: `sheet/Lineage_Schedule_v2.xlsx` 를 새 시트에서 *파일 → 가져오기 → 업로드 → "스프레드시트 바꾸기"* (시트 ID 유지).
봇 기동 시 계산 수식(Board·Payroll·Week 열)을 열린 범위로 다시 쓰고, 빠진 기록 탭은 헤더만 만들어 둠.
xlsx 가져오기는 목록 수식(SORT/UNIQUE/FILTER)을 계산하지 못해 Board·Payroll의 A열 목록은 값으로 들어가 있음 → **봇이 처음 기동할 때 동적 수식으로 바뀜** (그 전까지는 새 직원·계정이 목록에 자동 추가되지 않음).
합계 열(Hours·OT·Payroll)은 행마다 수식 (SUMIFS 는 ARRAYFORMULA 안에서 가져오기 시 첫 값만 계산되기 때문).

**급여 계산**: `Payable Hrs = Base(Week1 + Week2) + OT − Penalty`. 인센티브는 매니저가 `Incentives` 탭(또는 `/incentive`)에 금액을 직접 입력 → Payroll H열에 기간 합계. 급여 기간은 월~일 2주 — 기준 월요일은 Payroll G2 (`layout.PAY_ANCHOR`). 봇 기동 시 G2가 월요일이 아니면 기본값으로 고침.

- 주간 탭을 매주 새로 만들지 않음. 새 주 첫 작업(또는 `/week` 명령) 때 직전 주의 **시간·사냥터를 복사**, 플레이어·실적은 비움 (Active 계정만)
- 고객↔농장 이동 = `Accounts`의 Type 변경 (다음 주 생성분부터 반영)
- 봇 기동 시: Schedule/Accounts가 비어 있으면 `data/schedule_seed.csv`, `data/accounts_seed.csv`로 채우고, 계산 수식(P:S, Board)을 다시 씀
- 기존 수기 시트에서 다시 이관하려면: `python tools/build_v2_sheet.py 원본.xlsx "TargetWeekNN(...)" out.xlsx`

## 설정 순서
1. **텔레그램 봇**: @BotFather → /newbot → 토큰 확보. 대표/영업자 chat_id는 @userinfobot으로 확인
2. **시트**: `Lineage Schedule v2` 사용 (이미 생성됨, 탭 준비 불필요). 로그인 정보 탭 없음 — 넣지 말 것
3. **서비스계정·Firestore**: `WORK_ORDER.md` Phase 2 명령으로 `lineage-bot@<PROJECT_ID>.iam.gserviceaccount.com` 생성 + Firestore(Native) 생성
4. **시트 공유**: 위 서비스계정 이메일에 편집자 권한
5. **Anthropic API 키**: console.anthropic.com에서 발급
6. **디스코드**: 채널 설정 > 연동 > 웹훅 생성 → URL
7. **env.yaml**: `cp env.yaml.example env.yaml` 후 값 채우기 (커밋 금지). `TELEGRAM_WEBHOOK_SECRET`에 임의 문자열을 넣으면 위조 요청 차단
8. **배포**: `bash deploy.sh` → 출력된 URL을 env.yaml `BOT_BASE_URL`에 넣고 한 번 더 `bash deploy.sh`
9. (보류) **카카오 상담톡 (TalkBridge)** — 아래 별도 섹션. 활성화 시 `/kakao/webhook` URL을 톡브릿지 센터에 등록

## 카카오 상담톡 설정 (TalkBridge 개발자 모드)
1. **카카오톡 채널 개설** — https://center-pf.kakao.com (검색용 아이디 정하기)
2. **비즈니스 채널 전환 심사** — 관리자센터 > 관리 > 비즈니스 채널 신청. 법인: 사업자등록증 + 대표 휴대폰 본인인증. 통상 1~3영업일
3. 채널 **공개 ON + 검색 허용 ON**
4. **TalkBridge 가입** — https://console.talkbridge.io → 브랜드 설정에서 채널 연결(채널명·검색용 아이디·채널 URL 입력)
5. **연동 관리 > 개발자 모드 > 연동 키 발급** → 3개 값 확보
   - `TALKBRIDGE_BRAND_KEY` (Brand Key)
   - `TALKBRIDGE_AGENT_KEY` (blumnb-… , **BrandWrite** 스코프 — 회신 발신에 필요, 발급 직후 1회만 표시)
   - `TALKBRIDGE_WHSEC` (whsec_… 서명 검증 키)
6. 배포 후 **자체 서비스 URL**에 `https://<서비스URL>/kakao/webhook` 등록 → 「연결 테스트」로 수신/발신 확인
7. **상담 활성화** ON
8. 영업자에게 채널 검색용 아이디(@xxxx) 공유 → 앞으로 이 채널로 메시지 보내도록 안내

요금: Free(30일 3건) → Pro 100 월 ₩16,500(VAT포함, 100건) → Pro 300 ₩49,500. **하루에 응대한 고객 1명 = 1건**(메시지 수 아님)이라 영업자 몇 명 규모면 Pro 100으로 충분. 초과해도 후청구 없이 신규 상담만 멈춤.

주의: 상담톡 정식 사용 시 카카오톡 채널 관리자센터의 기존 1:1 채팅 메뉴는 비활성화됨(채팅 이력 조회 불가). 봇 웹훅이 응답 못 하면 TalkBridge가 재시도하므로 Cloud Run 최소 인스턴스 0으로 둬도 유실 없음.

## 디스코드 매니저 봇 (영어)
시간대별 매니저가 직원 스케줄·OT·인센티브·페널티를 디스코드에서 양식으로 기록 → 봇이 스케줄을 찾아 보여줌 → **Confirm** 누르면 시트에 기록 + `#bot-log` 에 기록.

| 명령 | 기록 위치 | 예 |
|---|---|---|
| `/ot staff hours [date] [time] [account] [reason]` | Overtime | `/ot staff:Reno hours:2 date:yesterday reason:boss` |
| `/incentive staff amount [date] [account] [kpi] [level] [reason]` | Incentives | |
| `/penalty staff hours [date] [account] [action] [ir]` | Death Penalty (Shift 자동: Morning/Mid/Graveyard) | |
| `/assign account player [date] [slot] [time]` | Schedule Player | |
| `/off account [date] [slot] [time]` | Schedule Time=OFF (슬롯 없으면 그날 전체) | |
| `/extend account new_time [date] [time] [slot]` | Schedule Time | |
| `/schedule [account] [staff] [date]` | 조회만 (인자 없으면 오늘 전체, 빈 자리 먼저) | |
| `/log text` | 자유 입력 → AI가 양식으로 변환 → 같은 확인 단계 | `/log Reno 2h OT on Jjuni last night` |
| `/week` | 다음 주 Schedule + TL 근무표 생성 | |
| `/plan [from] [to] [account]` | Planner 탭의 새 규칙을 Schedule에 반영 (미리보기 → Apply) | `/plan from:10-01 to:10-31` |
| `/plan account time/player [days] [slot] [ground] from to` | 한 줄 규칙을 바로 반영 (Planner 탭 없이) | `/plan account:ADA slot:1 days:Mon-Fri time:9am-5pm player:Cejay from:10-01 to:10-31` |

**한 달치 스케줄 입력 (Planner)** — 매니저가 `Planner` 탭에 규칙을 적고 `/plan` 실행:

| Account | Slot | Days | Time | Player | Hunting Ground | From | To |
|---|---|---|---|---|---|---|---|
| ADA | 1 | Mon-Fri | 9am-5pm | Cejay | | 2026-10-01 | 2026-10-31 |
| ADA | 1 | Weekends | 9am-5pm | Kim Carl | | 2026-10-01 | 2026-10-31 |
| Alex | 3 | Daily | 12am-8am | Raymond | Ivory Tower 4~5 | 2026-10-01 | 2026-10-31 |

- Days: `Daily`, `Weekdays`, `Weekends`, `Mon-Fri`, `Sat-Mon`, `Mon,Wed,Fri` · Time: `09:00-17:00`, `9am-5pm`, `12am-8am`(=24:00-08:00), `OFF`
- Player / Hunting Ground / Time 빈 칸 = 기존 값 유지 (예: 플레이어만 한 달 교체). 아래 줄이 위 줄을 덮어씀
- 미리보기에 표시: 바뀌는/새 시프트 수, 읽지 못한 줄(❌), 같은 플레이어 시간 겹침(⚠️), 정기점검과 겹쳐 빠지는 시간(⚙️)
- 적용된 줄은 Status에 `Applied …` → 다음 `/plan` 때 건너뜀 (`reapply:true` 로 다시 적용). 한 규칙 최대 62일

**속도 설계** — 디스코드는 3초 안에 응답해야 하고, 매니저가 기다리지 않아야 함:
- 스케줄 찾기는 AI가 아니라 **메모리 인덱스** (Accounts·Schedule 최근 3주~향후 2주·TL). 조회/자동완성 1ms 미만, 60초마다 백그라운드 갱신
- 이름·계정은 **자동완성**(입력하면서 후보 표시) + 별칭/유사도 매칭 → 오타에도 바로 찾음
- 시트 쓰기는 Confirm 즉시 "Saving…" 응답 후 백그라운드 (`--no-cpu-throttling`), 쓰기 전 행 재확인(누가 정렬해도 엉뚱한 행에 안 씀)
- AI는 `/log` 자유 입력에만 사용 (Claude API, effort low, JSON 스키마 고정 출력). 이름 매칭은 인덱스가 하므로 프롬프트가 짧음
- `--min-instances 1` 로 콜드 스타트 없음

**화면 흐름 (빠르게 쓰도록)**
- `/` 입력 → 명령 선택 → 이름 칸에 두세 글자 → 후보에 **그날 시프트가 같이 표시** (`Reno · ADA #1 09:00-17:00`) → 고르면 끝. 날짜 기본값 = 오늘
- 미리보기·확인 창은 **본인에게만 보임**, 저장 기록은 `#bot-log` 에 한 줄
- `/schedule` (인자 없음) = 오늘 전체 보드: **플레이어 빈 시프트가 맨 위**, 고객/농장/TL 순

**텔레그램 → 디스코드 → 시트 (한 번에)**
1. 영업자가 텔레그램에 평소처럼 요청 → AI가 해석 → 영업자 ✅ → **시트 즉시 반영**
2. `#sales-requests` 에 카드: 캐릭터 · 요청 영어 번역 · 시트 반영 결과 · 보낸 영업자 + 버튼
   - **✅ Confirm** (재접속 요청은 **Done**) → 영업자에게 "확정 완료" (한글)
   - **💬 Reply** → 입력창에 영어로 → 영업자에게 한글 번역으로 전달 (고객 질문은 **Answer** 한 개)
   - **📋 Schedule** → 그 캐릭터의 7일 스케줄 (본인만 보임)
   - 처리되면 카드가 회색으로 바뀌고 누가 처리했는지 표시 → 중복 처리 없음
3. 긴급(재접속) 카드는 `@Manager` 역할 멘션

**설정** — 새 서버: `tools/setup_discord.py` 가 역할(Manager)·채널(`#sales-requests` `#manager-desk` `#bot-log`)·명령·사용법 고정 메시지를 만들고 env.yaml 을 채움. 배포(`deploy.sh`) 때 Interactions Endpoint 자동 등록. 순서는 `WORK_ORDER.md` Phase 4.

## 용어 사전 (Glossary) & 학습 방법
`Glossary` 탭: Term / Variants(|구분) / KoreanFull / English / Category / Verified. 봇 시작 시 `data/glossary.csv`로 자동 생성.
파서·번역 프롬프트에 매번 주입되므로 **시트에 한 줄 추가 = 봇이 즉시 학습**.

**리니지 클래식 기본 용어**(사냥터·마을·클래스·아이템·은어 약 50개)는 `data/glossary.csv` 에 있고, 봇 기동 시 시트에 없는 용어만 Glossary 탭 뒤에 추가됨 (시트에서 고친 내용은 유지). Verified=N 은 추정 — 확인 후 Y로.

**시간 표기**: 파서가 `오후 8시~오전 4시` → `20:00-04:00`(끝이 시작보다 작으면 다음날), 자정 시작은 `24:00-08:00`. 확인 메시지에는 `20:00-04:00 (오후 8시~다음날 오전 4시)` 처럼 표시.

세 가지 학습 경로:
1. **자동 학습 루프** — 파싱 중 사전에 없는 은어가 나오면 봇이 영업자에게 "처음 보는 용어예요: '○○' 뜻을 답장해주세요" → 답장 `버림받은 땅 / Forsaken Land / 사냥터` → 사전 등록. 해당 메시지 처리는 멈추지 않고 계속 진행.
2. **수동 등록** — 텔레그램에서 `/learn 바람방 = 바람방 / Windroom / hunting_ground`
3. **일괄 학습** — `/learn_bulk` 뒤에 과거 카톡 대화 내보내기 텍스트를 붙여넣으면 AI가 용어 후보를 추출 → 검토 후 일괄 등록(Verified=N). `/glossary`로 미확인 목록 확인 후 시트에서 Verified=Y로 승격.

주의: Verified=N 용어는 프롬프트에 [미확인] 표시로 들어가 번역 시 신중히 사용됨. 영문 표기(예: 바람방=Windroom)는 트레이너들이 쓰는 표기로 통일.

## 작업 유형 (확장)
| 유형 | 트리거 예 | 시트 반영 | 알림 |
|---|---|---|---|
| HUNTING_GROUND | "돌 케릭 상아탑 6층 고정", "바람방 상아탑 왓다갓다" | Schedule `Hunting Ground` 열 | 디스코드 |
| SCHEDULE_LEDGER | "24. 수 9월 23일 : 10:00 ~ 18:00 -> 삭제 / 추가 목 …" | 시프트별 추가/OFF | 디스코드 |
| RELOGIN | "다시 로그인 부탁", "밀어냈어" | EventLog | 🔴 긴급 |
| QUESTION | "버땅심연이 경치 더 주나요?" | EventLog(open) | 매니저 답변 요청 → 답변 오면 영업자에게 전달 |
| INFO | "내일 문의 준다고함", "미리 나왔어요" | EventLog | 없음 |

캐릭터 식별: 고객 연락처명 `{서버}서버{캐릭}{고객명}` 패턴 자동 인식. 캐릭명 없는 메시지(장부 등)는 활성 캐릭터 버튼으로 재질문.
