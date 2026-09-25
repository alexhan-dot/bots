#!/bin/bash
# 가짜 시트/디스코드로 전체 테스트 (실제 시트·API 는 건드리지 않음)
#   pip install -r requirements.txt openpyxl && bash tests/run_all.sh
cd "$(dirname "$0")/.." || exit 1
fail=0
for t in test_sheets test_housekeep test_slots test_planner test_newchar test_confirm test_discord test_shot smoke; do
  out=$(python tests/$t.py 2>&1 | tail -1)
  case "$out" in *OK*) echo "✅ $t";; *) echo "❌ $t: $out"; fail=1;; esac
done
exit $fail
