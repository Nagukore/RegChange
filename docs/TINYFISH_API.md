# TinyFish API — verified contract

Everything in this project's TinyFish integration was built against the
official OpenAPI specifications, **not** against guesswork or blog posts.

**Captured:** 2026-10-31 · **Spec version:** 1.0.0 for each product.

```bash
curl -s https://agent.tinyfish.ai/v1/openapi/search  -o search.json
curl -s https://agent.tinyfish.ai/v1/openapi/fetch   -o fetch.json
curl -s https://agent.tinyfish.ai/v1/openapi/browser -o browser.json
curl -s https://agent.tinyfish.ai/v1/openapi/monitor -o monitor.json
```

Docs index: <https://docs.tinyfish.ai/llms.txt>

Authentication is the header `X-API-Key: $TINYFISH_API_KEY` on **every**
endpoint across all products. Keys are created at
<https://agent.tinyfish.ai/api-keys>.

---

## Search — `GET https://api.search.tinyfish.ai/`

Free to 12,000 requests/day.

**Query parameters**

| Parameter | Type | Notes |
|---|---|---|
| `query` | string | **required** |
| `purpose` | string | Why the search is run; max 2000 chars |
| `include_domains` | string | Comma-separated allow-list. **This is how discovery is scoped to official regulator domains** |
| `exclude_domains` | string | Comma-separated deny-list |
| `domain_type` | enum | `web` (default), `news`, `research_paper` |
| `after_date` / `before_date` | `YYYY-MM-DD` | Cannot combine with `recency_minutes` |
| `recency_minutes` | integer | 1 … 5,256,000 (10 years) |
| `location`, `language` | string | Geo-targeting |
| `page` | integer | From 0, max 10 |
| `include_thumbnail` | `"true"`/`"false"` | |
| `fetch` | string | JSON-encoded inline fetch config |

**Response**

```jsonc
{
  "query": "…",
  "results": [
    {
      "position": 1, "site_name": "…", "title": "…",
      "snippet": "…", "url": "https://…",
      "date": "…",        // news and some web results
      "publisher": "…"    // domain_type=news
    }
  ],
  "total_results": 10,
  "page": 0,
  "request_id": "…"       // also the X-Request-ID response header
}
```

Implemented in [`TinyFishClient.search`](../backend/app/integrations/tinyfish/client.py).
The mutual exclusion between `recency_minutes` and the date bounds is enforced
client-side so the API never receives an invalid combination.

---

## Fetch — `POST https://api.fetch.tinyfish.ai/`

Free to 1,000 URLs/day. **1–10 URLs per request**, fetched in parallel.

**Body**

| Field | Type | Default | Notes |
|---|---|---|---|
| `urls` | string[] | — | **required**, 1–10 |
| `format` | enum | `markdown` | `markdown`, `html`, `json` |
| `purpose` | string | — | Max 2000 chars |
| `ttl` | integer (s) | — | Omit = any cached entry; `0` = force live fetch |
| `if_none_match` | string | — | ETag. **Single URL only** (400 on a batch) |
| `if_modified_since` | string | — | Same single-URL restriction |
| `include_etag_and_last_modified` | bool | — | Opt in to validators + `not_modified` |
| `include_selectors` | string[] | — | CSS, max 20 |
| `exclude_selectors` | string[] | — | Applied before includes |
| `page_metadata`, `links`, `image_links`, `iframe_links` | bool | `false` | |
| `per_url_timeout_ms` | integer | — | Per-URL wall clock |

**Response**

```jsonc
{
  "results": [
    {
      "url": "…", "final_url": "…", "title": "…", "description": "…",
      "language": "en", "format": "markdown",
      "text": "…",                 // object when format=json
      "author": null, "published_date": null,
      "etag": "…", "last_modified": "…", "not_modified": false,
      "latency_ms": 812
    }
  ],
  "errors": [
    { "url": "…", "error": "target_http_error", "status": 403 }
  ],
  "request_id": "…"
}
```

Per-URL failures appear in `errors[]` with **HTTP 200 overall** — the client
must inspect the array, which
[`ExtractionAgent`](../backend/app/agents/extraction_agent.py) does, recording
the limitation on the document rather than inventing content.

**Why this matters for versioning:** `include_etag_and_last_modified` plus
`if_none_match` lets an unchanged document return `not_modified: true`, so
re-checking a regulator's page costs almost nothing and never creates a
spurious new version.

---

## Browser — `POST https://api.browser.tinyfish.ai/`

Metered. Session creation takes 10–30 s, so use a client timeout of 60 s+.

**Body:** `url` (optional startup navigation), `timeout_seconds`
(5–86400, inactivity), `proxy_config` (optional; defaults to a US residential
proxy, one exit IP per session).

**Response `201`**

```json
{
  "session_id": "br-a1b2c3d4-…",
  "cdp_url": "wss://example.tinyfish.io/cdp",
  "base_url": "https://example.tinyfish.io"
}
```

Connect Playwright with `connect_over_cdp(cdp_url)` — **not** `base_url`, which
is for session endpoints such as `/pages`. Terminate with
`DELETE https://api.browser.tinyfish.ai/{session_id}`; sessions also expire on
their inactivity timeout (15 min free, 60 min paid).

`cdp_url` grants control of the session, so this project treats it as a secret:
it is redacted from logs and only its hostname is ever recorded.

> **Reachability.** The browser runs in TinyFish's cloud, so it cannot resolve
> `localhost`. Driving the local CBS simulator requires a publicly reachable
> `CBS_PUBLIC_BASE_URL`; otherwise the project uses local Playwright and says
> so on the Settings screen.

---

## Agent — `POST https://agent.tinyfish.ai/v1/automation/{run,run-async,run-sse}`

Metered, ~10 s to minutes. Body: `url`, `goal`, optional `output_schema`.

SSE frames arrive as `data: {…}` lines:

| `type` | Fields |
|---|---|
| `STARTED` | `run_id` |
| `PROGRESS` | `run_id`, `purpose` |
| `COMPLETE` | `run_id`, `status`, `result` |

Used sparingly and only where Search and Fetch cannot do the job — for example
stepping through a paginated regulator archive. Search and Fetch are free;
Agent is not.

---

## Monitor — `https://agent.tinyfish.ai/v1/monitors`

Generally available (not beta). `POST` to create, `GET` to list, `GET`/`PATCH`/
`DELETE` on `/{id}`, `POST /{id}/runs` to run immediately.

**Create body:** `type` (`search` | `fetch`), `config` (product-specific),
`schedule_cron` (five-field, UTC; prefix `CRON_TZ=<zone>` to change),
optional `name`, `purpose`, `webhook_url`.

Monitor status: `creating`, `active`, `paused`, `failed`, `cancelled`.

Wrapped in `TinyFishClient.create_monitor` / `list_monitors` /
`delete_monitor`. Scheduling a continuous regulator watch is on the roadmap;
the client surface is ready for it.

---

## Error handling in this project

| Condition | Treatment |
|---|---|
| No API key | `TinyFishNotConfigured` → HTTP 503 with remediation text |
| 401 / 403 | Raised immediately; never retried |
| 429 or 5xx | `TinyFishTransient` → exponential backoff, 3 attempts |
| Network fault | Same retry path |
| Other 4xx | Raised immediately — retrying a caller error only burns quota |
| Per-URL fetch failure | Recorded as `access_limitation`; document marked `FETCH_FAILED` |

Typed response models live in
[`integrations/tinyfish/models.py`](../backend/app/integrations/tinyfish/models.py).
They set `extra="allow"` deliberately: TinyFish may add response fields, and a
new field must never break ingestion. Required fields are still enforced, so a
genuinely malformed response fails loudly.
