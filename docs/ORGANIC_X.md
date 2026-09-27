# Organic X (required for Jacob ping)

X/Twitter is the primary narrative helper for early unique candidates under ~$1M.
`x_social` live poll returns `[]` unless wired; **do not rely on pump.twitter alone**
(that floods HELLO-class farms).

## Env (set on box for next scans)

```bash
export XINTEL_X_SOCIAL_LIVE=1
export XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1
```

| Var | Role |
|-----|------|
| `XINTEL_X_SOCIAL_LIVE=1` | Opt into CA-scoped X path (agent MCP + disk cache). |
| `XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1` | Jacob ping / xintel publish only when `organic_x` **or** strong unique curve+profile. Farm disk suppress stays. |

Disk BUY may still use curve+pump social for research; **Jacob ping prefers `organic_x`**.

## Disk schema — `data/x_organic/<mint>.json`

```json
{
  "query": "<exact CA>",
  "queried_at": "2026-09-20T20:00:00+00:00",
  "posts": [
    {
      "id": "2101…",
      "text": "… <CA> …",
      "author": "handle",
      "created_at": "2026-09-20T19:30:00+00:00",
      "url": "https://x.com/handle/status/2101…",
      "metrics": {"like_count": 0, "repost_count": 0}
    }
  ]
}
```

Enrich/pipeline reads this file and sets `organic_x=True`, `x_social=True` when
**≥1 recent post (<6h)** mentions the mint **CA** (not ticker-only spam).
Pure pump.twitter URL reuse and airdrop templates are rejected.

## Agent routine (max ~5 CAs / cycle)

Python has **no MCP** — the agent fetches, then writes JSON:

1. Pick ≤5 early unique candidates under ~$1M (not `clone_storm` / farm tickers).
2. MCP `search_posts_all` with `query=<exact CA>`, `sort_order=recency`, `max_results=10`
   (optional `start_time` = now−6h ISO).
3. Normalize → schema above.
4. Ingest:

```bash
python -m x_intel.tools.organic_x_ingest --mint <CA> --from-json /tmp/posts.json
# or print query + write empty stub:
python -m x_intel.tools.organic_x_ingest --mint <CA> --write-stub
```

5. Next discovery cycle stamps hints; `has_publish_quality_evidence` can pass via
   real `organic_x`. `has_ping_quality_evidence` gates Jacob ping.

## Code map

| Path | Role |
|------|------|
| `x_intel/discovery/organic_x.py` | Load/eval/annotate disk cache |
| `x_intel/tools/organic_x_ingest.py` | CLI helper |
| `x_intel/discovery/gates.py` | `_organic_x_evidence`, `has_ping_quality_evidence` |
| `x_intel/discovery/pipeline.py` | annotate after enrich |
