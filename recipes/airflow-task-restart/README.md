# Airflow Task Restart

**Pattern:** guardrails &nbsp;·&nbsp; **Model:** Strands Decider 2B (~115 ms) &nbsp;·&nbsp; **Fallback:** Amazon Bedrock

Decides whether a failed Airflow / AWS Glue job should be **restarted**, **skipped**, or **escalated** — without asking a frontier model on every failure. Decider 2B answers three yes/no questions in ~115 ms. When confidence is too low, the decision is handed off to a frontier model on Bedrock.

---

## How it works

```
failed task
    │
    ▼
Decider 2B (3 questions, ~115 ms)
    │
    ├─ confidence ≥ 0.65 ──► act immediately (restart / skip / escalate)
    │
    └─ confidence < 0.65 ──► ask frontier model (Bedrock)
```

**The three questions:**

| Question | Yes means | No means |
|---|---|---|
| Is the failure transient? | timeout, OOM, throttle, infra blip | schema bug, missing config, logic error |
| Are retries exhausted? | used all attempts | retries remaining |
| Is this on the critical path? | downstream tasks blocked, SLA at risk | non-blocking |

**Policy rules (first match wins):**

| Condition | Action |
|---|---|
| transient + retries left | `restart` |
| retries exhausted + non-critical | `skip` |
| retries exhausted + critical path | `escalate` |
| default (or low confidence) | `escalate` |

---

## Usage

```python
from handler import restart_decision

result = restart_decision(decider, {
    "task":          "load_clickstream",
    "dag":           "daily_etl",
    "error":         "S3Exception: SlowDown — request throttled",
    "retries":       1,
    "max_retries":   3,
    "critical_path": False,
})

print(result.action)      # "restart"
print(result.confidence)  # 0.87
```

---

## Run the demo

Requires a running Decider 2B server:

```bash
strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8099
python handler.py
```

**Example output:**

```
task:     ingest_clickstream  [daily_etl]
error:    S3Exception: SlowDown — read throughput exceeded, request throttled
retries:  1/3  critical=False
decision: restart  (Decider 2B, confidence 87%)

task:     transform_orders  [daily_etl]
error:    AnalysisException: cannot resolve 'order_uuid' — column missing in source schema
retries:  3/3  critical=True
decision: escalate  (Decider 2B, confidence 91%)

task:     join_customer_events  [daily_etl]
error:    GlueException: Internal service error (HTTP 500) after 4 min — no further details
retries:  1/3  critical=True
decision: unsure (58%) — asking frontier model …
decision: [frontier model] task=join_customer_events ...  (frontier)
```

---

## Files

| File | Purpose |
|---|---|
| `airflow_task_restart.yaml` | Questions, policy rules, threshold |
| `handler.py` | `restart_decision()` API + runnable demo |
