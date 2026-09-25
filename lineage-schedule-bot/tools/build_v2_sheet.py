"""기존 수기 시트(TargetWeekNN 탭) → v2 구조 xlsx 생성 (Google Drive 업로드 시 Google Sheets로 변환)

v2 구조
  Client Board / Farming Board : 주간 그리드 보기 (수식, 편집 X) — B2에 주 시작일(일요일) 입력
  Schedule   : 원장. 1행 = 계정 × 날짜 × 시프트(Slot). 봇은 A:O만 쓰고 P:S는 ARRAYFORMULA
  Accounts   : 계정 마스터. Type = Client(고객) / Farming(농장) 으로 명확히 구분
  Glossary / EventLog

사용: python tools/build_v2_sheet.py 원본.xlsx "TargetWeek39(9.20~9.26.)" 출력.xlsx [--no-schedule-data]
  → data/schedule_seed.csv, data/accounts_seed.csv 도 함께 갱신
"""
import csv, datetime, os, re, sys
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
sys.path.insert(0, os.path.join(HERE, ".."))
from services.layout import (SCHEDULE_COLS, ACCOUNT_COLS, DAYS, schedule_formulas, board_formulas,  # noqa: E402
                             TL_TAB, TL_BOARD, TL_COLS, tl_formulas, tl_board_formulas,
                             PAYROLL_TAB, PAYROLL_HISTORY_TAB, payroll_formulas, week_formula, pay_week_formula,
                             SETTINGS_TAB, SETTINGS_ROWS, PLANNER_TAB, PLANNER_COLS, PLANNER_EXAMPLE, PAYROLL_NOTE,
                             PAYROLL_HEADERS, PAY_ANCHOR,
                             OVERTIME_TAB, OVERTIME_COLS, INCENTIVE_TAB, INCENTIVE_COLS,
                             PENALTY_TAB, PENALTY_COLS, PERF_PAY_TAB)
XLSX_LAST_ROW = 50000   # xlsx 가져오기용 범위 제한 (봇이 기동 시 열린 범위로 다시 씀)

# 시트 블록 헤더 → (정식명, 클래스). 원본 표기는 별칭으로 보존
HEADER_MAP = {
    "Road- Best Players Only!": ("Road", ""), "Zombie-Knight": ("Zombie", "Knight"),
    "Perez-Spatoy": ("Perez", ""), "Beongaebul": ("Beongaebul", ""), "ADA-Knight": ("ADA", "Knight"),
    "Dalo-Knight": ("Dalo", "Knight"), "Alex-Knight": ("Alex", "Knight"),
    "Power20-Knight": ("Power20", "Knight"), "Jjuni-Knight": ("Jjuni", "Knight"),
    "Return-Knight": ("Return", "Knight"), "Munjin-Knight": ("Munjin", "Knight"),
    "Geonbae-Elf": ("Geonbae", "Elf"), "Dol-Knight": ("Dol", "Knight"),
    "Neburegi-Wizzard Heal": ("Neburegi", "Wizard"), "Doma-Knight": ("Doma", "Knight"),
    "Elf-Elf": ("Elf", "Elf"), "Real Madrid-Knight": ("Real Madrid", "Knight"),
    "Mukkbo-Knight": ("Mukkbo", "Knight"), "Olleh-knight": ("Olleh", "Knight"),
    "Heon-Knight": ("Heon", "Knight"), "Tiger-Knight": ("Tiger", "Knight"),
    "Lolly Elf": ("Lolly", "Elf"), "yeoseo-elf": ("Yeoseo", "Elf"),
    "Captain - Lord (Prince)": ("Captain", "Prince"), "Dodam-Elf": ("Dodam", "Elf"),
    "Lammy Queen - Elf": ("Lammy Queen", "Elf"), "Goop - Wizard": ("Goop", "Wizard"),
    "Duga - Knight": ("Duga", "Knight"), "Jo-Fairy": ("Jo", "Elf"), "Ssen": ("SSEN", ""),
    "Kyoryu(Elf)": ("Kyoryu", "Elf"), "Ronaldo": ("Ronaldo", ""), "Marcelo": ("Marcelo", ""),
    "Sarim": ("Sarim", ""), "Taejo": ("Taejo", ""), "Pele(Chaerin)": ("Pele", ""),
    "Sudden - FIREBAT (Wizard)": ("Sudden", "Wizard"), "Kangaroo (Wizard)": ("Kangaroo", "Wizard"),
    "OldBlood (Wizard)": ("OldBlood", "Wizard"), "Sinsang(Magician)": ("Sinsang", "Wizard"),
    "Sunsang (Wizard)": ("Sunsang", "Wizard"),
}
SKIP_HEADERS = {"Master Player extra hours"}   # 계정 아님 (마스터 플레이어 추가근무 기록)
METRIC_ROWS = {"player name": "Player", "trainer name": "Player", "hunting ground": "Hunting Ground",
               "target kpi": "KPI", "gold accumulated": "Gold", "e-red used": "E-Red",
               "e-green used": "E-Green", "e-purple used": "E-Purple"}
TIME_RE = re.compile(r"^\s*(\d{1,2}):?(\d{2})0?\s*[-~]\s*(\d{1,2}):?(\d{2})0?\s*$")


def norm_time(v) -> str:
    """'16:000-24:00' / '8:00 ~ 16:00' → '16:00-24:00'. OFF/빈칸 유지"""
    if v is None: return ""
    s = str(v).strip()
    if not s: return ""
    if s.upper().startswith("OFF") or "CANCEL" in s.upper(): return "OFF"
    m = TIME_RE.match(s)
    return f"{int(m[1]):02d}:{m[2]}-{int(m[3]):02d}:{m[4]}" if m else s


def cell_str(v) -> str:
    if v is None: return ""
    if isinstance(v, float) and v.is_integer(): v = int(v)
    s = str(v).strip()
    return "" if s.startswith("=") else s


def parse_week(ws, date_cols_client=range(3, 10), date_cols_farm=range(13, 20)):
    """반환: accounts {name: {...}}, rows [schedule row dict]"""
    dates = [ws.cell(3, c).value for c in date_cols_client]
    dates = [d.date() if isinstance(d, datetime.datetime) else datetime.date.fromisoformat(str(d)[:10]) for d in dates]
    headers = sorted((m.min_row, m.min_col) for m in ws.merged_cells.ranges
                     if m.max_col - m.min_col >= 7 and m.min_row > 5)
    accounts, rows = {}, []
    for block_type, name_col, cols in (("Client", 1, date_cols_client), ("Farming", 11, date_cols_farm)):
        starts = [r for r, c in headers if c == name_col]
        for i, hr in enumerate(starts):
            raw = (ws.cell(hr, name_col).value or "").strip()
            if not raw or raw in SKIP_HEADERS: continue
            name, cls = HEADER_MAP.get(raw, (raw, ""))
            acc = accounts.setdefault(name, {"Account": name, "Type": block_type, "Class": cls,
                                             "Aliases": set(), "slots": 0})
            if raw != name: acc["Aliases"].add(raw)
            end = starts[i + 1] if i + 1 < len(starts) else ws.max_row + 1
            cur = None
            for r in range(hr + 1, end):
                label = cell_str(ws.cell(r, name_col).value)
                if TIME_RE.match(label or ""):                  # 시프트 시작 행
                    acc["slots"] += 1
                    cur = [{"Date": d.isoformat(), "Day": DAYS[j], "Type": block_type, "Account": name,
                            "Slot": acc["slots"], "Time": norm_time(ws.cell(r, c).value) or "OFF"}
                           for j, (d, c) in enumerate(zip(dates, cols))]
                    rows.extend(cur)
                elif cur and label.lower() in METRIC_ROWS:
                    key = METRIC_ROWS[label.lower()]
                    for j, c in enumerate(cols):
                        v = ws.cell(r, c).value
                        cur[j][key] = v if isinstance(v, (int, float)) and key != "Player" else cell_str(v)
    return accounts, rows, dates


DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _hours(v):
    """'4HOURS' / '1 Hour' / '3 hours' / 1.0 → 숫자. 못 읽으면 None"""
    if isinstance(v, (int, float)): return v
    m = re.search(r"\d+(\.\d+)?", str(v or ""))
    return float(m[0]) if m and "-" != str(v).strip() else None


def parse_tl(wb) -> tuple[list[dict], list[dict]]:
    """모든 TargetWeekNN 탭의 'Team Leader Schedule' 구역(AE~) → (TL Schedule 행, Overtime 행)"""
    out, ots = [], []
    for ws in wb.worksheets:
        if not ws.title.startswith("TargetWeek") or not str(ws.cell(5, 31).value or "").startswith("Team Leader"):
            continue
        days = [c for c in range(32, ws.max_column + 1) if str(ws.cell(5, c).value or "").strip() in DAY_NAMES]
        has_ot = str(ws.cell(5, days[0] + 2).value or "").upper().startswith("FOR OT")
        r = 6
        while ws.cell(r, 31).value not in (None, ""):
            leader = str(ws.cell(r, 31).value).strip()
            for j, c in enumerate(days):
                d = ws.cell(4, c).value
                d = d.date() if isinstance(d, datetime.datetime) else datetime.date.fromisoformat(str(d)[:10])
                row = {"Date": d.isoformat(), "Day": DAYS[j], "Leader": leader,
                       "Shift": cell_str(ws.cell(r, c).value), "Attendance": cell_str(ws.cell(r, c + 1).value)}
                if has_ot:
                    ot, hrs = cell_str(ws.cell(r, c + 2).value), ws.cell(r, c + 3).value
                    if ot not in ("", "-") or _hours(hrs):
                        ots.append({"Date": row["Date"], "Staff": leader, "Role": "TL",
                                    "Scheduled Time": row["Shift"], "OT Time": ot if ot != "-" else "",
                                    "OT Hours": _hours(hrs), "Reason": "" if _hours(hrs) else cell_str(hrs),
                                    "Source": f"migrated:{ws.title}"})
                if any(row.get(k) not in ("", None, "-") for k in ("Shift", "Attendance")):
                    out.append(row)
            r += 1
    out.sort(key=lambda x: (x["Date"], x["Leader"]))
    ots.sort(key=lambda x: (x["Date"], x["Staff"]))
    return out, ots


DATE_TYPOS = {"2026-06030": "2026-06-30", "2026-22-16": "2026-08-16"}


def parse_penalties(wb) -> list[dict]:
    """'Death Penalty Week NN & MM' 탭들 → Death Penalty 행 (빈 번호 행 제외, 탭 간 중복 제거)"""
    out, seen = [], set()
    for ws in wb.worksheets:
        if "Death Penalty" not in ws.title: continue
        for r in range(4, ws.max_row + 1):
            player = cell_str(ws.cell(r, 3).value)
            if not player: continue
            d = ws.cell(r, 5).value
            d = d.date().isoformat() if isinstance(d, datetime.datetime) else cell_str(d)
            action = cell_str(ws.cell(r, 8).value)
            if d in DATE_TYPOS:                                # 원본 시트 오타 → 추정 날짜 (원본값은 Action 에 남김)
                action = f"{action} (orig date: {d})".strip(); d = DATE_TYPOS[d]
            row = {"Date": d, "Player": player, "Character": cell_str(ws.cell(r, 4).value),
                   "Shift": cell_str(ws.cell(r, 6).value), "Penalty Hours": _hours(ws.cell(r, 7).value),
                   "Action": action,
                   "IR": "" if cell_str(ws.cell(r, 9).value) == "Comment" else cell_str(ws.cell(r, 9).value),
                   "Source": f"migrated:{ws.title.strip()}"}
            key = (row["Date"], row["Player"], row["Character"], row["Shift"], r)
            dup = (row["Date"], row["Player"], row["Character"], row["Shift"])
            # 같은 사람·날짜·시프트가 여러 탭(겹치는 주)에 반복되면 한 번만
            if any(k[:4] == dup and k[5] != ws.title for k in seen): continue
            seen.add(key + (ws.title,)); out.append(row)
    out.sort(key=lambda x: (x["Date"], x["Player"]))
    return out


def parse_payroll(wb) -> list[dict]:
    """'WeekNN Payroll' 탭들 → Payroll History 행 (요약 블록 제외)"""
    out = []
    for ws in wb.worksheets:
        m = re.match(r"Week(\d+) Payroll", ws.title)
        if not m: continue
        for r in range(2, ws.max_row + 1):
            name = ws.cell(r, 1).value
            if name in (None, ""): break                       # 첫 빈 줄 이후는 요약
            out.append({"Week": f"W{m[1]}", "Player": str(name).strip(),
                        "Total Hours": round(float(ws.cell(r, 2).value or 0), 2),
                        "Shifts": int(ws.cell(r, 3).value or 0), "Characters": cell_str(ws.cell(r, 4).value)})
    return out


def next_week_rows(rows):
    """다음 주 자동 생성 규칙과 동일: 시간·사냥터만 복사, 플레이어·실적은 비움"""
    out = []
    for r in rows:
        d = datetime.date.fromisoformat(r["Date"]) + datetime.timedelta(days=7)
        out.append({"Date": d.isoformat(), "Day": r["Day"], "Type": r["Type"], "Account": r["Account"],
                    "Slot": r["Slot"], "Time": r["Time"], "Hunting Ground": r.get("Hunting Ground", "")})
    return out


HDR_FILL = PatternFill("solid", fgColor="1F3864"); HDR_FONT = Font(color="FFFFFF", bold=True)
CLIENT_FILL = PatternFill("solid", fgColor="DDEBF7"); FARM_FILL = PatternFill("solid", fgColor="E2EFDA")
OFF_FILL = PatternFill("solid", fgColor="EDEDED")


def header(ws, cols, row=1):
    for i, h in enumerate(cols, 1):
        c = ws.cell(row, i, h); c.fill = HDR_FILL; c.font = HDR_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")


def build(src_path, tab, out_path, with_schedule=True):
    src = openpyxl.load_workbook(src_path)
    accounts, rows, dates = parse_week(src[tab])
    rows += next_week_rows(rows)

    # 기존 마스터 CSV와 병합 (한글명·서버·별칭 유지, 이번 주 시트에 없는 계정은 Inactive)
    with open(os.path.join(DATA, "character_master.csv"), encoding="utf-8-sig") as f:
        master = {r["CanonicalName"]: r for r in csv.DictReader(f)}
    conflicts = []
    for name, m in master.items():
        a = accounts.get(name)
        if a is None:
            accounts[name] = {"Account": name, "Type": m["Block"], "Class": m["Class"],
                              "Aliases": set(filter(None, m["Aliases"].split("|"))),
                              "Server": m["Server"], "KoreanName": m["KoreanName"], "Status": "Inactive"}
            continue
        a["Aliases"] |= set(filter(None, m["Aliases"].split("|")))
        a["Server"] = m["Server"]; a["KoreanName"] = m["KoreanName"]
        a["Class"] = a["Class"] or m["Class"]
        if m["Block"] != a["Type"]:
            conflicts.append(f"{name}: 마스터={m['Block']} / 시트 배치={a['Type']}")

    wb = openpyxl.Workbook()
    readme = wb.active; readme.title = "README"
    client_b = wb.create_sheet("Client Board"); farm_b = wb.create_sheet("Farming Board")
    sch = wb.create_sheet("Schedule"); acc = wb.create_sheet("Accounts")
    glo = wb.create_sheet("Glossary"); ev = wb.create_sheet("EventLog")

    # ── Schedule ──
    header(sch, SCHEDULE_COLS)
    rows.sort(key=lambda r: (r["Date"], r["Type"] != "Client", r["Account"].lower(), r["Slot"]))
    with open(os.path.join(DATA, "schedule_seed.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(SCHEDULE_COLS)
        w.writerows([r.get(c, "") for c in SCHEDULE_COLS] for r in rows)
    # xlsx에는 이관 주만 (다음 주는 봇이 자동 생성). 비워두면 봇이 첫 기동 때 schedule_seed.csv로 채움
    for r in rows if with_schedule else []:
        if True:
            sch.append([r.get(c) if r.get(c) != "" else None for c in SCHEDULE_COLS])
    for col, (h, f) in schedule_formulas(XLSX_LAST_ROW).items():
        c = sch[f"{col}1"]; c.value = h; c.fill = PatternFill("solid", fgColor="7F7F7F"); c.font = HDR_FONT
        sch[f"{col}2"] = f
    sch.freeze_panes = "A2"; sch.auto_filter.ref = "A1:O1"
    for col, w in zip("ABCDEFGHIJKLMNOPQRS", [11, 5, 8, 16, 5, 12, 16, 18, 8, 9, 7, 7, 7, 20, 16, 6, 11, 28, 22]):
        sch.column_dimensions[col].width = w
    dv_type = DataValidation(type="list", formula1='"Client,Farming"', allow_blank=False); sch.add_data_validation(dv_type)
    dv_type.add("C2:C50000")

    # ── Accounts ──
    header(acc, ACCOUNT_COLS)
    order = sorted(accounts.values(), key=lambda a: (a["Type"] != "Client", a.get("Status") == "Inactive", a["Account"].lower()))
    for a in order:
        acc.append([a["Account"], a["Type"], a.get("Class", ""), a.get("Server", ""), a.get("KoreanName", ""),
                    "|".join(sorted(a["Aliases"])), "", "", a.get("Status", "Active"), "", ""])
    acc.freeze_panes = "B2"; acc.auto_filter.ref = f"A1:K{acc.max_row}"
    for col, w in zip("ABCDEFGHIJK", [16, 9, 9, 9, 12, 40, 14, 12, 9, 11, 30]):
        acc.column_dimensions[col].width = w
    dv_t = DataValidation(type="list", formula1='"Client,Farming"'); dv_s = DataValidation(type="list", formula1='"Active,Paused,Inactive"')
    acc.add_data_validation(dv_t); acc.add_data_validation(dv_s); dv_t.add("B2:B1000"); dv_s.add("I2:I1000")
    acc.conditional_formatting.add("A2:K1000", FormulaRule(formula=['$B2="Client"'], fill=CLIENT_FILL))
    acc.conditional_formatting.add("A2:K1000", FormulaRule(formula=['$B2="Farming"'], fill=FARM_FILL))

    # ── Boards ──
    for ws, typ, fill in ((client_b, "Client", CLIENT_FILL), (farm_b, "Farming", FARM_FILL)):
        ws["A1"] = f"{'CLIENT (고객)' if typ == 'Client' else 'FARMING (농장)'} ACCOUNTS — Weekly Board"
        ws["A1"].font = Font(bold=True, size=14)
        ws["A2"] = "Week start (Sun):"; ws["A2"].font = Font(bold=True)
        ws["B2"] = '=TEXT(TODAY()-WEEKDAY(TODAY())+1,"yyyy-mm-dd")'
        ws["B2"].fill = PatternFill("solid", fgColor="FFF2CC")
        ws["D2"] = "← 다른 주를 보려면 일요일 날짜(yyyy-mm-dd)를 직접 입력. 이 탭은 보기 전용 — 수정은 Schedule 탭에서."
        ws["D2"].font = Font(italic=True, color="7F7F7F")
        for i, h in enumerate(["Account", "Slot"] + DAYS + ["Hours"], 1):
            c = ws.cell(3, i, h); c.fill = HDR_FILL; c.font = HDR_FONT; c.alignment = Alignment(horizontal="center")
        ws.cell(4, 1, "date →").font = Font(italic=True, color="7F7F7F")
        for ref, f in board_formulas(typ, XLSX_LAST_ROW).items():
            ws[ref] = f
        # 목록(A:B)은 값으로 — xlsx 가져오기가 목록 수식을 계산하지 못함. 봇 기동 시 동적 수식으로 교체
        wk0 = dates[0].isoformat(); wk1 = dates[-1].isoformat()
        pairs = sorted({(r["Account"], r["Slot"]) for r in rows
                        if r["Type"] == typ and wk0 <= r["Date"] <= wk1}, key=lambda x: (x[0].lower(), x[1]))
        for i, (acc_name, slot) in enumerate(pairs):
            ws.cell(5 + i, 1, acc_name); ws.cell(5 + i, 2, slot)
        for j in range(7):
            ws.cell(4, 3 + j).alignment = Alignment(horizontal="center")
            ws.column_dimensions[openpyxl.utils.get_column_letter(3 + j)].width = 17
        ws.conditional_formatting.add("C5:I300", FormulaRule(formula=['C5="OFF"'], fill=OFF_FILL, font=Font(color="999999")))
        ws.conditional_formatting.add("A5:B300", FormulaRule(formula=['$A5<>""'], fill=fill, font=Font(bold=True)))
        ws.freeze_panes = "C5"
        ws.column_dimensions["A"].width = 16; ws.column_dimensions["B"].width = 5; ws.column_dimensions["J"].width = 7

    # ── TL Schedule / TL Board ──
    tl, ot_rows = parse_tl(src)
    last_week = max(r["Date"] for r in tl)
    lw_start = (datetime.date.fromisoformat(last_week) - datetime.timedelta(days=6)).isoformat()
    for r in [r for r in tl if r["Date"] >= lw_start]:          # 다음 주 초안: 근무 시간만 복사
        d = datetime.date.fromisoformat(r["Date"]) + datetime.timedelta(days=7)
        tl.append({"Date": d.isoformat(), "Day": r["Day"], "Leader": r["Leader"], "Shift": r["Shift"]})
    tls = wb.create_sheet(TL_TAB)
    header(tls, TL_COLS)
    for r in tl:
        tls.append([r.get(c) if r.get(c) not in ("", None) else None for c in TL_COLS])
    for col, (h, f) in tl_formulas(XLSX_LAST_ROW).items():
        c = tls[f"{col}1"]; c.value = h; c.fill = PatternFill("solid", fgColor="7F7F7F"); c.font = HDR_FONT
        tls[f"{col}2"] = f
    tls.freeze_panes = "A2"; tls.auto_filter.ref = "A1:F1"
    for col, w in zip("ABCDEFGHIJ", [11, 5, 16, 14, 11, 24, 11, 26, 24, 7]):
        tls.column_dimensions[col].width = w

    tlb = wb.create_sheet(TL_BOARD, index=3)
    tlb["A1"] = "TEAM LEADER — Weekly Schedule"; tlb["A1"].font = Font(bold=True, size=14)
    tlb["A2"] = "Week start (Sun):"; tlb["A2"].font = Font(bold=True)
    tlb["B2"] = '=TEXT(TODAY()-WEEKDAY(TODAY())+1,"yyyy-mm-dd")'; tlb["B2"].fill = PatternFill("solid", fgColor="FFF2CC")
    tlb["D2"] = "← 셀 = 근무시간 / 출근. OT Hrs = Overtime 탭 합계. 보기 전용 — 입력은 'TL Schedule' 탭에서."
    tlb["D2"].font = Font(italic=True, color="7F7F7F")
    for i, h in enumerate(["Leader"] + DAYS + ["OT Hrs"], 1):
        c = tlb.cell(3, i, h); c.fill = HDR_FILL; c.font = HDR_FONT; c.alignment = Alignment(horizontal="center")
    for ref, f in tl_board_formulas(XLSX_LAST_ROW).items():
        tlb[ref] = f
    wk0, wk1 = dates[0].isoformat(), dates[-1].isoformat()
    for i, name in enumerate(sorted({r["Leader"] for r in tl if wk0 <= r["Date"] <= wk1})):
        tlb.cell(5 + i, 1, name)
    tlb.freeze_panes = "B5"; tlb.column_dimensions["A"].width = 16
    for j in range(7): tlb.column_dimensions[openpyxl.utils.get_column_letter(2 + j)].width = 17

    penalties = parse_penalties(src)

    # ── Payroll (2주 단위, 자동 집계) / Payroll History ──
    pay = wb.create_sheet(PAYROLL_TAB)
    pay["A1"] = "PAYROLL — 2-week pay period (auto: Schedule + TL hours + OT − Death Penalty, Incentives)"
    pay["A1"].font = Font(bold=True, size=14)
    pay["A2"] = "Period (Mon):"; pay["A2"].font = Font(bold=True)
    pay["F2"] = "Anchor:"; pay["F2"].font = Font(bold=True)
    pay["G2"] = PAY_ANCHOR
    pay["H2"] = PAYROLL_NOTE
    pay["H2"].font = Font(italic=True, color="7F7F7F")
    pay["A3"] = ("Payable Hrs = Base(플레이어 시프트 + TL 근무) + OT − Death Penalty.  Incentives = Incentives 탭 금액 합계 (매니저 입력).")
    pay["A3"].font = Font(italic=True, color="7F7F7F")
    for i, h in enumerate(PAYROLL_HEADERS, 1):
        c = pay.cell(4, i, h); c.fill = HDR_FILL; c.font = HDR_FONT; c.alignment = Alignment(horizontal="center")
    for ref, f in payroll_formulas(XLSX_LAST_ROW).items():
        pay[ref] = f
    # 직원 목록(A)은 현재 급여 기간 기준 값으로 (봇 기동 시 동적 수식으로 교체)
    anchor = datetime.date.fromisoformat(PAY_ANCHOR)
    p0 = anchor + datetime.timedelta(days=14 * ((dates[-1] - anchor).days // 14))
    lo, hi = p0.isoformat(), (p0 + datetime.timedelta(days=13)).isoformat()
    def shift_hours(t):
        m = re.match(r"^(\d+):(\d+)-(\d+):(\d+)$", t or "")
        return ((int(m[3]) + int(m[4]) / 60) - (int(m[1]) + int(m[2]) / 60)) % 24 if m else 0
    names = {r.get("Player") for r in rows if lo <= r["Date"] <= hi and r.get("Player")
             and r["Time"] != "OFF" and shift_hours(r["Time"]) > 0}
    names |= {r["Leader"] for r in tl if lo <= r["Date"] <= hi and re.search(r"\d\s*[ap]m\s*-", r.get("Shift", ""), re.I)
              and str(r.get("Attendance", "")).lower() != "absent"}
    names |= {r["Staff"] for r in ot_rows if lo <= r["Date"] <= hi}
    names |= {r["Player"] for r in penalties if lo <= r["Date"] <= hi}
    for i, name in enumerate(sorted(n for n in names if n)):
        pay.cell(5 + i, 1, name)
    for ref in ("B2", "C2", "D2"):
        pay[ref].fill = PatternFill("solid", fgColor="FFF2CC")
    pay.freeze_panes = "B5"
    for col, w in zip("ABCDEFGHIJ", [20, 11, 11, 10, 9, 11, 12, 11, 8, 60]): pay.column_dimensions[col].width = w

    hist = wb.create_sheet(PAYROLL_HISTORY_TAB)
    hcols = ["Week", "Player", "Total Hours", "Shifts", "Characters"]
    header(hist, hcols)
    for r in parse_payroll(src):
        hist.append([r[c] for c in hcols])
    hist.freeze_panes = "A2"; hist.auto_filter.ref = "A1:E1"
    for col, w in zip("ABCDE", [7, 20, 11, 8, 90]): hist.column_dimensions[col].width = w

    # ── 매니저 기록 탭: Overtime / Incentives / Death Penalty (디스코드 봇이 입력) ──
    logs = {OVERTIME_TAB: (OVERTIME_COLS, ot_rows), INCENTIVE_TAB: (INCENTIVE_COLS, []),
            PENALTY_TAB: (PENALTY_COLS, penalties)}
    for title, (cols, data) in logs.items():
        ws = wb.create_sheet(title)
        header(ws, cols)
        wcol = openpyxl.utils.get_column_letter(len(cols) + 1)
        pcol = openpyxl.utils.get_column_letter(len(cols) + 2)
        for col, h in ((wcol, "Week"), (pcol, "Pay Week")):
            c = ws[f"{col}1"]; c.value = h; c.fill = PatternFill("solid", fgColor="7F7F7F"); c.font = HDR_FONT
        for r in data:
            ws.append([r.get(k) if r.get(k) not in ("", None) else None for k in cols])
        ws[f"{wcol}2"] = week_formula(XLSX_LAST_ROW)          # 데이터 뒤에 써야 2행부터 데이터가 들어감
        ws[f"{pcol}2"] = pay_week_formula(XLSX_LAST_ROW)
        ws.freeze_panes = "A2"; ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(cols))}1"
        for i in range(len(cols)): ws.column_dimensions[openpyxl.utils.get_column_letter(i + 1)].width = 14

    # ── Settings (정기점검) / Planner (기간 스케줄 입력) ──
    st = wb.create_sheet(SETTINGS_TAB)
    for r in SETTINGS_ROWS: st.append(r)
    for c in st[1]: c.fill = HDR_FILL; c.font = HDR_FONT
    for col, w in zip("ABC", [20, 10, 60]): st.column_dimensions[col].width = w
    pl = wb.create_sheet(PLANNER_TAB, index=1)
    header(pl, PLANNER_COLS)
    for r in PLANNER_EXAMPLE: pl.append(r)
    pl.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHI", [16, 5, 14, 13, 16, 18, 11, 11, 60]): pl.column_dimensions[col].width = w

    # ── Performance Pay: 기준표 그대로 복사 (값 + 병합) ──
    pp_src = src["performance pay"]; pp = wb.create_sheet(PERF_PAY_TAB)
    for row in pp_src.iter_rows(min_row=2, max_row=20):
        for c in row:
            if c.value not in (None, ""):
                pp.cell(c.row - 1, c.column, c.value if not str(c.value).startswith("=") else None)
    for m in pp_src.merged_cells.ranges:
        if m.min_row >= 2 and m.max_row <= 20:
            pp.merge_cells(start_row=m.min_row - 1, start_column=m.min_col, end_row=m.max_row - 1, end_column=m.max_col)
    pp["A1"].font = Font(bold=True)
    for col in "ABCDEFGHIJKLM": pp.column_dimensions[col].width = 11
    pp.column_dimensions["A"].width = 14

    # ── Glossary / EventLog ──
    with open(os.path.join(DATA, "glossary.csv"), encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.reader(f)):
            glo.append(r)
    header(glo, ["Term", "Variants", "KoreanFull", "English", "Category", "Verified"])
    header(ev, ["Timestamp", "Type", "Character", "Summary", "Status", "Answer"])

    # ── README ──
    lines = [
        ("Lineage Schedule v2 — 사용 안내", True),
        ("", False),
        ("탭 구성", True),
        ("• Client Board / Farming Board — 고객 계정 / 농장 계정 주간 보기 (수식, 직접 수정 금지). B2에 주 시작 일요일 입력하면 다른 주 조회", False),
        ("• Schedule — 원장. 1행 = 계정 × 날짜 × 시프트(Slot). 시간·플레이어·사냥터·KPI·골드·물약을 여기서 입력/수정 (필터 사용)", False),
        ("   - Time: HH:MM-HH:MM 또는 OFF.  Hours/Week/Key/Display/Maint Hrs/Pay Week(P~U열)는 자동 계산 — 건드리지 말 것", False),
        ("• Planner — 기간 스케줄 입력 (예: 10/1~10/31 평일 09:00-17:00 Cejay). 행을 채우고 디스코드 /plan → 미리보기 → Apply", False),
        ("• Settings — 정기점검 요일·시간 (기본 수요일 05:00-09:00). 겹치는 시프트 시간은 Hours에서 자동 차감", False),
        ("• Accounts — 계정 마스터. Type=Client(고객) / Farming(농장), Status=Active/Paused/Inactive, Customer=고객명, SalesRep=담당 영업", False),
        ("• TL Board / TL Schedule — 팀 리더 근무표 (Board = 주간 보기, Schedule = 입력: 근무시간·출근). TL OT는 Overtime 탭 (Role=TL). 이관: Week 16~39 + 다음 주 초안", False),
        ("• Overtime / Incentives / Death Penalty — 매니저 기록 로그 (디스코드 봇 /ot /incentive /penalty 로 입력, 직접 입력도 가능). Performance Pay — 인센티브 기준표", False),
        ("• Payroll — 2주 단위(월~일 × 2, 예: 9/21~10/4) 직원별 1·2주차 근무시간, OT, 페널티, 지급 시간, 인센티브 합계, 담당 캐릭터 자동 집계. B2에 기간 시작일 입력하면 과거 기간 조회 / Payroll History — 기존 수기 Payroll(W19·20·33·34)", False),
        ("• Glossary — 봇 용어 사전 / EventLog — 봇 기록(질문·재접속·정보)", False),
        ("", False),
        ("운영 규칙", True),
        ("• 새 주 행은 봇이 자동 생성: 직전 주의 시간·사냥터를 복사하고 플레이어·실적은 비움 (Active 계정만). TL 근무표는 /week 명령 때 근무시간만 복사", False),
        ("• 계정을 고객↔농장으로 옮기려면 Accounts의 Type만 바꾸면 됨 (다음 주 생성분부터 반영, 이번 주는 Schedule C열도 변경)", False),
        ("• 로그인 정보(ID/비밀번호)는 이 시트에 절대 넣지 말 것", False),
        ("", False),
        (f"이관: 원본 '{tab}' ({dates[0]}~{dates[-1]}) + 다음 주 초안. 이후 주는 봇이 자동 생성 (/week 명령으로 미리 생성 가능)", False),
    ]
    if conflicts:
        lines += [("", False), ("⚠️ 확인 필요 — 마스터 CSV와 시트 배치가 다른 계정 (현재는 시트 배치 기준)", True)]
        lines += [(f"   {c}", False) for c in conflicts]
    for i, (t, b) in enumerate(lines, 1):
        readme.cell(i, 1, t).font = Font(bold=b, size=13 if i == 1 else 11)
    readme.column_dimensions["A"].width = 120

    with open(os.path.join(DATA, "accounts_seed.csv"), "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(acc.values)
    wb.save(out_path)
    return accounts, rows, conflicts


if __name__ == "__main__":
    accs, rows, conflicts = build(sys.argv[1], sys.argv[2], sys.argv[3],
                                  with_schedule="--no-schedule-data" not in sys.argv)
    act = [a for a in accs.values() if a.get("Status", "Active") == "Active"]
    print(f"accounts={len(accs)} active={len(act)} "
          f"(client={sum(a['Type']=='Client' for a in act)}, farming={sum(a['Type']=='Farming' for a in act)}) "
          f"schedule_rows={len(rows)}")
    print("conflicts:", *conflicts, sep="\n  ")
