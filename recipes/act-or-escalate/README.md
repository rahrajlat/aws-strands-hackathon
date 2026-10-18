# Act-or-Escalate: let a 2B model decide whether the small model is enough

**Pattern:** model routing (`#model-routing`). **Wiring:** a Strands `RoutingStrategy`.

Strands can route each request to one of several models (`ModelRouter`). Its built-in
`ClassifierStrategy` makes that choice by **calling another LLM on every request**. This recipe makes
the same choice with Strands Decider 2B running locally: about 120 ms, no tokens, and a calibrated
confidence. Answer on the small model when Decider is sure it is enough; **escalate** to the frontier
model when Decider says the request is hard, when it is *unsure*, or when it cannot be reached.

## When to use it
- You have a cheap/fast model and a strong/expensive one, and most traffic is easy.
- You want the routing decision to cost nothing and be explainable (a yes/no with a confidence).
- You want a safe default: anything uncertain goes to the strong model.

## Drop it in (3 lines)
```python
from strands import Agent
from strands.models import ModelRouter, RoutingCandidate
from handler import DeciderRoutingStrategy  # copy handler.py into your project

router = ModelRouter(
    [RoutingCandidate(small_model, name="small"), RoutingCandidate(frontier_model, name="frontier")],
    strategy=DeciderRoutingStrategy(),  # needs: strands-decider serve <checkpoint> --port 8099
)
agent = Agent(model=router)
```
`handler.py` depends only on `strands-agents` and the standard library. Set `STRANDS_DECIDER_URL` if the
server is not on `127.0.0.1:8099`. Pass `on_decision=` to receive a `RoutingDecision` per request
(choice, reason, confidence, latency) for your own logs.

## The typed question
`needs_frontier`, a yes/no (Decider "noul") question, with a description of each option. The wording
lives at the top of `handler.py`, and the descriptions matter as much as the question: Decider reads them.
Replace `QUESTION`, `YES` and `NO` to fit your domain.

## Tuning the threshold
`threshold` (default `0.70`) is the confidence below which a "no, the small model is enough" is not
trusted and the request is escalated anyway. Raise it to escalate more (safer, costs more); lower it to
keep more traffic on the small model. Decider's confidence is calibrated, so this is a real dial: see below.

## Measured
On 40 hand-labelled customer-support requests (20 simple, 20 hard), laptop RTX 3050 (6 GB) under WSL2,
checkpoint `strands-decider-2B-hobson-v19`, threshold 0.70 (`python eval.py` reproduces this):

| | result |
|---|---|
| Routing correct | **36 / 40 (90%)** |
| Simple requests needlessly escalated | **0 / 20** |
| Hard requests escalated to the frontier model | 16 / 20 |
| Hard requests left on the small model | 4 / 20 |
| Median time to decide | **123 ms** (p90 171 ms) |

The confidence is worth using: Decider's answers below 0.70 confidence were right only 6% of the time
(all of them were hard requests it was unsure about), and the threshold sends exactly those to the
frontier model. Answers at 0.70 or above were right 83% of the time.
Small, hand-made set; Decider 2B is an experimental model. Measure on your own traffic.

## How Roverthinking uses it
In the game, the rover (Rovy) makes the same call on every hazard: act on its onboard Decider's choice,
or stop and ask Earth, a large LLM on Amazon Bedrock that is minutes away. The rover only waits for
Earth when Decider is below the threshold *and* waiting is safe. The game implements that decision itself; this recipe is the same
decision packaged as a standalone Strands drop-in for ordinary agents.

## Try it
```bash
uv run python example.py --offline    # no AWS, no server: stand-in models, shows the routing
uv run python example.py              # real Decider on :8099 + Amazon Bedrock models
uv run python eval.py                 # the 40-request measurement (needs the Decider server)
```
