# Agent instructions

Main project: `lineage-schedule-bot/` — read **`lineage-schedule-bot/CODEX_HANDOFF.md`** first (state, credentials setup, rules, next tasks).

- Tests (fake sheet/Discord, no network): `cd lineage-schedule-bot && pip install -r requirements.txt openpyxl && bash tests/run_all.sh`
- Never commit `env.yaml` or any token/key. Secrets come from environment variables → `bash tools/make_env.sh > env.yaml`.
- Work on branch `claude/vibrant-albattani-j5mnpo` unless told otherwise.
