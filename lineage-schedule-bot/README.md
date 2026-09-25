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
| NEW_CHARACTER | 캐릭명+클래스 구분 입력, MON~SUN 전 요일 시간 필수 | 신규 정의 |
| STOP | 해당 일자만 OFF | ❌ 불변 |
| PLAYER_SWAP | 플레이어명만 교체 | ❌ 불변 |
| EXTEND | 해당 일자·해당 시프트만 24:00-08:00 → 24:00-09:30 식 변경 | 해당 셀만 |

## 시트 구조
- `CharacterMaster` 탭: CanonicalName / Class / Server / Block / KoreanName / Aliases(|구분) / Status
  - 스케줄 없는 캐릭터는 Status=Inactive로 기록 보존, 주간 탭에서는 제외
- 주간 탭 `W{주차}_{일요일날짜}`: 봇이 자동 생성. Character | Class | Block | Shift | SUN~SAT
  - 셀값: `시간\n플레이어명` 또는 `OFF`
- 기존 수기 탭은 그대로 두고 읽기 전용 (봇 가동 주부터 표준 탭 사용)

## 설정 순서
1. **텔레그램 봇**: @BotFather → /newbot → 토큰 확보. 대표/영업자 chat_id는 @userinfobot으로 확인
2. **시트 준비**: `data/character_master.csv`를 `CharacterMaster` 탭으로 가져오기(파일 > 가져오기)
   - ⚠️ 계정 비밀번호 탭은 별도 시트로 분리 권장 (봇 서비스계정 접근 범위 밖으로)
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

## 매니저 컨펌 경로
- 디스코드 알림의 confirm 링크 클릭 → 봇이 텔레그램으로 영업자에게 확정 회신
- 고객 질문(QUESTION)은 알림의 answer 링크 뒤에 `&text=답변내용` 을 붙여 열면 영업자에게 한글로 전달됨

## 용어 사전 (Glossary) & 학습 방법
`Glossary` 탭: Term / Variants(|구분) / KoreanFull / English / Category / Verified. 봇 시작 시 `data/glossary.csv`로 자동 생성.
파서·번역 프롬프트에 매번 주입되므로 **시트에 한 줄 추가 = 봇이 즉시 학습**.

세 가지 학습 경로:
1. **자동 학습 루프** — 파싱 중 사전에 없는 은어가 나오면 봇이 영업자에게 "처음 보는 용어예요: '○○' 뜻을 답장해주세요" → 답장 `버림받은 땅 / Forsaken Land / 사냥터` → 사전 등록. 해당 메시지 처리는 멈추지 않고 계속 진행.
2. **수동 등록** — 텔레그램에서 `/learn 바람방 = 바람방 / Windroom / hunting_ground`
3. **일괄 학습** — `/learn_bulk` 뒤에 과거 카톡 대화 내보내기 텍스트를 붙여넣으면 AI가 용어 후보를 추출 → 검토 후 일괄 등록(Verified=N). `/glossary`로 미확인 목록 확인 후 시트에서 Verified=Y로 승격.

주의: Verified=N 용어는 프롬프트에 [미확인] 표시로 들어가 번역 시 신중히 사용됨. 영문 표기(예: 바람방=Windroom)는 트레이너들이 쓰는 표기로 통일.

## 작업 유형 (확장)
| 유형 | 트리거 예 | 시트 반영 | 알림 |
|---|---|---|---|
| HUNTING_GROUND | "돌 케릭 상아탑 6층 고정", "바람방 상아탑 왓다갓다" | 셀 3행 `@사냥터` | 디스코드 |
| SCHEDULE_LEDGER | "24. 수 9월 23일 : 10:00 ~ 18:00 -> 삭제 / 추가 목 …" | 시프트별 추가/OFF | 디스코드 |
| RELOGIN | "다시 로그인 부탁", "밀어냈어" | EventLog | 🔴 긴급 |
| QUESTION | "버땅심연이 경치 더 주나요?" | EventLog(open) | 매니저 답변 요청 → 답변 오면 영업자에게 전달 |
| INFO | "내일 문의 준다고함", "미리 나왔어요" | EventLog | 없음 |

캐릭터 식별: 고객 연락처명 `{서버}서버{캐릭}{고객명}` 패턴 자동 인식. 캐릭명 없는 메시지(장부 등)는 활성 캐릭터 버튼으로 재질문.
