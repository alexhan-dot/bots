"""디스코드 봇용 메모리 인덱스 — 스케줄 조회를 시트 API 없이 밀리초 단위로.

Accounts / Schedule(최근 3주 ~ 향후 2주) / TL Schedule 을 주기적으로 읽어 메모리에 보관.
- 조회·자동완성은 항상 메모리에서 (디스코드 3초 제한 + 즉시 응답)
- 60초 지나면 백그라운드 스레드로 갱신 (요청은 기다리지 않음)
- 봇이 시트에 쓴 직후엔 invalidate() → 다음 조회 때 갱신
"""
import datetime, threading, time, logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from services import sheets, clock
from services.layout import SCHEDULE_TAB, ARCHIVE_TAB, SCHEDULE_COLS, TL_TAB, TL_COLS

log = logging.getLogger("index")
TTL = 60
WINDOW_PAST, WINDOW_FUTURE = 21, 14


@dataclass
class Shift:
    row: int            # 시트 행 번호 (쓰기 전에 내용 재확인)
    date: str
    account: str
    type: str
    slot: int
    time: str
    player: str
    ground: str
    kpi: str = ""

    @property
    def off(self) -> bool:
        return self.time.upper() == "OFF"

    def label(self) -> str:
        t = "OFF" if self.off else self.time
        who = f" · {self.player}" if self.player else ""
        return f"{self.account} #{self.slot} {t}{who}"


@dataclass
class TLShift:
    row: int
    date: str
    leader: str
    shift: str
    attendance: str


@dataclass
class Snapshot:
    loaded_at: float = 0.0
    accounts: dict = field(default_factory=dict)        # name -> Accounts row
    shifts: list = field(default_factory=list)          # [Shift]
    tl: list = field(default_factory=list)              # [TLShift]
    staff: list = field(default_factory=list)           # 자동완성용 직원 이름 (플레이어 + TL)


_snap = Snapshot()
_lock = threading.Lock()
_refreshing = False


def _norm(s: str) -> str:
    return "".join(str(s).lower().split()).replace("-", "").replace("(", "").replace(")", "")


def load() -> Snapshot:
    """시트에서 새로 읽기 (API 3회)"""
    book = sheets._book()
    today = clock.today()
    lo = (today - datetime.timedelta(days=WINDOW_PAST)).isoformat()
    hi = (today + datetime.timedelta(days=WINDOW_FUTURE)).isoformat()
    snap = Snapshot(loaded_at=time.time())
    snap.accounts = {r["Account"]: r for r in book.worksheet("Accounts").get_all_records() if r.get("Account")}
    C = {c: i for i, c in enumerate(SCHEDULE_COLS)}
    end = chr(64 + len(SCHEDULE_COLS))

    def add(vals, first_row):
        for i, v in enumerate(vals):
            v = list(v) + [""] * (len(SCHEDULE_COLS) - len(v))
            d = sheets._norm_date(v[0])
            if not d or not lo <= d <= hi:
                continue
            try: slot = int(v[C["Slot"]])
            except (TypeError, ValueError): slot = 1
            snap.shifts.append(Shift(first_row + i if first_row else 0, d, v[C["Account"]], v[C["Type"]], slot,
                                     str(v[C["Time"]]).strip(), str(v[C["Player"]]).strip(),
                                     v[C["Hunting Ground"]], str(v[C["KPI"]])))

    add(book.worksheet(SCHEDULE_TAB).get(f"A2:{end}"), 2)
    try:                                                        # 지난 3주는 Schedule Archive 에 있음 (row=0: 수정 불가)
        aw = book.worksheet(ARCHIVE_TAB)
        n = len(aw.col_values(1))
        if n >= 2:
            add(aw.get(f"A{max(2, n - sheets.ARCHIVE_TAIL + 1)}:{end}{n}"), None)
    except Exception as e:
        log.warning("archive skipped: %s", e)
    try:
        for i, v in enumerate(book.worksheet(TL_TAB).get(f"A2:{chr(64 + len(TL_COLS))}")):
            v = list(v) + [""] * (len(TL_COLS) - len(v))
            d = sheets._norm_date(v[0])
            if d and lo <= d <= hi:
                snap.tl.append(TLShift(i + 2, d, v[2].strip(), v[3], v[4]))
    except Exception as e:                                      # TL 탭 없어도 동작
        log.warning("TL tab skipped: %s", e)
    names = {s.player for s in snap.shifts if s.player} | {t.leader for t in snap.tl if t.leader}
    snap.staff = sorted(names, key=str.lower)
    return snap


def get(block: bool = False) -> Snapshot:
    """캐시 반환. 오래됐으면 백그라운드 갱신 (block=True면 처음 한 번은 기다림)"""
    global _snap
    age = time.time() - _snap.loaded_at
    if _snap.loaded_at == 0 and block:
        with _lock:
            if _snap.loaded_at == 0:
                _snap = load()
    elif age > TTL:
        _refresh_async()
    return _snap


def ready() -> bool:
    return _snap.loaded_at > 0


def _refresh_async():
    global _refreshing
    if _refreshing:
        return
    _refreshing = True

    def run():
        global _snap, _refreshing
        try:
            sheets.housekeep_if_needed()                        # 날짜가 바뀌었으면 지난 시프트 보관·정렬
            _snap = load()
        except Exception as e:
            log.warning("index refresh failed: %s", e)
        finally:
            _refreshing = False
    threading.Thread(target=run, daemon=True).start()


def invalidate():
    """쓰기 직후 호출 — 다음 조회 때 백그라운드 갱신"""
    if _snap.loaded_at:
        _snap.loaded_at = 1.0


def warm():
    """앱 기동 시 호출 — 첫 요청이 기다리지 않도록"""
    try:
        get(block=True)
    except Exception as e:
        log.warning("index warm failed: %s", e)


# ── 조회 (전부 메모리) ─────────────────────────
def suggest(pool, query: str, limit: int = 25) -> list[str]:
    """자동완성: 접두사 > 포함 > 유사도 순"""
    q = _norm(query)
    if not q:
        return list(pool)[:limit]
    scored = []
    for name in pool:
        n = _norm(name)
        if n.startswith(q): score = 3
        elif q in n: score = 2
        else:
            r = SequenceMatcher(None, q, n).ratio()
            score = r if r >= 0.6 else 0
        if score: scored.append((-score, name.lower(), name))
    return [n for *_, n in sorted(scored)[:limit]]


def resolve_name(pool, raw: str) -> tuple[str | None, list[str]]:
    """정확히 하나로 정해지면 (이름, []), 아니면 (None, 후보)"""
    if not raw:
        return None, []
    n = _norm(raw)
    exact = [p for p in pool if _norm(p) == n]
    if len(exact) == 1:
        return exact[0], []
    cands = suggest(pool, raw, 5)
    return (cands[0], []) if len(cands) == 1 else (None, cands)


def account_names(snap: Snapshot | None = None) -> list[str]:
    snap = snap or get()
    active = [n for n, r in snap.accounts.items() if r.get("Status") == "Active"]
    return sorted(active, key=str.lower) + sorted((n for n in snap.accounts if n not in active), key=str.lower)


def resolve_account(raw: str, snap: Snapshot | None = None) -> tuple[str | None, list[str]]:
    snap = snap or get()
    if not raw:
        return None, []
    n = _norm(raw)
    for name, r in snap.accounts.items():                       # 정식명·한글명·별칭 정확 일치
        names = [name, r.get("KoreanName", "")] + str(r.get("Aliases", "")).split("|")
        if any(x and _norm(x) == n for x in names):
            return name, []
    return resolve_name(account_names(snap), raw)


def shifts_on(date: str, account: str | None = None, player: str | None = None,
              snap: Snapshot | None = None) -> list[Shift]:
    snap = snap or get()
    p = _norm(player) if player else None
    out = [s for s in snap.shifts if s.date == date
           and (account is None or s.account == account)
           and (p is None or _norm(s.player) == p)]
    return sorted(out, key=lambda s: (s.account.lower(), s.slot))


def tl_on(date: str, leader: str | None = None, snap: Snapshot | None = None) -> list[TLShift]:
    snap = snap or get()
    l = _norm(leader) if leader else None
    return [t for t in snap.tl if t.date == date and (l is None or _norm(t.leader) == l)]
