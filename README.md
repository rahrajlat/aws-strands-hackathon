<div align="center">

<img src="docs/assets/banner.gif" alt="Roverthinking — A Mars rover that thinks before it drives" width="860">

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![AWS Bedrock](https://img.shields.io/badge/AWS-Bedrock-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/bedrock/)
[![Strands Decider 2B](https://img.shields.io/badge/Strands-Decider%202B-6B46C1?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iMTAiIGZpbGw9IndoaXRlIi8+PC9zdmc+&logoColor=white)](https://huggingface.co/StrandsAgents/strands-decider-2B-hobson-v19)
[![Textual](https://img.shields.io/badge/TUI-Textual-004578?logo=python&logoColor=white)](https://textual.textualize.io/)
[![uv](https://img.shields.io/badge/package%20manager-uv-DE5FE9?logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![Tests](https://img.shields.io/badge/tests-92%20passing-22C55E?logo=pytest&logoColor=white)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

Roverthinking is an autonomous rover **agent** that runs in your terminal. A
tiny rover named **Rovy** navigates across Mars terrain, hitting dust storms,
rockfalls, solar flares, and strange rocks. Every hazard triggers a real AI
decision: Strands Decider 2B is asked five typed yes/no questions about the
situation — in about 115 ms, on a laptop GPU — and its answers are turned into
an action. When Decider isn't confident enough and waiting is safe, Rovy stops
and escalates to **Earth** (Amazon Bedrock). Earth replies after a simulated
light-delay.

The Mars run is the environment the agent acts in — a live simulation you can
drive from the keyboard — so you can watch the same decision fire dozens of
times and measure how well it holds up.

The real output is a **YAML-driven decision framework** — `decider_framework` —
that any agent can drop into their project. The rover is one example of it in
action; the [`recipes/`](recipes/) folder has more.

<div align="center">

### ▶ [Watch the 3-minute demo](https://youtu.be/KAkgkHpOiZc)

</div>

---

## Design overview

<div align="center">
<img src="docs/assets/design.gif" alt="Roverthinking design overview — animated architecture flow" width="860">
</div>

Every hazard fires a batch of yes/no questions at **Strands Decider 2B** (running locally, ~115 ms). A **Policy Engine** matches the answers against YAML rules and checks the confidence score. If confidence is high enough the rover acts immediately. If it is low *and* waiting is safe, the request is queued for **Earth** — an Amazon Bedrock call that returns after a simulated 15-second light-delay.

---

> **Hackathon submission** — AWS Builder Center · Agent Experience Hackathon: Strands Decider 2B
> This is an independent open-source project. Not affiliated with or endorsed by AWS or Strands.

---

## Submission details

| Field | Value |
|---|---|
| **Hackathon** | AWS Builder Center · Strands Decider 2B Agent Experience |
| **Demo video** | [youtu.be/KAkgkHpOiZc](https://youtu.be/KAkgkHpOiZc) |
| **Decision patterns** | Model routing (`#model-routing`) |
| **Tags** | `#strands-decider-2b` `#model-routing` |
| **Earth model** | `us.anthropic.claude-haiku-4-5-20251001-v1:0` via Amazon Bedrock |
| **Decider checkpoint** | `StrandsAgents/strands-decider-2B-hobson-v19` |
| **Tests** | 92 passing (offline) |
| **License** | MIT |
| **Author** | Rahul Rajasekharan |

---

## Table of contents

1. [Why a rover agent?](#1-why-a-rover-agent)
2. [Design overview](#design-overview)
3. [How the decisions work](#3-how-the-decisions-work)
4. [The reusable framework](#3-the-reusable-framework)
5. [Recipes](#4-recipes)
6. [What the rover does](#5-what-the-rover-does)
7. [Measured results](#6-measured-results)
8. [Quick start](#7-quick-start)
9. [Project layout](#8-project-layout)

---

## 1. Why a rover agent?

A real Mars rover cannot
ask mission control every time it hits a pebble. It needs to decide locally,
fast. But for genuinely ambiguous situations it needs a human (or at least a
frontier model) in the loop — after the light-delay. That constraint maps
directly onto the decide-locally-or-escalate pattern, and it makes the latency
number *matter*.

---

## 2. How the decisions work

Every hazard Rovy detects triggers a **batch decision call** to Decider 2B.
Six typed yes/no questions are sent in a single request, sharing one encoded
state:

| Question | What it resolves |
|---|---|
| Is this a weather or space threat? | External vs. terrain |
| Is the threat severe? | Severity classification |
| Can Rovy go around it? | Bypass feasibility |
| Does the rover need to stop and wait? | Safe-mode trigger |
| Is the rover's instrument needed here? | Science opportunity |
| Can Rovy safely wait for Earth? | Escalation gate |

The first five classifiers feed a **policy engine** that matches rules in
`decisions/hazard_triage.yaml`. The sixth — *can Rovy wait?* — is the
escalation question.

A confident local call looks like this — Decider 2B answers all five classifiers
for a dust storm in one 576 ms batch, and because the result clears the 0.70
threshold Rovy acts immediately (raise the shield), no Earth round-trip needed:

<div align="center">
<img src="docs/assets/decision-local.png" alt="Decision inspector — Decider 2B answers five yes/no classifiers for a dust storm and decides 'shield' at 0.83 confidence in 576 ms" width="820">
</div>

**Decision logic:**

```
confidence ≥ 0.70  →  act immediately on Decider's answer
confidence < 0.70  AND  can_wait = yes  →  stop, ask Earth, hold
confidence < 0.70  AND  can_wait = no   →  act on best guess, log "under_uncertainty"
```

Earth answers arrive after a simulated 12–18 minute light-delay (compressed to
15 real seconds in this run).

The built-in decision inspector (`i`) shows every call in full — the situation,
who decided (Decider or Earth), the reasoning, the options considered, and the
state snapshot the decision was made from:

<div align="center">
<img src="docs/assets/decision-inspector.png" alt="Decision inspector — an escalated rockfall resolved by Earth (reroute), with options, reasoning and state snapshot" width="820">
</div>

Here a low-confidence rockfall call was escalated to **Earth** (Amazon Bedrock,
Claude Haiku 4.5), which recommended `reroute`; the game UI frames the backend
as "Earth" for immersion, while the inspector records that Earth — not the local
Decider — made this call.

---

## 3. The reusable framework

`src/decider_framework/` is a standalone Python package — no rover code, no
simulation imports. Drop a YAML file in any project and get structured decisions
back with two lines:

```python
from decider_framework import DecisionEngine

engine = DecisionEngine.from_yaml("recipes/airflow-task-restart/airflow_task_restart.yaml")

result = engine.decide(
    model=decider_2b,
    state={
        "task": "load_clickstream", "dag": "daily_etl",
        "error": "S3Exception: SlowDown — request throttled",
        "retries": 1, "max_retries": 3, "critical_path": False,
    },
)

print(result.action)      # "restart"
print(result.confidence)  # 0.87
print(result.escalated)   # False — confidence cleared the 0.65 threshold
```

**The YAML** — here is an example of how this pattern can be reused, for Amazon MWAA Airflow task restarts:

```yaml
name: airflow-task-restart

questions:
  - key: is_transient
    type: yesno
    text: "Is this failure likely transient rather than a persistent bug?"
    yes: "network timeout, resource exhaustion, OOM, infra blip"
    no:  "logic error, schema mismatch, missing config"

  - key: retries_exhausted
    type: yesno
    text: "Has the task already used all its configured retries?"

  - key: critical_path
    type: yesno
    text: "Will downstream tasks stall or the SLA be missed if this task doesn't complete soon?"

policy:
  threshold: 0.65
  rules:
    - when: {is_transient: yes, retries_exhausted: no}
      then: restart
    - when: {retries_exhausted: yes, critical_path: no}
      then: skip
    - when: {retries_exhausted: yes, critical_path: yes}
      then: escalate
  default: escalate
```

**Two things built on it:**

| Built on the framework | What it decides |
|---|---|
| Rover hazard triage (`decisions/hazard_triage.yaml`) | shield / slow / safe_mode / reroute |
| [Airflow task restart](recipes/airflow-task-restart/) | restart / skip / escalate a failed Glue job |

---

## 4. Recipes

[`recipes/airflow-task-restart/`](recipes/airflow-task-restart/) — standalone,
copy-pasteable: a YAML config and a handler you can run immediately.

### Airflow / Glue task restart (`#guardrails`)

Asks Decider 2B whether a failed task should be restarted, skipped, or escalated —
based on failure type, retry budget, and DAG criticality. When Decider's confidence
is too low, it hands off to a frontier model on Amazon Bedrock.

```python
from handler import restart_decision

result = restart_decision(decider, {
    "task": "load_clickstream", "dag": "daily_etl",
    "error": "S3Exception: SlowDown — request throttled",
    "retries": 1, "max_retries": 3, "critical_path": False,
})
print(result.action)     # "restart" | "skip" | "escalate"
print(result.confidence) # 0.87
```

→ [Full recipe README](recipes/airflow-task-restart/README.md)

---

## 5. What the rover does

Rovy is a 14-character-wide Unicode rover rendered in a [Textual](https://textual.textualize.io/)
TUI. He drives across a procedurally generated Mars landscape — scrolling
terrain, day/night cycle, dust haze — and hits nine types of hazardous events:

| Key | Hazard | Notes |
|---|---|---|
| `d` | Dust storm | External, severity 1–3, can wait |
| `r` | Rockfall | Terrain, can reroute |
| `m` | Meteor shower | External, severe |
| `f` | Solar flare | External, instrument risk |
| `s` | Sensor fault | Internal, safe-mode candidate |
| `g` | Strange rock | Science opportunity, Earth needed |
| `h` | Steep slope | Terrain, reroutable |
| `t` | Sand trap | Terrain, slow or wait |
| `n` | Cascade | Multi-hazard, highest stakes |

Rovy's face — eyes, OLED screen, LED pulse — changes with his emotional state.
He looks alert when a hazard is undecided, dizzy when stuck, happy when he
reaches the science site, and sad when he runs out of health.

---

## 6. Measured results

All measurements on a laptop RTX 3050 6 GB under WSL2, checkpoint
`StrandsAgents/strands-decider-2B-hobson-v19`.

| Decision | Dataset | Score |
|---|---|---|
| Hazard triage (5 classifiers) | 40 labelled hazard events | **34/40** |
| Median Decider latency | — | **~115 ms** |

### Decider 2B vs always-Bedrock

| Metric | Decider 2B (local) | Always Bedrock |
|---|---|---|
| Cost per decision | **$0** (local GPU) | ~$0.0004 (Claude Haiku) |
| Median latency | **~115 ms** | ~800 ms – 2 s |
| 100-decision run | **~11.5 s** total | ~80–200 s total |
| Works offline | **Yes** | No |

In a 40-hazard run Decider 2B handles ~85% of decisions locally. Only genuinely
ambiguous calls go to Earth — cutting API cost and round-trip wait by roughly **6×**.

---

## What I learned

**Batching yes/no questions is the right abstraction.** Sending five classifiers in
one request, sharing a single encoded state, is cleaner than prompt-engineering a
single "what should Rovy do?" question. It gives you per-question confidence and a
traceable policy you can read and change.

**The confidence threshold is a design parameter, not a constant.** 0.70 worked well
for rover hazards; 0.65 was better for the Airflow recipe. The threshold encodes your
tolerance for local autonomy vs. escalation — different domains need different values.

**The YAML pattern generalises.** I expected the rover to be the one interesting use
case. The Airflow recipe showed the same engine works for operational decisions too —
the questions change but the framework doesn't.

**Latency matters more than accuracy in interactive loops.** 115 ms per batch call
means the rover doesn't stutter. A frontier model call adds 800 ms+ of perceived lag
even when it's more accurate. In a live simulation the UX difference is real.

---

## 7. Quick start

**Requirements:** Python 3.11+, `uv`, a GPU with ≥4 GB VRAM for Decider 2B.
For Earth escalation: AWS credentials (Bedrock) or `ollama signin` (Ollama Cloud).

```bash
# 1. Clone and install
git clone https://github.com/rahrajlat/aws-strands-hackathon
cd aws-strands-hackathon
uv sync

# 2. Copy env and edit if needed
cp .env.example .env

# 3. Run the agent with a mock Decider (fully offline, no GPU needed)
uv run roverthinking play --earth mock

# 4. Run the agent with real Decider 2B
strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8099
uv run roverthinking play

# 5. Verify your config (checks Decider + Earth connection)
uv run roverthinking check

# 6. Run the test suite (92 tests, fully offline)
uv run pytest tests/ --ignore=tests/test_recipes.py
```

**Key bindings in the TUI:**

| Key | Action |
|---|---|
| `d r m f s g h t n` | Trigger a hazard by type |
| `1 / 2 / 3` | Set rover speed |
| `space` | Pause / resume |
| `i` | Open decision inspector |
| `q` | Quit |

**Running with Amazon Bedrock:**

```bash
# Set credentials (one-time)
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# Configure .env
echo "ROVER_EARTH_PROVIDER=bedrock" >> .env
echo "ROVER_EARTH_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0" >> .env

# Start Decider, then run the agent
strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8099
uv run roverthinking play
```

**Running with Ollama Cloud:**

```bash
# Sign in once
ollama signin

# Configure .env
echo "ROVER_EARTH_PROVIDER=ollama" >> .env
echo "ROVER_EARTH_MODEL=gpt-oss:120b-cloud" >> .env

# Start Decider, then run the agent
strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8099
uv run roverthinking play
```

**Verify whichever provider you chose** (`--ping` makes one real call to Earth, so a
bad model ID or missing model access shows up here instead of silently falling back to mock):

```bash
uv run roverthinking check --ping
# Expected output:
#   ✓ Decider 2B  — reachable at localhost:8099
#   ✓ Earth       — bedrock / us.anthropic.claude-haiku-4-5-20251001-v1:0 responding
```

**Earth provider config** (in `.env`):

```env
# Bedrock (needs AWS_REGION + credentials + model access enabled in the console)
# us.* is the US cross-region inference profile (Haiku 4.5 isn't on-demand callable via the
# bare model ID); outside the US use eu.* / apac.* / global.*.
ROVER_EARTH_PROVIDER=bedrock
ROVER_EARTH_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0

# Ollama Cloud (needs `ollama signin`)
ROVER_EARTH_PROVIDER=ollama
ROVER_EARTH_MODEL=gpt-oss:120b-cloud
```

---

## 8. Project layout

```
├── src/
│   ├── decider_framework/        ← reusable YAML decision engine (standalone)
│   │   ├── schema.py             ← YAML → DecisionConfig dataclasses
│   │   ├── engine.py             ← DecisionEngine.from_yaml() + EngineResult
│   │   └── strands_adapters.py   ← YamlInterventionHandler, YamlRoutingStrategy
│   └── roverthinking/
│       ├── cli.py                ← `roverthinking play | check | sim | bench`
│       ├── decisions/configs/    ← YAML decision configs (installed with package)
│       ├── planners/steward.py   ← decision logic, Earth escalation
│       ├── earth/                ← Bedrock + Ollama Cloud providers, light-delay queue
│       ├── sim/                  ← rover state machine, hazards, scoring
│       └── tui/                  ← Textual app, Unicode rover art, decision panel
├── decisions/                    ← builder-facing copies of the decision configs
├── recipes/
│   └── airflow-task-restart/     ← handler.py (runnable) + airflow_task_restart.yaml
└── tests/                        ← 92 tests (offline)
```

---

<div align="center">

Built for the **AWS Builder Center · Strands Decider 2B** hackathon, October 2026.
Rovy made it to the science site. Most of the time.

</div>
