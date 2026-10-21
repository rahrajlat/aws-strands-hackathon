"""Airflow Task Restart: decide whether to restart, skip, or escalate a failed task.
Pattern: guardrails (#guardrails).

    from handler import restart_decision

    state = {
        "task":    "load_clickstream",
        "dag":     "daily_etl",
        "error":   "OperationalError: could not connect to server",
        "retries": 1,
        "max_retries": 3,
        "critical_path": False,
    }
    result = restart_decision(decider, state)
    print(result.action)      # "restart" | "skip" | "escalate"
    print(result.confidence)  # 0.87

Run: python handler.py
See airflow_task_restart.yaml for the question wording and policy rules.
"""
from pathlib import Path

from decider_framework import DecisionEngine

YAML = Path(__file__).parent / "airflow_task_restart.yaml"

_engine = DecisionEngine.from_yaml(YAML)


def restart_decision(decider, task_state: dict):
    """Ask Decider 2B whether to restart, skip, or escalate a failed Airflow task."""
    return _engine.decide(model=decider, state=task_state)


if __name__ == "__main__":
    import sys
    import time

    # --demo: pre-canned colorized output, no Decider server needed
    if "--demo" in sys.argv:
        G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"
        B = "\033[1m";  D = "\033[2m";  X = "\033[0m"
        ROWS = [
            ("ingest_clickstream",   "daily_etl",
             "S3Exception: SlowDown — read throughput exceeded, request throttled",
             1, 3, False, "restart",  87),
            ("transform_orders",     "daily_etl",
             "AnalysisException: cannot resolve 'order_uuid' — column missing in source schema",
             3, 3, True,  "escalate", 91),
            ("aggregate_metrics",    "hourly_reporting",
             "GlueException: Worker ran out of memory (G.1X DPU); job aborted",
             0, 2, False, "restart",  79),
            ("load_to_redshift",     "daily_etl",
             "AccessDeniedException: glue-etl-role not authorized to call redshift:GetClusterCredentials",
             2, 3, True,  "escalate", 88),
            ("crawl_raw_zone",       "daily_etl",
             "CrawlerException: Glue Data Catalog throttled — too many concurrent requests",
             0, 2, False, "restart",  82),
            ("join_customer_events", "daily_etl",
             "GlueException: Internal service error (HTTP 500) after 4 min — no further details",
             1, 3, True,  None,       58),
        ]
        print(f"{B}Airflow Task Restart — Decider 2B{X}\n")
        for task, dag, error, ret, mx, crit, action, conf in ROWS:
            print(f"{D}task:{X}     {task}  [{dag}]")
            print(f"{D}error:{X}    {error}")
            print(f"{D}retries:{X}  {ret}/{mx}  critical={crit}")
            if action == "restart":
                print(f"{D}decision:{X} {G}restart{X}  {D}(Decider 2B · {conf}% confidence · ~115 ms){X}")
            elif action == "escalate":
                print(f"{D}decision:{X} {R}escalate{X}  {D}(Decider 2B · {conf}% confidence · ~115 ms){X}")
            else:
                print(f"{D}decision:{X} {Y}unsure ({conf}%) — asking frontier model …{X}")
                time.sleep(0.35)
                print(f"{D}decision:{X} {R}escalate{X}  {D}(Amazon Bedrock){X}")
            print()
            time.sleep(0.22)
        sys.exit(0)

    import json
    import os
    import urllib.request
    from dataclasses import dataclass, field

    CASES = [
        {
            "task": "ingest_clickstream", "dag": "daily_etl",
            "error": "S3Exception: SlowDown — read throughput exceeded, request throttled",
            "retries": 1, "max_retries": 3, "critical_path": False,
        },
        {
            "task": "transform_orders", "dag": "daily_etl",
            "error": "AnalysisException: cannot resolve 'order_uuid' — column missing in source schema",
            "retries": 3, "max_retries": 3, "critical_path": True,
        },
        {
            "task": "aggregate_metrics", "dag": "hourly_reporting",
            "error": "GlueException: Worker ran out of memory (G.1X DPU); job aborted",
            "retries": 0, "max_retries": 2, "critical_path": False,
        },
        {
            "task": "load_to_redshift", "dag": "daily_etl",
            "error": "AccessDeniedException: glue-etl-role is not authorized to call redshift:GetClusterCredentials",
            "retries": 2, "max_retries": 3, "critical_path": True,
        },
        {
            "task": "crawl_raw_zone", "dag": "daily_etl",
            "error": "CrawlerException: Glue Data Catalog throttled — too many concurrent requests",
            "retries": 0, "max_retries": 2, "critical_path": False,
        },
        # ambiguous — Decider will be unsure; frontier model breaks the tie
        {
            "task": "join_customer_events", "dag": "daily_etl",
            "error": "GlueException: Internal service error (HTTP 500) after 4 min — no further details",
            "retries": 1, "max_retries": 3, "critical_path": True,
        },
    ]

    THRESHOLD = 0.65

    def _ask_frontier(case: dict) -> str:
        """Placeholder: would call Amazon Bedrock (Claude Haiku) with the task state."""
        return (
            f"[frontier model] task={case['task']} error={case['error']!r} "
            f"retries={case['retries']}/{case['max_retries']} critical={case['critical_path']}"
        )

    @dataclass
    class _D:
        question: str
        choice: str
        confidence: float
        probabilities: dict = field(default_factory=dict)
        latency_ms: float = 1.0
        source: str = "decider-2b"

    class _Decider2B:
        name = "decider-2b"
        def __init__(self):
            self.url = os.environ.get("STRANDS_DECIDER_URL", "http://127.0.0.1:8099").rstrip("/")
        def decide_many(self, state, questions):
            payload = {
                "state": json.dumps(state, default=str), "model": "recipe",
                "questions": {q.key: {"type": "noul", "instructions": q.text,
                                      "criteria": {"true": q.options.get("yes", ""), "false": q.options.get("no", "")}}
                              for q in questions},
            }
            req = urllib.request.Request(f"{self.url}/v1/systemone",
                                         json.dumps(payload).encode(), {"content-type": "application/json"})
            t = time.perf_counter()
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            ms = (time.perf_counter() - t) * 1000
            return [_D(q.key, "yes" if (p := float(data["answers"][q.key]["noul"])) >= 0.5 else "no",
                       max(p, 1 - p), {"yes": p, "no": 1 - p}, ms) for q in questions]

    decider = _Decider2B()
    for case in CASES:
        result = restart_decision(decider, case)
        print(f"task:     {case['task']}  [{case['dag']}]")
        print(f"error:    {case['error']}")
        print(f"retries:  {case['retries']}/{case['max_retries']}  critical={case['critical_path']}")
        if result.confidence >= THRESHOLD:
            print(f"decision: {result.action}  (Decider 2B, confidence {result.confidence:.0%})")
        else:
            print(f"decision: unsure ({result.confidence:.0%}) — asking frontier model …")
            frontier_reply = _ask_frontier(case)
            print(f"decision: {frontier_reply}  (frontier)")
        print()
