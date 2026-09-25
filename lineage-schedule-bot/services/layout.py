"""v2 시트 레이아웃 정의 — 봇(services/sheets.py)과 이관 스크립트(tools/build_v2_sheet.py)가 공유"""

SCHEDULE_TAB = "Schedule"            # 오늘 + 앞으로의 시프트 (매일 날짜순 정렬, 오늘이 맨 위)
ARCHIVE_TAB = "Schedule Archive"     # 지난 시프트 (봇이 매일 Schedule 에서 옮김, 같은 열 구조)
ALL_TAB = "All Shifts"               # 숨김: Schedule + Archive 합본 (Board·Payroll 수식이 이 탭을 봄)
AS = f"'{ALL_TAB}'"                   # 수식 안에서 쓰는 탭 참조
TODAY_TAB = "Today"                  # 맨 앞 탭: 오늘·내일 시프트만 (OFF·플레이어 없는 파밍 제외)
ACCOUNTS_TAB = "Accounts"

# Schedule: 1행 = 계정 × 날짜 × 시프트(Slot). 봇은 A:O만 쓰고 P:S는 1행의 ARRAYFORMULA가 계산
SCHEDULE_COLS = ["Date", "Day", "Type", "Account", "Slot", "Time", "Player", "Hunting Ground",
                 "KPI", "Gold", "E-Red", "E-Green", "E-Purple", "Note", "Updated"]
METRIC_COLS = ["Player", "KPI", "Gold", "E-Red", "E-Green", "E-Purple", "Note"]   # 새 주로 복사하지 않는 칸

ACCOUNT_COLS = ["Account", "Type", "Class", "Server", "KoreanName", "Aliases", "Customer",
                "SalesRep", "Status", "StartDate", "Notes"]
TYPES = ("Client", "Farming")          # Client = 고객 계정, Farming = 농장 계정

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

BOARDS = {"Client Board": "Client", "Farming Board": "Farming"}

# Settings: 정기점검 (주 1회 게임 불가 시간 — Schedule Hours 에서 자동 차감)
SETTINGS_TAB = "Settings"
SETTINGS_ROWS = [["Setting", "Value", "Note"],
                 ["Maintenance Day", "Wed", "Weekly game maintenance day (Sun/Mon/…/Sat). Blank = none"],
                 ["Maintenance Start", "05:00", "Same clock as the Schedule times"],
                 ["Maintenance End", "09:00", "Shift hours inside this window are not counted"],
                 ["Daily Confirm Time", "07:00", "Discord #schedule-confirm: today's shifts & players to confirm"],
                 ["Weekly Confirm Day", "Sat", "Next week is created (players kept) and sent for confirmation"],
                 ["Weekly Confirm Time", "12:00", ""],
                 ["Reminder After (hours)", "3", "Remind managers once if not confirmed"]]

STAFF_TAB = "Staff"                  # 디스코드 계정 ↔ 스케줄 이름 (플레이어가 /iam 으로 연결)
STAFF_COLS = ["Name", "Discord ID", "Discord Name", "Role", "Linked At"]
REPORTS_TAB = "Shift Reports"        # 플레이어 시작·종료 스크린샷 → 레벨·EXP %·아데나
REPORT_COLS = ["Date", "Account", "Slot", "Shift Time", "Player",
               "Start At", "Start Lv", "Start EXP %", "Start Adena", "Start Shot",
               "End At", "End Lv", "End EXP %", "End Adena", "End Shot",
               "EXP Gained %", "Adena Gained", "Status", "Discord ID"]

CONFIRM_TAB = "Confirmations"
CONFIRM_COLS = ["Kind", "Period", "Status", "Posted At", "Confirmed By", "Confirmed At", "Summary"]

# Planner: 매니저가 기간 단위(한 달 등)로 반복 스케줄을 적는 탭 → 디스코드 /plan 이 Schedule 에 반영
PLANNER_TAB = "Planner"
PLANNER_COLS = ["Account", "Slot", "Days", "Time", "Player", "Hunting Ground", "From", "To", "Status"]
PLANNER_EXAMPLE = [["ADA", 1, "Mon-Fri", "09:00-17:00", "Cejay", "", "2026-10-01", "2026-10-31",
                    "EXAMPLE — delete this row. Blank Player/Ground = keep what's there. Days: Daily, Mon-Fri, "
                    "Weekends, Mon,Wed,Fri. Then run /plan in Discord."]]
BOARD_FIRST_ROW, BOARD_LAST_ROW = 5, 300


def _hm(x: str) -> str:
    """시각 셀("05:00" 문자열 또는 시간 값) → 시(小數)"""
    return f"(HOUR({x})+MINUTE({x})/60)"


def _vmin(a: str, b: str) -> str:            # MIN/MAX 는 ARRAYFORMULA 안에서 행별로 계산되지 않음 → IF 로
    return f"IF({a}<{b},{a},{b})"


def _vmax(a: str, b: str) -> str:
    return f"IF({a}>{b},{a},{b})"


def pay_week(date: str) -> str:
    """급여 주 = 그 날짜가 속한 주의 월요일 (급여는 월~일 2주 단위)"""
    return f'TEXT({date}-WEEKDAY({date},3),"yyyy-mm-dd")'


def schedule_formulas(last_row: int | None = None) -> dict:
    """Schedule 계산 열: 1행 = 헤더, 2행 = ARRAYFORMULA. 열 → (헤더, 수식)
    last_row=None → 열린 범위(A2:A, API로 쓸 때). xlsx 가져오기는 열린 범위를 못 읽으므로 숫자로 제한.
    {..} 배열 리터럴도 xlsx 가져오기에서 깨지므로 사용하지 않음.
    Maint(T) = 정기점검(Settings 탭)과 겹치는 시간 → Hours(P)에서 뺌. "24:00-08:00" 은 다음 날 0시 시작."""
    n = "" if last_row is None else str(last_row)
    A, D, E, F, G, H, T = (f"{c}2:{c}{n}" for c in "ADEFGHT")
    date = f'IF(ISNUMBER({A}),{A},IFERROR(DATEVALUE({A})))'
    start = f'(VALUE(REGEXEXTRACT({F},"^(\\d+):"))+VALUE(REGEXEXTRACT({F},"^\\d+:(\\d+)"))/60)'
    end = f'(VALUE(REGEXEXTRACT({F},"-(\\d+):"))+VALUE(REGEXEXTRACT({F},"-\\d+:(\\d+)"))/60)'
    length = f"MOD({end}-{start},24)"
    fin = f"({start}+{length})"
    st = f"'{SETTINGS_TAB}'"
    md = f'((FIND(LEFT({st}!$B$2,3),"SunMonTueWedThuFriSat")+2)/3)'       # 점검 요일 1=일 … 7=토
    ms, me = _hm(f"{st}!$B$3"), _hm(f"{st}!$B$4")
    clip = lambda x: f"IF({x}>0,{x},0)"
    same = clip(f"{_vmin(fin, me)}-{_vmax(start, ms)}")                   # 같은 날 점검
    nxt = clip(f"{_vmin(fin, f'(24+{me})')}-{_vmax(start, f'(24+{ms})')}")  # 자정 넘어 다음 날 점검
    maint = (f"IFERROR(IF(WEEKDAY({date})={md},{same},0)+IF(WEEKDAY({date}+1)={md},{nxt},0),0)")
    return {
        "P": ("Hours", f'=ARRAYFORMULA(IF({A}="",,IF(({F}="OFF")+({G}=""),0,IFERROR({length}-{T},0))))'),
        "Q": ("Week", f'=ARRAYFORMULA(IF({A}="",,TEXT({date}-WEEKDAY({date})+1,"yyyy-mm-dd")))'),
        "R": ("Key", f'=ARRAYFORMULA(IF({A}="",,TEXT({date},"yyyy-mm-dd")&"|"&{D}&"|"&{E}))'),
        "S": ("Display", f'=ARRAYFORMULA(IF({A}="",,IF({F}="OFF","OFF",{F}&IF({G}="","",CHAR(10)&{G})&IF({H}="","",CHAR(10)&"@"&{H})'
                         f'&IF({T}>0,CHAR(10)&"⚙ maint -"&{T}&"h",""))))'),
        "T": ("Maint Hrs", f'=ARRAYFORMULA(IF({A}="",,IF({F}="OFF",0,IFERROR({maint},0))))'),
        "U": ("Pay Week", f'=ARRAYFORMULA(IF({A}="",,{pay_week(date)}))'),
    }


def board_formulas(type_: str, last_row: int | None = None) -> dict:
    """Client/Farming Board 수식. 셀 → 수식. B2(주 시작일)는 사람이 바꿀 수 있어 여기 포함하지 않음"""
    n = "" if last_row is None else str(last_row)
    r0, rl = BOARD_FIRST_ROW, BOARD_LAST_ROW
    # 그 주에 보일 시프트가 하나라도 있는 계정·슬롯만 (OFF 만 있는 행, 플레이어 없는 파밍 행은 숨김)
    f = {"A5": (f'=IFERROR(SORT(UNIQUE(FILTER({AS}!D2:E{n},{AS}!Q2:Q{n}=$B$2,'
                f'{AS}!C2:C{n}="{type_}",{AS}!F2:F{n}<>"OFF",'
                f'({AS}!C2:C{n}="Client")+({AS}!G2:G{n}<>""))),1,TRUE,2,TRUE),"")')}
    for j in range(7):
        col = chr(ord("C") + j)
        f[f"{col}4"] = f'=TEXT(DATEVALUE($B$2)+{j},"yyyy-mm-dd")'
        v = f'IFERROR(VLOOKUP({col}$4&"|"&$A{r0}:$A{rl}&"|"&$B{r0}:$B{rl},{AS}!$R:$S,2,FALSE),"")'
        f[f"{col}{r0}"] = f'=ARRAYFORMULA(IF($A{r0}:$A{rl}="","",IF({v}="OFF","",{v})))'   # OFF 는 빈칸
    for r in range(r0, rl + 1):              # SUMIFS 는 ARRAYFORMULA 안에서 xlsx 가져오기 시 첫 값만 계산 → 행마다
        f[f"J{r}"] = (f'=IF($A{r}="","",SUMIFS({AS}!$P:$P,{AS}!$Q:$Q,$B$2,'
                      f'{AS}!$D:$D,$A{r},{AS}!$E:$E,$B{r}))')
    return f


def all_shifts_formula() -> str:
    """All Shifts!A2 — Schedule + Archive 를 한 목록으로 (계산 열 P:U 포함)"""
    return (f"=IFERROR(FILTER(VSTACK({SCHEDULE_TAB}!A2:U,'{ARCHIVE_TAB}'!A2:U),"
            f"VSTACK({SCHEDULE_TAB}!A2:A,'{ARCHIVE_TAB}'!A2:A)<>\"\"),\"\")")


TODAY_COLS = ["Time", "Account", "Type", "Slot", "Player", "Hunting Ground", "KPI", "Adena", "Note"]


def today_formulas() -> dict:
    """Today 탭: A = 오늘, K = 내일. OFF·플레이어 없는 파밍 제외, 시간순"""
    def block(offset: int) -> str:
        S = SCHEDULE_TAB
        day = f'TEXT(TODAY()+{offset},"yyyy-mm-dd")'
        cols = ",".join(f"{S}!{c}2:{c}" for c in "FDCEGHIJN")
        return (f'=IFERROR(SORT(FILTER({{{cols}}},TEXT({S}!A2:A,"yyyy-mm-dd")={day},{S}!F2:F<>"OFF",'
                f'({S}!C2:C="Client")+({S}!G2:G<>"")),1,TRUE,2,TRUE),"— no shifts —")')
    return {
        "A1": '="📋 TODAY  "&TEXT(TODAY(),"yyyy-mm-dd (ddd)")',
        "K1": '="NEXT  "&TEXT(TODAY()+1,"yyyy-mm-dd (ddd)")',
        "A2": '="⚠️ No player (client): "&IFERROR(COUNTIFS(Schedule!A2:A,TEXT(TODAY(),"yyyy-mm-dd"),Schedule!F2:F,"<>OFF",'
              'Schedule!C2:C,"Client",Schedule!G2:G,""),0)',
        "A4": block(0), "K4": block(1),
    }


# ── TL(팀 리더) 근무표 ─────────────────────────
TL_TAB, TL_BOARD = "TL Schedule", "TL Board"
TL_COLS = ["Date", "Day", "Leader", "Shift", "Attendance", "Note"]   # A:F 입력 (OT는 Overtime 탭)


def tl_formulas(last_row: int | None = None) -> dict:
    """TL Schedule 계산 열 G:I (1행 헤더, 2행 ARRAYFORMULA)"""
    n = "" if last_row is None else str(last_row)
    A, C, D, E = (f"{c}2:{c}{n}" for c in "ACDE")
    date = f'IF(ISNUMBER({A}),{A},IFERROR(DATEVALUE({A})))'
    return {
        "G": ("Week", f'=ARRAYFORMULA(IF({A}="",,TEXT({date}-WEEKDAY({date})+1,"yyyy-mm-dd")))'),
        "H": ("Key", f'=ARRAYFORMULA(IF({A}="",,TEXT({date},"yyyy-mm-dd")&"|"&{C}))'),
        "I": ("Display", f'=ARRAYFORMULA(IF({A}="",,{D}&IF({E}="","",CHAR(10)&{E})))'),
        "J": ("Hours", tl_hours(D, E, A)),
        "K": ("Pay Week", f'=ARRAYFORMULA(IF({A}="",,{pay_week(date)}))'),
    }


def tl_hours(D: str, E: str, A: str) -> str:
    """TL 근무시간: "8am-4pm" / "10pm-4am" / "12am-8am" → 시간. OFF·CANCEL OFF 등 못 읽는 값, 결근(Absent)은 0"""
    ok = f'REGEXMATCH({D},"^\\s*\\d{{1,2}}(:\\d\\d)?\\s*[aApP][mM]\\s*-\\s*\\d{{1,2}}(:\\d\\d)?\\s*[aApP][mM]")'
    start = (f'(MOD(VALUE(REGEXEXTRACT({D},"^\\s*(\\d{{1,2}})")),12)'
             f'+12*REGEXMATCH({D},"^\\s*\\d{{1,2}}(:\\d\\d)?\\s*[pP][mM]"))')
    end = (f'(MOD(VALUE(REGEXEXTRACT({D},"-\\s*(\\d{{1,2}})")),12)'
           f'+12*REGEXMATCH({D},"-\\s*\\d{{1,2}}(:\\d\\d)?\\s*[pP][mM]"))')
    return (f'=ARRAYFORMULA(IF({A}="",,IF(({ok}=FALSE)+(LOWER({E})="absent"),0,'
            f'IFERROR(MOD({end}-{start},24),0))))')


def tl_board_formulas(last_row: int | None = None) -> dict:
    n = "" if last_row is None else str(last_row)
    r0, rl = BOARD_FIRST_ROW, BOARD_LAST_ROW
    t = f"'{TL_TAB}'"
    f = {"A5": f'=IFERROR(SORT(UNIQUE(FILTER({t}!C2:C{n},{t}!I2:I{n}=$B$2))),"")'}
    for j in range(7):
        col = chr(ord("B") + j)
        f[f"{col}4"] = f'=TEXT(DATEVALUE($B$2)+{j},"yyyy-mm-dd")'
        f[f"{col}{r0}"] = (f'=ARRAYFORMULA(IF($A{r0}:$A{rl}="","",IFERROR(VLOOKUP({col}$4&"|"&$A{r0}:$A{rl},'
                           f'{t}!$H:$I,2,FALSE),"")))')
    # OT 합계: Overtime 탭에서 해당 주(M열 Week) 리더 이름으로 합산
    for r in range(r0, rl + 1):
        f[f"I{r}"] = f'=IF($A{r}="","",SUMIFS(Overtime!$H:$H,Overtime!$B:$B,$A{r},Overtime!$M:$M,$B$2))'
    return f


# ── Payroll: 2주 단위 급여 집계 (Schedule·TL 근무 + OT − 페널티, 인센티브 합계) ──
PAYROLL_TAB, PAYROLL_HISTORY_TAB = "Payroll", "Payroll History"
PAYROLL_ROWS = 200
PAY_ANCHOR = "2026-09-07"      # 급여 기간 기준 월요일. 급여 = 월~일 × 2주 (9/7-9/20, 9/21-10/4, …). Payroll!G2 에서 변경 가능
PAYROLL_NOTE = ("← B2 = pay period start (a Monday; default = current period). Pay period = 2 weeks, Mon–Sun. "
                "C2 = week 2 start, D2 = period end, G2 = anchor Monday of any pay period.")
PAYROLL_HEADERS = ["Staff", "Week 1 Hrs", "Week 2 Hrs", "Base Hrs", "OT Hrs", "Penalty Hrs",
                   "Payable Hrs", "Incentives", "Shifts", "Characters"]


def payroll_formulas(last_row: int | None = None) -> dict:
    """Payroll 탭 수식. B2 = 기간 시작(월요일, 기본: 오늘이 속한 기간), C2 = 2주차 시작, D2 = 기간 끝, G2 = 기준일.
    A열 직원 목록 = 기간 내 근무(플레이어·TL) / OT / 인센티브 / 페널티 기록이 있는 사람 전부."""
    n = "" if last_row is None else str(last_row)
    r0 = BOARD_FIRST_ROW; rl = r0 + PAYROLL_ROWS - 1
    tl, dp = f"'{TL_TAB}'", f"'{PENALTY_TAB}'"
    in_period = lambda col: f"(({col}=$B$2)+({col}=$C$2))"          # Pay Week 열(월요일)이 1주차 또는 2주차

    def both(fn):                                                  # 1주차 + 2주차 합
        return f"{fn('$B$2')}+{fn('$C$2')}"

    names = ",".join([
        f"IFERROR(FILTER({AS}!G2:G{n},{AS}!P2:P{n}>0,{in_period(f'{AS}!U2:U{n}')}))",
        f"IFERROR(FILTER({tl}!C2:C{n},{tl}!J2:J{n}>0,{in_period(f'{tl}!K2:K{n}')}))",
        f"IFERROR(FILTER({OVERTIME_TAB}!B2:B{n},{in_period(f'{OVERTIME_TAB}!N2:N{n}')}))",
        f"IFERROR(FILTER({INCENTIVE_TAB}!B2:B{n},{in_period(f'{INCENTIVE_TAB}!N2:N{n}')}))",
        f"IFERROR(FILTER({dp}!B2:B{n},{in_period(f'{dp}!L2:L{n}')}))",
    ])
    f = {
        "B2": f'=TEXT(DATEVALUE($G$2)+14*FLOOR((TODAY()-DATEVALUE($G$2))/14),"yyyy-mm-dd")',
        "C2": '=TEXT(DATEVALUE($B$2)+7,"yyyy-mm-dd")',
        "D2": '=TEXT(DATEVALUE($B$2)+13,"yyyy-mm-dd")',
        f"A{r0}": f'=IFERROR(SORT(UNIQUE(QUERY(FLATTEN({names}),"select Col1 where Col1 is not null and Col1 <> \'\'",0))),"")',
    }
    # 합계 열은 행마다 (SUMIFS/COUNTIFS 는 ARRAYFORMULA 안에서 xlsx 가져오기 시 첫 값만 계산됨)
    for r in range(r0, rl + 1):
        a = f"$A{r}"
        hours = lambda w: (f"SUMIFS({AS}!$P:$P,{AS}!$G:$G,{a},{AS}!$U:$U,{w})"
                           f"+SUMIFS({tl}!$J:$J,{tl}!$C:$C,{a},{tl}!$K:$K,{w})")
        row = lambda expr: f'=IF({a}="","",{expr})'
        f[f"B{r}"] = row(hours("$B$2"))
        f[f"C{r}"] = row(hours("$C$2"))
        f[f"D{r}"] = row(f"B{r}+C{r}")
        f[f"E{r}"] = row(both(lambda w: f"SUMIFS({OVERTIME_TAB}!$H:$H,{OVERTIME_TAB}!$B:$B,{a},{OVERTIME_TAB}!$N:$N,{w})"))
        f[f"F{r}"] = row(both(lambda w: f"SUMIFS({dp}!$E:$E,{dp}!$B:$B,{a},{dp}!$L:$L,{w})"))
        f[f"G{r}"] = row(f"D{r}+E{r}-F{r}")
        f[f"H{r}"] = row(both(lambda w: f"SUMIFS({INCENTIVE_TAB}!$H:$H,{INCENTIVE_TAB}!$B:$B,{a},{INCENTIVE_TAB}!$N:$N,{w})"))
        f[f"I{r}"] = row(both(lambda w: f'COUNTIFS({AS}!$G:$G,{a},{AS}!$U:$U,{w},{AS}!$P:$P,">0")'
                                        f'+COUNTIFS({tl}!$C:$C,{a},{tl}!$K:$K,{w},{tl}!$J:$J,">0")'))
    for r in range(r0, rl + 1):                                    # 담당 캐릭터 (TEXTJOIN 은 행마다)
        f[f"J{r}"] = (f'=IF($A{r}="","",IFERROR(TEXTJOIN(", ",TRUE,UNIQUE(FILTER({AS}!$D$2:$D{n},'
                      f'{AS}!$G$2:$G{n}=$A{r},{AS}!$P$2:$P{n}>0,{in_period(f"{AS}!$U$2:$U{n}")}))),""))')
    return f


# ── 매니저 기록 (디스코드 봇이 입력) ───────────
OVERTIME_TAB = "Overtime"
OVERTIME_COLS = ["Date", "Staff", "Role", "Account", "Slot", "Scheduled Time", "OT Time", "OT Hours",
                 "Reason", "Manager", "Logged At", "Source"]
INCENTIVE_TAB = "Incentives"
INCENTIVE_COLS = ["Date", "Staff", "Role", "Account", "Class", "Level", "KPI", "Amount",
                  "Reason", "Manager", "Logged At", "Source"]
PENALTY_TAB = "Death Penalty"
PENALTY_COLS = ["Date", "Player", "Character", "Shift", "Penalty Hours", "Action", "IR",
                "Manager", "Logged At", "Source"]
PERF_PAY_TAB = "Performance Pay"      # 기준표 (사람이 관리, 봇은 읽기만)
LOG_TABS = {OVERTIME_TAB: OVERTIME_COLS, INCENTIVE_TAB: INCENTIVE_COLS, PENALTY_TAB: PENALTY_COLS}


def week_formula(last_row: int | None = None) -> str:
    """로그 탭(Overtime 등) M열: Date → 주 시작 일요일 (1행 헤더, 2행 수식)"""
    n = "" if last_row is None else str(last_row)
    A = f"A2:A{n}"
    date = f'IF(ISNUMBER({A}),{A},IFERROR(DATEVALUE({A})))'
    return f'=ARRAYFORMULA(IF({A}="",,TEXT({date}-WEEKDAY({date})+1,"yyyy-mm-dd")))'


def pay_week_formula(last_row: int | None = None) -> str:
    """로그 탭 Week 다음 열: Date → 급여 주(월요일)"""
    n = "" if last_row is None else str(last_row)
    A = f"A2:A{n}"
    date = f'IF(ISNUMBER({A}),{A},IFERROR(DATEVALUE({A})))'
    return f'=ARRAYFORMULA(IF({A}="",,{pay_week(date)}))'


# xlsx 가져오기는 SORT/UNIQUE/FILTER 같은 목록 수식을 계산하지 못함 → xlsx 에는 목록을 값으로 넣고,
# 봇이 기동할 때 이 범위를 비운 뒤 A5 에 동적 수식을 씀 (새 직원·계정 자동 반영)
SPILL_SEEDS = {"Client Board": "A5:B300", "Farming Board": "A5:B300", TL_BOARD: "A5:A300",   # B5 값도 지워야 A5 가 2열로 펼쳐짐
               PAYROLL_TAB: f"A6:A{BOARD_FIRST_ROW + PAYROLL_ROWS - 1}"}
