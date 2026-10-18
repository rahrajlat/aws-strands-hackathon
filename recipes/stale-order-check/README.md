# Stale Order Check: is this tool call still valid right now?

**Pattern:** guardrails. **Wiring:** a Strands `InterventionHandler` (`before_tool_call`).

Agents often run a tool call that was decided a while ago: a queued approval, a plan from a minute back,
an order that took a long time to arrive. By then the world may have changed. `StaleCallGuard` asks
Strands Decider 2B, *given the state right now*, whether the call is still appropriate, and blocks it
(or steers the model) when the answer is a confident no. It runs in about 110 ms, locally.

## When to use it
- Tools with side effects (refunds, cancellations, deployments, messages) where acting on stale
  information is costly.
- Calls that may be delayed between "decided" and "executed".
- You want a check the agent cannot talk its way past: it is a number from a separate model, not the agent's own opinion.

## Drop it in (3 lines)
```python
from strands import Agent
from handler import StaleCallGuard  # copy handler.py into your project

guard = StaleCallGuard(state_fn=lambda event: describe_current_state(event), tools={"issue_refund"})
agent = Agent(model=model, tools=[issue_refund], interventions=[guard])
```
`state_fn(event)` returns the **current** state as plain text (what a human would check first). The
handler adds the tool name and arguments and asks Decider the question. It returns `Proceed`, or `Deny`
with a reason the model sees (or `Guide` with `action="guide"`, so the model can re-plan).
Depends only on `strands-agents` and the standard library; set `STRANDS_DECIDER_URL` if needed.

## The typed question
`still_valid`: "Is the thing this tool call acts on still in the state the call expects?" with options
*yes: still in the expected state* and *no: already done, cancelled, closed, expired, refunded, paid or
unavailable*. I tried four wordings on the same cases; this one clearly won (8 of 12 stale calls caught,
against 4-6 for the others), so the wording is worth tuning for your domain.

## Tuning
`block_confidence` (default `0.50`, i.e. any "no"): block only when Decider's "no" is at least this
confident. On the 24 cases below, stale calls blocked at 0.50 / 0.60 / 0.70 were 9 / 8 / 6 of 12, with
no valid call blocked at any of them. Raise it if a false block is costly.
`fail_closed=True` (default) blocks the call if Decider cannot be reached; set `False` to let it proceed.

## Measured
24 hand-labelled cases across 12 domains (refunds, meetings, deployments, thermostats, tickets, ...),
laptop RTX 3050 under WSL2, checkpoint `strands-decider-2B-hobson-v19` (`python eval.py` reproduces it):

| | result |
|---|---|
| Stale calls blocked | **9 / 12** |
| Valid calls wrongly blocked | **0 / 12** |
| Median time per check | **110 ms** |

Small, hand-made set; Decider 2B is experimental, so treat this as a guardrail layer, not the only one.

## How Roverthinking uses it
Orders from Mission Control cross a long light delay. When one arrives, the rover asks Decider whether it
is still valid and safe, using its current state, before running it: "go normal" is held when a
catastrophic sand trap appeared while it was in flight. The game implements that check itself; this recipe is the same check packaged as a
standalone Strands drop-in.

## Try it
```bash
uv run python example.py --offline    # no AWS, no server: scripted model and a keyword stand-in
uv run python example.py              # real Decider on :8099 + an Amazon Bedrock model
uv run python eval.py                 # the 24-case measurement (needs the Decider server)
```
