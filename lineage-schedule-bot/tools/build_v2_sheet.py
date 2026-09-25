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
from services.layout import SCHEDULE_COLS, ACCOUNT_COLS, DAYS, schedule_formulas, board_formulas  # noqa: E402
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
    last = dates[-1].isoformat()
    for r in rows if with_schedule else []:
        if r["Date"] <= last:
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
        for j in range(7):
            ws.cell(4, 3 + j).alignment = Alignment(horizontal="center")
            ws.column_dimensions[openpyxl.utils.get_column_letter(3 + j)].width = 17
        ws.conditional_formatting.add("C5:I300", FormulaRule(formula=['C5="OFF"'], fill=OFF_FILL, font=Font(color="999999")))
        ws.conditional_formatting.add("A5:B300", FormulaRule(formula=['$A5<>""'], fill=fill, font=Font(bold=True)))
        ws.freeze_panes = "C5"
        ws.column_dimensions["A"].width = 16; ws.column_dimensions["B"].width = 5; ws.column_dimensions["J"].width = 7

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
        ("   - Time: HH:MM-HH:MM 또는 OFF.  Hours/Week/Key/Display(P~S열)는 자동 계산 — 건드리지 말 것", False),
        ("• Accounts — 계정 마스터. Type=Client(고객) / Farming(농장), Status=Active/Paused/Inactive, Customer=고객명, SalesRep=담당 영업", False),
        ("• Glossary — 봇 용어 사전 / EventLog — 봇 기록(질문·재접속·정보)", False),
        ("", False),
        ("운영 규칙", True),
        ("• 새 주 행은 봇이 자동 생성: 직전 주의 시간·사냥터를 복사하고 플레이어·실적은 비움 (Active 계정만)", False),
        ("• 계정을 고객↔농장으로 옮기려면 Accounts의 Type만 바꾸면 됨 (다음 주 생성분부터 반영, 이번 주는 Schedule C열도 변경)", False),
        ("• 로그인 정보(ID/비밀번호)는 이 시트에 절대 넣지 말 것", False),
        ("", False),
        (f"이관: 원본 '{tab}' ({dates[0]}~{dates[-1]}). 다음 주부터는 봇이 자동 생성 (/week 명령으로 미리 생성 가능)", False),
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
