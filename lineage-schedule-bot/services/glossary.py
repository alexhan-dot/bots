"""리니지 클래식 용어 사전 — Glossary 시트 탭 기준(없으면 data/glossary.csv).
파서 프롬프트에 주입 + 미확인 용어 학습 루프."""
import csv, os, datetime, functools
from services import sheets

TAB = "Glossary"
_cache = {"ts": None, "rows": []}

def load(force=False) -> list[dict]:
    now = datetime.datetime.utcnow()
    if not force and _cache["ts"] and (now - _cache["ts"]).total_seconds() < 300:
        return _cache["rows"]
    try:
        ws = sheets._book().worksheet(TAB)
        rows = ws.get_all_records()
    except Exception:
        with open(os.path.join(os.path.dirname(__file__), "..", "data", "glossary.csv"),
                  encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    _cache.update(ts=now, rows=rows)
    return rows

def prompt_block() -> str:
    """파서/번역 시스템 프롬프트에 넣을 용어표"""
    lines = []
    for r in load():
        v = f" (변형: {r['Variants']})" if r.get("Variants") else ""
        flag = "" if r.get("Verified") == "Y" else " [미확인]"
        lines.append(f"- {r['Term']}{v} = {r['KoreanFull']} / EN: {r['English']} [{r['Category']}]{flag}")
    return "## 리니지 용어 사전 (번역 시 영문은 반드시 이 표기 사용)\n" + "\n".join(lines)

def all_terms() -> set[str]:
    s = set()
    for r in load():
        s.add(r["Term"])
        s.update(x.strip() for x in r.get("Variants", "").split("|") if x.strip())
    return s

def add(term: str, korean_full: str, english: str, category: str = "unknown",
        variants: str = "", verified: str = "Y"):
    ws = sheets._book().worksheet(TAB)
    ws.append_row([term, variants, korean_full, english, category, verified])
    load(force=True)

def ensure_tab():
    """Glossary 탭이 없으면 CSV로 생성"""
    book = sheets._book()
    try:
        book.worksheet(TAB); return
    except Exception:
        ws = book.add_worksheet(TAB, rows=300, cols=6)
        with open(os.path.join(os.path.dirname(__file__), "..", "data", "glossary.csv"),
                  encoding="utf-8-sig") as f:
            ws.update(values=list(csv.reader(f)), range_name="A1")
