"""매니저 기록 로직 (디스코드 봇이 사용, 영어 UI)

양식 입력 → build() 가 메모리 인덱스에서 스케줄을 찾아 Draft(미리보기 + 후보) 생성 → 매니저 확인 → commit() 이 시트에 기록.
AI 없이 동작 (자유 입력은 ai_parse 가 같은 필드로 변환한 뒤 여기로 들어옴).

kind:
  ot        Overtime 탭 추가       (staff, date, hours, time?, account?, reason?)
  incentive Incentives 탭 추가     (staff, date, amount, account?, kpi?, reason?)
  penalty   Death Penalty 탭 추가  (staff, date, hours, account?, action?, ir?)
  assign    Schedule 플레이어 지정  (account, date, player, slot?/time?)
  off       Schedule OFF 처리      (account, date, slot?/time?)
  extend    Schedule 시간 변경      (account, date, new_time, time?/slot?)
"""
import re, datetime
from dataclasses import dataclass, field, asdict
from services import index, sheets, clock
from services.layout import OVERTIME_TAB, INCENTIVE_TAB, PENALTY_TAB

KINDS = ("ot", "incentive", "penalty", "assign", "off", "extend")
TITLES = {"ot": "Overtime", "incentive": "Incentive", "penalty": "Death Penalty",
          "assign": "Assign player", "off": "Set OFF", "extend": "Change shift time"}
TIME_RE = re.compile(r"^\s*(\d{1,2}):?(\d{2})\s*[-~]\s*(\d{1,2}):?(\d{2})\s*$")


@dataclass
class Draft:
    kind: str
    fields: dict                                  # 입력값 (정규화 후)
    candidates: list = field(default_factory=list)   # 선택지 시프트 [dict]
    selected: list = field(default_factory=list)     # 확정된 대상 시프트 [dict]
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)       # 있으면 저장 불가

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d): return cls(**d)

    @property
    def can_save(self) -> bool:
        needs_target = self.kind in ("assign", "off", "extend")
        return not self.errors and not (self.candidates and not self.selected) and \
            not (needs_target and not self.selected)


# ── 입력 정규화 ───────────────────────────────
def parse_date(raw: str | None) -> str | None:
    """today / yesterday / tomorrow / 2026-09-25 / 09-25 / 9/25 → ISO. 못 읽으면 None"""
    t = clock.today()
    s = (raw or "today").strip().lower()
    rel = {"today": 0, "yesterday": -1, "tomorrow": 1, "오늘": 0, "어제": -1, "내일": 1}
    if s in rel:
        return (t + datetime.timedelta(days=rel[s])).isoformat()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m: y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", s)
        if not m: return None
        y, (mo, d) = t.year, map(int, m.groups())
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def norm_time(raw: str | None) -> str | None:
    if not raw: return None
    m = TIME_RE.match(raw)
    return f"{int(m[1]):02d}:{m[2]}-{int(m[3]):02d}:{m[4]}" if m else None


def shift_name(time_range: str) -> str:
    """시작 시각으로 Morning / Mid / Graveyard 구분 (Death Penalty 표기용)"""
    m = TIME_RE.match(time_range or "")
    if not m: return ""
    h = int(m[1]) % 24
    return "Morning Shift" if 5 <= h < 13 else "Mid Shift" if 13 <= h < 21 else "Graveyard Shift"


def _sd(s: index.Shift) -> dict:
    return {"row": s.row, "date": s.date, "account": s.account, "type": s.type, "slot": s.slot,
            "time": s.time, "player": s.player, "ground": s.ground, "kpi": s.kpi}


# ── Draft 생성 ────────────────────────────────
def build(kind: str, **inp) -> Draft:
    if kind not in KINDS:
        raise ValueError(kind)
    snap = index.get()
    f = {k: (v.strip() if isinstance(v, str) else v) for k, v in inp.items() if v not in (None, "")}
    d = Draft(kind, f)
    date = parse_date(f.get("date"))
    if not date:
        d.errors.append(f"Can't read date '{f.get('date')}'. Use YYYY-MM-DD, MM-DD, today or yesterday.")
        return d
    f["date"] = date
    for key in ("time", "new_time"):
        if key in f:
            t = norm_time(f[key])
            if not t: d.errors.append(f"Time '{f[key]}' must look like 16:00-24:00.")
            else: f[key] = t

    if "account" in f:
        acc, cands = index.resolve_account(f["account"], snap)
        if acc: f["account"] = acc
        elif kind in ("assign", "off", "extend"):
            d.errors.append(f"Unknown account '{f['account']}'." + (f" Did you mean: {', '.join(cands)}?" if cands else ""))
            return d
        else:
            d.warnings.append(f"Account '{f['account']}' is not in Accounts.")

    if kind in ("ot", "incentive", "penalty"):
        _build_staff(d, snap)
    else:
        _build_account(d, snap)
    return d


def _build_staff(d: Draft, snap):
    f = d.fields
    staff, cands = index.resolve_name(snap.staff, f.get("staff", ""))
    if staff: f["staff"] = staff
    elif cands:
        d.warnings.append(f"Staff '{f.get('staff')}' not matched exactly. Similar: {', '.join(cands)}.")
    else:
        d.warnings.append(f"'{f.get('staff')}' has no shifts in the last 3 weeks (new name?).")
    if d.kind in ("ot", "penalty"):
        try:
            f["hours"] = float(f.get("hours"))
            if not 0 < f["hours"] <= 24: raise ValueError
        except (TypeError, ValueError):
            d.errors.append("Hours must be a number between 0 and 24.")
    if d.kind == "incentive":
        try: f["amount"] = float(f.get("amount"))
        except (TypeError, ValueError): d.errors.append("Amount must be a number.")

    tl = index.tl_on(f["date"], f.get("staff"), snap)
    shifts = [s for s in index.shifts_on(f["date"], f.get("account"), f.get("staff"), snap) if not s.off]
    if tl and not shifts:
        f["role"] = "TL"; f["scheduled"] = tl[0].shift
        return
    f["role"] = "Player"
    if len(shifts) == 1:
        d.selected = [_sd(shifts[0])]
    elif len(shifts) > 1:
        d.candidates = [_sd(s) for s in shifts]
    else:
        where = f" on {f['account']}" if f.get("account") else ""
        d.warnings.append(f"No shift found for {f.get('staff')}{where} on {f['date']}.")
        if f.get("account"):                                   # 그 계정의 그날 시프트를 보여줌
            others = [s for s in index.shifts_on(f["date"], f["account"], snap=snap) if not s.off]
            d.candidates = [_sd(s) for s in others]


def _build_account(d: Draft, snap):
    f = d.fields
    if "account" not in f:
        d.errors.append("Account is required."); return
    day = index.shifts_on(f["date"], f["account"], snap=snap)
    if not day:
        d.errors.append(f"No schedule rows for {f['account']} on {f['date']} (run /week if it's next week).")
        return
    pick = day
    if f.get("slot"):
        pick = [s for s in day if s.slot == int(f["slot"])]
    elif f.get("time"):
        pick = [s for s in day if s.time == f["time"]]
    elif d.kind in ("assign", "extend"):
        pick = [s for s in day if not s.off] or day
    if d.kind == "assign" and not f.get("player"):
        d.errors.append("Player is required.")
    if d.kind == "extend" and not f.get("new_time"):
        d.errors.append("New time is required (e.g. 24:00-09:30).")
    if not pick:
        d.errors.append(f"No matching shift. {f['account']} on {f['date']}: " + "; ".join(s.label() for s in day))
    elif len(pick) == 1 or d.kind == "off":                    # off: 시간 지정 없으면 그날 전체
        d.selected = [_sd(s) for s in pick]
    else:
        d.candidates = [_sd(s) for s in pick]


def select(d: Draft, idx: int) -> Draft:
    d.selected = [d.candidates[idx]]
    return d


# ── 미리보기 (영어) ───────────────────────────
def preview_lines(d: Draft) -> list[tuple[str, str]]:
    f = d.fields; out = [("Date", f["date"])] if "date" in f else []
    if d.kind in ("ot", "incentive", "penalty"):
        out.append(("Staff", f"{f.get('staff', '?')} ({f.get('role', 'Player')})"))
        if d.kind in ("ot", "penalty"): out.append(("Hours", _num(f.get("hours"))))
        if d.kind == "ot" and f.get("time"): out.append(("OT time", f["time"]))
        if d.kind == "incentive": out.append(("Amount", _num(f.get("amount"))))
        if f.get("kpi"): out.append(("KPI", f["kpi"]))
        if f.get("scheduled"): out.append(("TL shift", f["scheduled"]))
    else:
        out.append(("Account", f.get("account", "?")))
        if d.kind == "assign": out.append(("Player", f.get("player", "?")))
        if d.kind == "extend": out.append(("New time", f.get("new_time", "?")))
    for s in d.selected:
        out.append(("Matched shift", _shift_text(s)))
        if d.kind == "penalty": out.append(("Shift", shift_name(s["time"])))
    for k in ("reason", "action", "ir"):
        if f.get(k): out.append((k.upper() if k == "ir" else k.capitalize(), f[k]))
    return out


def _shift_text(s: dict) -> str:
    who = s["player"] or "no player"
    extra = f" @{s['ground']}" if s.get("ground") else ""
    return f"{s['account']} ({s['type']}) #{s['slot']} {s['time']} — {who}{extra}"


def _num(v) -> str:
    return "?" if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))


# ── 저장 ─────────────────────────────────────
def commit(d: Draft, manager: str, source: str = "discord") -> str:
    if not d.can_save:
        raise ValueError("draft not ready")
    f = d.fields; s = d.selected[0] if d.selected else {}
    if d.kind == "ot":
        sheets.append_log(OVERTIME_TAB, {
            "Date": f["date"], "Staff": f.get("staff"), "Role": f.get("role"), "Account": s.get("account") or f.get("account"),
            "Slot": s.get("slot"), "Scheduled Time": s.get("time") or f.get("scheduled"), "OT Time": f.get("time"),
            "OT Hours": f["hours"], "Reason": f.get("reason"), "Manager": manager, "Source": source})
        msg = f"OT {_num(f['hours'])}h — {f.get('staff')}"
    elif d.kind == "incentive":
        acc = s.get("account") or f.get("account")
        cls = (index.get().accounts.get(acc) or {}).get("Class", "") if acc else ""
        sheets.append_log(INCENTIVE_TAB, {
            "Date": f["date"], "Staff": f.get("staff"), "Role": f.get("role"), "Account": acc, "Class": cls,
            "Level": f.get("level"), "KPI": f.get("kpi") or s.get("kpi"), "Amount": f["amount"],
            "Reason": f.get("reason"), "Manager": manager, "Source": source})
        msg = f"Incentive {_num(f['amount'])} — {f.get('staff')}"
    elif d.kind == "penalty":
        sheets.append_log(PENALTY_TAB, {
            "Date": f["date"], "Player": f.get("staff"), "Character": s.get("account") or f.get("account"),
            "Shift": shift_name(s.get("time", "")) or f.get("shift", ""), "Penalty Hours": f["hours"],
            "Action": f.get("action") or f"Removed {_num(f['hours'])} hours in the sheet", "IR": f.get("ir"),
            "Manager": manager, "Source": source})
        msg = f"Death penalty {_num(f['hours'])}h — {f.get('staff')}"
    else:
        changed = []
        for s in d.selected:
            fields = {"assign": {"Player": f.get("player")}, "off": {"Time": "OFF"},
                      "extend": {"Time": f.get("new_time")}}[d.kind]
            if not sheets.update_shift_row(s["row"], s["date"], s["account"], s["slot"], **fields):
                index.invalidate()
                raise RuntimeError("That shift is no longer in the Schedule tab (past shifts move to 'Schedule Archive'). Edit it in the sheet.")
            changed.append(f"#{s['slot']}")
        msg = f"{TITLES[d.kind]} — {f['account']} {f['date']} {', '.join(changed)}"
    index.invalidate()
    return msg
