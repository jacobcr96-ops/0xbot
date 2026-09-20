# x_intel → 0xbot decision queue (git handoff)

**Channel:** GitHub branch `xintel/signals` (not a shared local filesystem).

## Contract
- Schema: `xintel.decision.v1`
- BUY/ADD TTL: **20 minutes** (`expires_at` = issued_at + 1200s) — git fetch can cost ~3+ minutes
- `sizing_intent.mode=percent_equity` floor **0.75%** (FOMO ~$2.10 min notional on small accounts)
- `evidence[].refs` is always an array (use `[]`, never null)
- Ignore `*.tmp`
- Refuse test artifacts: risk_flags matching pipe_check|smoke|fixture|e2e_test|synthetic|demo, or ticker PIPECHECK
- `do_not_execute_until_armed` is Grok's stamp; **0xbot has its own arm/calibration gate**
