# x_intel → 0xbot decision queue (git handoff)

**Channel:** GitHub branch `xintel/signals` (not a shared local filesystem).

Grok's sandbox and the Mac running 0xbot do **not** share a disk. Intents are
committed here and pushed; 0xbot's poller `git fetch`es this branch and ingests
new `*.json` files.

## Contract
- Schema: `xintel.decision.v1`
- BUY/ADD TTL: 5 minutes from `issued_at` (`expires_at`)
- Ignore `*.tmp`
- Refuse test artifacts: `risk_flags` matching pipe_check|smoke|fixture|e2e_test|synthetic|demo, or ticker `PIPECHECK`
- `do_not_execute_until_armed` is Grok's stamp; **0xbot has its own arm/calibration gate**

## Calibration
Until 0xbot is armed locally, intents may still arrive for shadow tracking.
