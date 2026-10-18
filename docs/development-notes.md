# Roverthinking — Development Notes

> Hackathon: **AWS Builder Center · Agent Experience Hackathon: Strands Decider 2B**
> Deadline: **October 23, 2026**
> Status: Core game + YAML framework complete. Tests pass. Not yet committed to git.

---

## What This Is

**Roverthinking** is a terminal game (Textual TUI) where a Mars rover named **Rovy** navigates to a science target on Mars, making autonomous decisions using **Strands Decider 2B** (a local model server). When Decider's confidence is low and it is safe to wait, Rovy escalates to **Earth** (Amazon Bedrock / Ollama Cloud). Everything is playable, scored, and demoed in the terminal.

**Run it:**
```
uv run roverthinking play              # with mock decider
uv run roverthinking play --earth mock # fully offline
uv run roverthinking check             # verify config
```

Key bindings in the TUI: `d` dust storm · `r` rockfall · `m` meteor shower · `f` solar flare · `s` sensor fault · `g` strange rock · `h` steep slope · `t` sand trap · `n` cascade · `1/2/3` speed · `space` pause · `i` decision inspector.

---

## Project Layout

```
aws-strands-hackathon/
├── src/
│   ├── decider_framework/          # ← NEW: YAML-driven decision framework
│   │   ├── __init__.py             # public API
│   │   ├── schema.py               # YAML config → dataclasses
│   │   ├── engine.py               # DecisionEngine.from_yaml() + EngineResult
│   │   └── strands_adapters.py     # YamlInterventionHandler, YamlRoutingStrategy
│   └── roverthinking/
│       ├── cli.py                  # `roverthinking play|check|sim|bench`
│       ├── config.py               # Settings (env vars / .env)
│       ├── decisions/
│       │   ├── configs/            # ← NEW: YAML files installed with the package
│       │   │   ├── hazard_triage.yaml
│       │   │   ├── instrument_choice.yaml
│       │   │   ├── stale_order_check.yaml
│       │   │   └── act_or_escalate.yaml
│       │   ├── model.py            # Question, Decision, MockDecider, Decider2B
│       │   ├── questions.py        # ← MIGRATED: loads Question objects from YAML
│       │   ├── triage.py           # ← MIGRATED: decide_triage/instrument via engine
│       │   ├── render.py           # rover snapshot → prose text for Decider
│       │   ├── mock_rules.py       # deterministic rules for offline tests
│       │   └── log.py              # JSONL decision log
│       ├── planners/
│       │   ├── steward.py          # main planner: Decider first, Earth on escalation
│       │   ├── rules.py            # fallback deterministic planner
│       │   └── llm_only.py         # Earth-only planner (comparison baseline)
│       ├── sim/
│       │   ├── rover.py            # Rover state machine + events
│       │   ├── hazards.py          # hazard definitions
│       │   ├── actions.py          # apply_triage, apply_instrument, etc.
│       │   └── oracle.py           # ground-truth decision evaluator
│       ├── earth/
│       │   ├── providers.py        # Bedrock + Ollama Earth models (Ollama Cloud only)
│       │   ├── link.py             # simulated light-delay queue
│       │   └── mission_control.py  # parse/validate Earth orders
│       ├── game/
│       │   ├── session.py          # GameSession: rover + planner + Earth + clock
│       │   ├── desk.py             # EarthDesk: manages escalation workflow
│       │   └── scenarios.py        # scripted scenario YAML loader
│       ├── tui/
│       │   ├── app.py              # Textual app root
│       │   ├── scene.py            # scrolling Mars scene renderer
│       │   ├── rover_art.py        # Rovy pixel art (half-block chars)
│       │   ├── sprites.py          # rover geometry constants
│       │   ├── canvas.py           # pixel layer + flush_pixels()
│       │   ├── effects.py          # particles, shield dome, sparks
│       │   ├── terrain.py          # procedural terrain
│       │   └── widgets/
│       │       ├── decision_panel.py  # live decision card
│       │       ├── panels.py          # status bars, Earth inbox
│       │       └── screens.py         # modal screens (inspector, settings, …)
│       └── bench/
│           └── run.py              # headless run for benchmarking
├── decisions/                      # ← builder-facing copies of the YAML configs
│   ├── hazard_triage.yaml
│   ├── instrument_choice.yaml
│   ├── stale_order_check.yaml
│   └── act_or_escalate.yaml
├── recipes/                        # standalone recipes (pre-framework)
│   ├── act-or-escalate/
│   │   ├── handler.py              # DeciderRoutingStrategy (Strands RoutingStrategy)
│   │   └── recipe.yaml             # pattern card with measurements
│   └── stale-order-check/
│       ├── handler.py              # StaleCallGuard (Strands InterventionHandler)
│       └── recipe.yaml
├── tests/
│   ├── test_decisions.py           # Question/Decision model, Decider2B payloads
│   ├── test_framework.py           # ← NEW: YAML engine tests (17 tests)
│   ├── test_sim.py                 # rover simulation
│   ├── test_steward.py             # Steward planner with mock decider
│   ├── test_earth.py               # Earth providers (needs real keys to test Bedrock)
│   ├── test_recipes.py             # recipe standalone tests (needs Decider server)
│   └── test_tui.py                 # TUI smoke tests
├── pyproject.toml
├── .env.example
└── docs/
    └── development-notes.md        # ← this file
```

---

## The YAML-Driven Framework (the Reusable Hackathon Asset)

### What it is

`decider_framework` is a standalone Python package (no game imports needed) that lets any builder configure Strands Decider 2B decisions in YAML and get structured decisions back with automatic escalation and Strands wiring.

### The YAML schema

```yaml
name: hazard-triage
description: "..."
pattern: split-and-combine      # informational tag

questions:
  - key: is_external_threat
    type: yesno                  # or "choice"
    text: "Is the hazard weather or something from space?"
    short: "Weather or space threat?"   # shown on-screen
    yes: "a solar flare, meteor shower or dust storm"
    no: "terrain or fault inside the rover"

escalation:                      # this question is asked in the same batch
  key: can_wait
  type: yesno
  text: "Can the rover safely wait for an answer from Earth?"
  yes: "waiting is safe"
  no: "waiting is dangerous"

policy:
  threshold: 0.70
  rules:
    - when: {is_external_threat: yes, is_severe: yes}
      then: shield
      require_context: shield_ready    # skipped if context["shield_ready"] is falsy
      name: "shield severe external threat"
    - when: {needs_safe_mode: yes}
      then: safe_mode
  default: slow
```

**Important YAML quirk**: PyYAML parses bare `yes`/`no` as booleans. `schema.py` handles this in both `_parse_question()` (checks `True`/`False` keys) and `_parse_rule()` (`_val()` converts `True` → `"yes"`, `False` → `"no"`). This was a real bug that caused all rules to silently fail.

### The Python API

```python
from decider_framework import DecisionEngine, YamlInterventionHandler, YamlRoutingStrategy

# Core decision engine
engine = DecisionEngine.from_yaml("decisions/hazard_triage.yaml")
result = engine.decide(
    model=decider_2b,               # anything with decide_many()
    state=rover.snapshot(),
    context={"shield_ready": True, "positional": True},
    threshold=0.70,
)
print(result.action, result.confidence)
# result.escalated → True if conf < threshold AND can_wait=yes

# Strands InterventionHandler (guardrail pattern)
guard = YamlInterventionHandler.from_yaml("decisions/stale_order_check.yaml", model)
agent = Agent(model=..., tools=[...], interventions=[guard])

# Strands RoutingStrategy (act-or-escalate pattern)
router = YamlRoutingStrategy.from_yaml("decisions/act_or_escalate.yaml", model, small, frontier)
```

### EngineResult fields

```python
@dataclass
class EngineResult:
    action: str               # chosen action
    confidence: float         # confidence of the matched rule (or min of all for default)
    can_wait: bool            # escalation question answer
    can_wait_confidence: float
    escalated: bool           # conf < threshold AND can_wait=True
    source: str               # "strands-decider-2b" | "mock-decider" | ...
    latency_ms: float         # wall time for the batch call
    detail: tuple             # list of {key, short, text, answer, confidence} per question
    rule_name: str            # name of the matched rule (or "default")
```

---

## Key Technical Decisions

### Strands Decider 2B API

Local server, port 8099 by default.

```
POST /v1/systemone
{
  "state": "<prose text>",
  "model": "roverthinking",
  "questions": {
    "key1": {"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}},
    "key2": {"type": "choice", "instructions": "...", "criteria": {"opt1": "...", "opt2": "..."}}
  }
}
```

- `noul` = yes/no, returns `{"noul": 0.82}` (P(yes))
- `choice` = multi-option, returns `{"choice": "reroute", "confidence": 0.77, "probabilities": {...}}`
- All questions share one encoded state (batch efficiency)
- Serialise concurrent requests — Decider doesn't handle them well

### The Act-or-Escalate Pattern

1. Ask Decider locally → get `(choice, confidence)`
2. `confidence >= threshold (0.70)` → act immediately
3. `confidence < threshold AND can_wait=yes` → stop, ask Earth, hold until reply
4. `confidence < threshold AND can_wait=no` → act on best guess, emit "under uncertainty"

### Shield Logic

- `shield.charges > 0 OR shield.state != "off"` → shield is ready
- `is_external_threat=yes AND is_severe=yes` → use shield (if ready)
- Shield blocks ALL damage: `rover.damage()` returns early if `self.shielded`
- Particles inside the dome boundary are intercepted in `effects.py` → cyan sparks instead of damage

### Earth Providers

- **Ollama Cloud only** (not local): model must end with `-cloud` or host must contain `ollama.com`
- Default model: `gpt-oss:120b-cloud` (requires `ollama signin`)
- **Bedrock**: `anthropic.claude-haiku-5-5` — needs AWS credentials; not yet tested (no creds in environment)
- Earth answers are vetted by the Stale Order Check before being applied

### Rover "Rovy"

- Pixel art rendered via half-block characters (`▀▄`) in `rover_art.py`
- 38 px wide × ~13 text rows
- 5×5 sensor-ring eyes with pupils, catchlights, eyelids per mood
- Tiny OLED screen above eyes shows heartbeat/icons
- Geometry constants in `sprites.py`

---

## Strands Decider 2B — Best Wording Discoveries

Tested on 24–40 labelled cases. These prompts achieved the measured scores:

| Question | Best wording | Score |
|---|---|---|
| Stale order | "Is the thing this tool call acts on still in the state the call expects?" | 9/12 stale blocked, 0/12 valid blocked |
| Needs frontier | "Does answering this request need a frontier model? Consider complexity, reasoning depth, domain expertise" | 36/40 correct |
| Hazard triage (split) | 5 narrow yes/no classifiers → Python rules | 34/40 correct |

---

## Game Decision Flow

```
Hazard arrives → Steward.step()
  → decide_triage(decider, state, positional)   [batched: 5+1 questions]
    → DecisionEngine("hazard_triage.yaml").decide()
      → model.decide_many(state, [5 classifiers + can_wait])
      → apply_rules() → action, confidence
    → returns Decision(choice, confidence, detail), Decision(can_wait)
  
  if confidence >= threshold:
    execute immediately
  elif can_wait:
    stop rover, ask Earth, wait for reply
    reply arrives → StaleOrderCheck vets it → apply
  else:
    act on best guess, emit "under_uncertainty"
```

---

## Test Suite (92 tests passing)

```
tests/test_decisions.py   (7 tests)   — Question model, Decider2B payloads, parse
tests/test_framework.py  (17 tests)   — YAML engine, rules, context guards, escalation
tests/test_sim.py        (28 tests)   — rover simulation, hazards, scoring
tests/test_steward.py    (16 tests)   — Steward planner end-to-end with mock decider
tests/test_tui.py         (6 tests)   — TUI smoke (no screen rendered)
tests/test_earth.py       (?)         — Earth providers (Bedrock needs real AWS creds)
tests/test_recipes.py     (8 tests)   — recipe standalone tests (needs Decider server)
```

Run:
```
uv run pytest tests/ --ignore=tests/test_recipes.py   # 92 pass, offline
uv run pytest tests/test_recipes.py                   # needs strands-decider serve
```

---

## Environment / Config

Copy `.env.example` to `.env` and set:

```env
ROVER_EARTH_PROVIDER=ollama      # or bedrock
ROVER_EARTH_MODEL=gpt-oss:120b-cloud
OLLAMA_HOST=http://localhost:11434
# For Bedrock:
AWS_REGION=us-east-1
# ROVER_EARTH_MODEL=anthropic.claude-haiku-5-5
STRANDS_DECIDER_URL=http://127.0.0.1:8099
ROVER_DECIDER=auto               # auto | server | mock
ROVER_CONFIDENCE_THRESHOLD=0.70
ROVER_LIGHT_DELAY_MIN=12
ROVER_DELAY_REAL_SECONDS=15
```

---

## What Remains To Do

### High priority (needed before submission Oct 23, 2026)

1. **Git identity + commit** — no git identity set on this machine yet:
   ```
   git config user.name "Rahul"
   git config user.email "rahrajlat@gmail.com"
   git add -A && git commit -m "Add YAML-driven decider_framework + migrate game decisions"
   ```

2. **Bedrock testing** — test `ROVER_EARTH_PROVIDER=bedrock ROVER_EARTH_MODEL=anthropic.claude-haiku-5-5 uv run roverthinking check --ping`
   - Need AWS credentials in the environment

3. **Demo video** (< 3 minutes) showing:
   - Rovy driving on Mars
   - A hazard arriving, decision card showing questions + answers + action
   - An escalation to Earth (show the light delay)
   - Earth reply vetted by stale order check

4. **Builder Center article** — the submission writeup explaining:
   - The YAML framework as the reusable asset
   - The two patterns (split-and-combine, act-or-escalate)
   - How to use `DecisionEngine.from_yaml()` in your own agent
   - Tag: `#strands-decider-2b` and `#model-routing`

5. **Hackathon tags** in submission: `#strands-decider-2b` `#model-routing`

### Nice to have

6. **Benchmark script** — compare Steward (Decider 2B) vs rules vs LLM-only planner with charts
   - `uv run roverthinking bench` currently returns a stub

7. **YAML framework README** — a `decisions/README.md` explaining the format for other builders:
   - Schema reference
   - How to add a new decision config
   - How to wire up with Strands

8. **recipe YAML alignment** — the `recipes/` standalone handlers predate the framework.
   Consider updating them to document they've been superseded by `decisions/*.yaml` + `decider_framework`.

9. **TUI decision panel** — currently shows question keys from `Q.HAZARD_TRIAGE` etc.
   The YAML configs have human-readable `short` labels that could replace these.

---

## Known Issues / Gotchas

- **Ollama Cloud only**: setting `ROVER_EARTH_MODEL` to a local model name (no `-cloud` suffix, no `ollama.com` host) shows a clear error. Run `ollama signin` before using Cloud models.
- **PyYAML boolean keys**: bare `yes`/`no` in YAML flow mappings (`{key: yes}`) are parsed as boolean `True`/`False`. `schema.py`'s `_parse_rule._val()` handles this. If you see rules silently not matching, check this.
- **Decider batching lock**: `Decider2B` serialises all requests through a `threading.Lock` — the server doesn't handle concurrency well.
- **Shield charges**: `HAZARD_TRIAGE.options` in `questions.py` is still a hardcoded Python `Question` (not from YAML). It represents all possible rover actions for UI display, not what Decider is asked.
- **`SPLIT` tuple**: `triage.py` still exports `SPLIT` as the five `Question` objects because `steward.py` passes them to `emit_pending()`. This is fine — they're loaded from YAML via `questions.py`.

---

## Architecture Summary

```
                     YAML config files
                     decisions/*.yaml
                          │
                    decider_framework
                    (standalone pkg)
                    ┌─────────────┐
                    │ schema.py   │  YAML → DecisionConfig
                    │ engine.py   │  DecisionConfig + model → EngineResult
                    │ strands_    │  InterventionHandler, RoutingStrategy
                    │ adapters.py │
                    └─────────────┘
                          │
              roverthinking.decisions
              ┌───────────────────────┐
              │ questions.py          │  Question objects (from YAML)
              │ triage.py             │  decide_triage() → engine.decide()
              │ model.py              │  Decider2B, MockDecider, Decision
              └───────────────────────┘
                          │
              roverthinking.planners.steward
              (act-or-escalate logic, Earth escalation)
                          │
              roverthinking.game.session
              (frame loop, background model calls, log)
                          │
              roverthinking.tui
              (Textual screen, rover pixel art, decision panel)
```
