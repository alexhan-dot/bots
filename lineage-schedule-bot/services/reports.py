"""플레이어 시프트 리포트 — 시작·종료 스크린샷의 레벨/EXP %/아데나를 Shift Reports 탭에 기록.

- 시프트 찾기: 그 플레이어의 오늘·어제 시프트 중 지금 시각에 걸친 것 (시작 90분 전 ~ 끝 3시간 후)
- 시작/종료: 같은 시프트에 시작 기록이 없으면 시작, 있으면 종료 (명령에서 바꿀 수 있음)
- 종료 때 EXP 획득 = (끝 레벨 − 시작 레벨) × 100 + 끝 % − 시작 %,  아데나 획득 = 끝 − 시작
  → Schedule(지났으면 Archive) 의 KPI(= EXP % ÷ 100) · Gold 칸에도 기록
"""
import datetime
from services import clock, index, sheets, shiftutil
from services.layout import REPORTS_TAB, REPORT_COLS, STAFF_TAB, STAFF_COLS

C = {c: i for i, c in enumerate(REPORT_COLS)}
BEFORE, AFTER = datetime.timedelta(minutes=90), datetime.timedelta(hours=3)


# ── 디스코드 계정 연결 (/iam) ─────────────────
def link(discord_id: str, discord_name: str, name: str, role: str = "Player"):
    book = sheets._book()
    try:
        ws = book.worksheet(STAFF_TAB)
    except Exception:
        ws = book.add_worksheet(STAFF_TAB, rows=300, cols=len(STAFF_COLS))
        ws.update(values=[STAFF_COLS], range_name="A1")
    rows = ws.get("A2:B")
    row = next((i + 2 for i, r in enumerate(rows) if len(r) > 1 and str(r[1]) == str(discord_id)), None)
    vals = [[name, str(discord_id), discord_name, role, clock.stamp()]]
    if row:
        ws.update(values=vals, range_name=f"A{row}:E{row}", value_input_option="RAW")
    else:
        ws.append_rows(vals, value_input_option="RAW", table_range="A1")
    index.invalidate()


def register_unknown(discord_id: str, discord_name: str):
    """연결 안 된 디스코드 사용자 → Staff 탭에 Name 비운 줄 추가 (매니저가 이름만 채우면 됨)"""
    book = sheets._book()
    try:
        ws = book.worksheet(STAFF_TAB)
    except Exception:
        ws = book.add_worksheet(STAFF_TAB, rows=300, cols=len(STAFF_COLS))
        ws.update(values=[STAFF_COLS], range_name="A1")
    if any(len(r) > 1 and str(r[1]) == str(discord_id) for r in ws.get("A2:B")):
        return
    ws.append_rows([["", str(discord_id), discord_name, "Player", ""]], value_input_option="RAW", table_range="A1")


# ── 시프트 찾기 ───────────────────────────────
def _window(s) -> tuple[datetime.datetime, datetime.datetime] | None:
    sp = shiftutil.span(s.time)
    if not sp:
        return None
    base = datetime.datetime.combine(datetime.date.fromisoformat(s.date), datetime.time(0), tzinfo=clock.TZ)
    return base + datetime.timedelta(minutes=sp[0]), base + datetime.timedelta(minutes=sp[1])


def find_shift(player: str, account: str | None = None, now: datetime.datetime | None = None, snap=None):
    """지금 걸쳐 있는 그 플레이어의 시프트. 없으면 (None, [가까운 후보])"""
    now = now or clock.now()
    snap = snap or index.get()
    p = index._norm(player)
    days = {(now.date() - datetime.timedelta(days=i)).isoformat() for i in (0, 1)}
    mine = [s for s in snap.shifts if s.date in days and not s.off and index._norm(s.player) == p
            and (not account or s.account == account)]
    scored = []
    for s in mine:
        w = _window(s)
        if not w: continue
        if w[0] - BEFORE <= now <= w[1] + AFTER:
            dist = min(abs((now - w[0]).total_seconds()), abs((now - w[1]).total_seconds()))
            scored.append((dist, s))
    if scored:
        return sorted(scored, key=lambda x: x[0])[0][1], []
    return None, sorted(mine, key=lambda s: (s.date, s.time))


# ── Shift Reports 탭 ──────────────────────────
def _ws():
    book = sheets._book()
    try:
        return book.worksheet(REPORTS_TAB)
    except Exception:
        ws = book.add_worksheet(REPORTS_TAB, rows=2000, cols=len(REPORT_COLS))
        ws.update(values=[REPORT_COLS], range_name="A1")
        return ws


def find_report(shift: dict, player: str):
    """(행 번호, 값 dict) 또는 (None, None)"""
    ws = _ws()
    for i, v in enumerate(ws.get(f"A2:{chr(64 + len(REPORT_COLS))}")):
        v = list(v) + [""] * (len(REPORT_COLS) - len(v))
        if (sheets._norm_date(v[0]) == shift["date"] and v[1] == shift["account"] and str(v[2]) == str(shift["slot"])
                and index._norm(v[4]) == index._norm(player)):
            return i + 2, dict(zip(REPORT_COLS, v))
    return None, None


def kind_for(shift: dict, player: str) -> str:
    _, rep = find_report(shift, player)
    return "end" if rep and str(rep.get("Start At", "")).strip() and not str(rep.get("End At", "")).strip() else "start"


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def gains(start: dict, end: dict) -> tuple[float | None, int | None]:
    """start/end: {"level","exp_percent","adena"} → (EXP 획득 %, 아데나 획득)"""
    exp = None
    sl, sp, el, ep = (_num(start.get("level")), _num(start.get("exp_percent")),
                      _num(end.get("level")), _num(end.get("exp_percent")))
    if None not in (sl, sp, el, ep) and sl > 0 and el > 0 and sp >= 0 and ep >= 0:
        exp = round((el - sl) * 100 + ep - sp, 4)
    sa, ea = _num(start.get("adena")), _num(end.get("adena"))
    adena = int(ea - sa) if sa is not None and ea is not None and sa >= 0 and ea >= 0 else None
    return exp, adena


def clear(shift: dict, player: str, kind: str):
    """잘못 넣은 시작/끝 기록 지우기 (플레이어가 '시작이 아니라 끝' 이라고 바꿀 때)"""
    row, _ = find_report(shift, player)
    if not row:
        return
    pre = "Start" if kind == "start" else "End"
    cols = [f"{pre} At", f"{pre} Lv", f"{pre} EXP %", f"{pre} Adena", f"{pre} Shot", "EXP Gained %", "Adena Gained"]
    _ws().batch_update([{"range": f"{chr(65 + C[k])}{row}", "values": [[""]]} for k in cols])
    if kind == "end":                                   # 끝 기록으로 넣었던 획득량도 Schedule 에서 지움
        sheets.update_shift_anywhere(shift["date"], shift["account"], int(shift["slot"]), KPI="", Gold="")


def save(shift: dict, player: str, kind: str, vals: dict, shot_url: str, discord_id: str) -> dict:
    """기록하고 결과 반환 {kind, exp_gain, adena_gain, start}"""
    ws = _ws()
    row, rep = find_report(shift, player)
    stamp = clock.stamp()
    lv = vals.get("level") or ""
    pct = vals.get("exp_percent") if (vals.get("exp_percent") or -1) >= 0 else ""
    ad = vals.get("adena") if (vals.get("adena") or -1) >= 0 else ""
    out = {"kind": kind, "exp_gain": None, "adena_gain": None, "start": None}
    if kind == "start":
        cells = {"Start At": stamp, "Start Lv": lv, "Start EXP %": pct, "Start Adena": ad, "Start Shot": shot_url,
                 "Status": "Started"}
    else:
        start = {"level": (rep or {}).get("Start Lv"), "exp_percent": (rep or {}).get("Start EXP %"),
                 "adena": (rep or {}).get("Start Adena")}
        exp, adena = gains(start, vals)
        out.update(exp_gain=exp, adena_gain=adena, start=start)
        cells = {"End At": stamp, "End Lv": lv, "End EXP %": pct, "End Adena": ad, "End Shot": shot_url,
                 "EXP Gained %": "" if exp is None else exp, "Adena Gained": "" if adena is None else adena,
                 "Status": "Done" if rep else "End only"}
    if row:
        ws.batch_update([{"range": f"{chr(65 + C[k])}{row}", "values": [[v]]} for k, v in cells.items()],
                        value_input_option="RAW")
    else:
        base = {"Date": shift["date"], "Account": shift["account"], "Slot": shift["slot"], "Shift Time": shift["time"],
                "Player": player, "Discord ID": str(discord_id)}
        ws.append_rows([[{**base, **cells}.get(c, "") for c in REPORT_COLS]], value_input_option="RAW",
                       table_range="A1")
    if kind == "end" and (out["exp_gain"] is not None or out["adena_gain"] is not None):
        fields = {}
        if out["exp_gain"] is not None: fields["KPI"] = round(out["exp_gain"] / 100, 6)
        if out["adena_gain"] is not None: fields["Gold"] = out["adena_gain"]
        sheets.update_shift_anywhere(shift["date"], shift["account"], int(shift["slot"]), **fields)
    index.invalidate()
    return out
