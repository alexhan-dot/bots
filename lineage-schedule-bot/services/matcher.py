"""캐릭터명 매칭 — Accounts 탭 기준. 정확일치 → 별칭 → 한글명 → 유사도"""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from services import sheets

@dataclass
class MatchResult:
    kind: str                      # exact | ambiguous | none
    canonical: str | None = None
    candidates: list = field(default_factory=list)

def _norm(s: str) -> str:
    return "".join(s.lower().split()).replace("-", "").replace("(", "").replace(")", "")

def match(raw: str) -> MatchResult:
    if not raw:
        return MatchResult("none")
    master = sheets.load_master()     # [{Account, Type, KoreanName, Aliases, Status, ...}]
    n = _norm(raw)

    # 1. 정확일치 (정식명/한글명/별칭)
    for row in master:
        names = [row["Account"], row.get("KoreanName", "")] + \
                (row.get("Aliases", "").split("|") if row.get("Aliases") else [])
        if any(_norm(x) == n for x in names if x):
            return MatchResult("exact", canonical=row["Account"])

    # 2. 유사도 (0.75 이상 후보 최대 3개)
    scored = []
    for row in master:
        names = [row["Account"], row.get("KoreanName", "")] + \
                (row.get("Aliases", "").split("|") if row.get("Aliases") else [])
        best = max((SequenceMatcher(None, n, _norm(x)).ratio() for x in names if x), default=0)
        if best >= 0.75:
            scored.append((best, row["Account"]))
    scored.sort(reverse=True)
    if len(scored) == 1 and scored[0][0] >= 0.92:
        return MatchResult("exact", canonical=scored[0][1])
    if scored:
        return MatchResult("ambiguous", candidates=[c for _, c in scored[:3]])
    return MatchResult("none")
