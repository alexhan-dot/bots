"""v2 시트 레이아웃 정의 — 봇(services/sheets.py)과 이관 스크립트(tools/build_v2_sheet.py)가 공유"""

SCHEDULE_TAB = "Schedule"
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
BOARD_FIRST_ROW, BOARD_LAST_ROW = 5, 300


def schedule_formulas(last_row: int | None = None) -> dict:
    """Schedule 계산 열: 1행 = 헤더, 2행 = ARRAYFORMULA. 열 → (헤더, 수식)
    last_row=None → 열린 범위(A2:A, API로 쓸 때). xlsx 가져오기는 열린 범위를 못 읽으므로 숫자로 제한.
    {..} 배열 리터럴도 xlsx 가져오기에서 깨지므로 사용하지 않음."""
    n = "" if last_row is None else str(last_row)
    A, D, E, F, G, H = (f"{c}2:{c}{n}" for c in "ADEFGH")
    date = f'IF(ISNUMBER({A}),{A},IFERROR(DATEVALUE({A})))'
    start = f'(VALUE(REGEXEXTRACT({F},"^(\\d+):"))+VALUE(REGEXEXTRACT({F},"^\\d+:(\\d+)"))/60)'
    end = f'(VALUE(REGEXEXTRACT({F},"-(\\d+):"))+VALUE(REGEXEXTRACT({F},"-\\d+:(\\d+)"))/60)'
    return {
        "P": ("Hours", f'=ARRAYFORMULA(IF({A}="",,IF(({F}="OFF")+({G}=""),0,IFERROR(MOD({end}-{start},24),0))))'),
        "Q": ("Week", f'=ARRAYFORMULA(IF({A}="",,TEXT({date}-WEEKDAY({date})+1,"yyyy-mm-dd")))'),
        "R": ("Key", f'=ARRAYFORMULA(IF({A}="",,TEXT({date},"yyyy-mm-dd")&"|"&{D}&"|"&{E}))'),
        "S": ("Display", f'=ARRAYFORMULA(IF({A}="",,IF({F}="OFF","OFF",{F}&IF({G}="","",CHAR(10)&{G})&IF({H}="","",CHAR(10)&"@"&{H}))))'),
    }


def board_formulas(type_: str, last_row: int | None = None) -> dict:
    """Client/Farming Board 수식. 셀 → 수식. B2(주 시작일)는 사람이 바꿀 수 있어 여기 포함하지 않음"""
    n = "" if last_row is None else str(last_row)
    r0, rl = BOARD_FIRST_ROW, BOARD_LAST_ROW
    f = {"A5": (f'=IFERROR(SORT(UNIQUE(FILTER(Schedule!D2:E{n},Schedule!Q2:Q{n}=$B$2,'
                f'Schedule!C2:C{n}="{type_}")),1,TRUE,2,TRUE),"")')}
    for j in range(7):
        col = chr(ord("C") + j)
        f[f"{col}4"] = f'=TEXT(DATEVALUE($B$2)+{j},"yyyy-mm-dd")'
        f[f"{col}{r0}"] = (f'=ARRAYFORMULA(IF($A{r0}:$A{rl}="","",IFERROR(VLOOKUP({col}$4&"|"&$A{r0}:$A{rl}'
                           f'&"|"&$B{r0}:$B{rl},Schedule!$R:$S,2,FALSE),"")))')
    f[f"J{r0}"] = (f'=ARRAYFORMULA(IF($A{r0}:$A{rl}="","",SUMIFS(Schedule!$P:$P,Schedule!$Q:$Q,$B$2,'
                   f'Schedule!$D:$D,$A{r0}:$A{rl},Schedule!$E:$E,$B{r0}:$B{rl})))')
    return f
