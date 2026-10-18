"""The recipes are standalone drop-ins: prove they work inside real Strands agents, with no network."""

import ast
import importlib.util
import pathlib
from types import SimpleNamespace

RECIPES = pathlib.Path(__file__).parents[1] / "recipes"


def load(recipe: str, module: str):
    """Import recipes/<recipe>/<module>.py in isolation (both recipes call their file `handler`)."""
    import sys

    path = RECIPES / recipe / f"{module}.py"
    for name in ("handler", "example"):  # the examples do `from handler import ...`
        sys.modules.pop(name, None)
    sys.path.insert(0, str(path.parent))
    try:
        if module == "example":
            spec = importlib.util.spec_from_file_location(f"{recipe}_example", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
        name = f"{recipe.replace('-', '_')}_{module}"
        spec = importlib.util.spec_from_file_location("handler", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["handler"] = mod
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
        return mod
    finally:
        sys.path.remove(str(path.parent))


def test_recipes_do_not_import_the_game():
    for f in RECIPES.glob("*/*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            mods = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            )
            assert not any(m.split(".")[0] == "roverthinking" for m in mods), f.name


def test_every_recipe_ships_the_four_required_files():
    for d in RECIPES.iterdir():
        if d.is_dir():
            for name in ("recipe.yaml", "handler.py", "README.md", "example.py"):
                assert (d / name).exists(), f"{d.name}/{name}"


def test_recipe_yaml_declares_a_pattern_a_question_and_measurements():
    import yaml

    for d in RECIPES.iterdir():
        if d.is_dir():
            meta = yaml.safe_load((d / "recipe.yaml").read_text())
            assert meta["pattern"] in (
                "model-routing",
                "guardrails",
                "policy",
                "tool-selection",
                "context-memory",
            )
            assert meta["question"]["options"].keys() == {"yes", "no"}
            assert (
                meta["wiring"]["class"]
                and meta["measured"][
                    "median_decision_latency_ms"
                    if "median_decision_latency_ms" in meta["measured"]
                    else "median_check_latency_ms"
                ]
            )


# ---- act-or-escalate -----------------------------------------------------------------------------


def test_act_or_escalate_routes_inside_a_real_strands_agent():
    ex = load("act-or-escalate", "example")
    log = []
    agent = ex.build_agent(True, log.append)
    easy = str(agent("What time do you open?"))
    hard = str(agent(ex.REQUESTS[3]))
    assert "small model" in easy and "frontier model" in hard
    assert [d.chose for d in log] == ["small", "frontier"]
    assert log[0].reason == "confident-small" and log[1].reason == "needs-frontier"


def test_act_or_escalate_escalates_when_unsure_or_when_decider_is_down():
    h = load("act-or-escalate", "handler")
    ex = load("act-or-escalate", "example")

    class Fixed:
        def __init__(self, yn):
            self.yn = yn

        def ask_yes_no(self, *a):
            if isinstance(self.yn, Exception):
                raise self.yn
            return self.yn

    from strands import Agent
    from strands.models import ModelRouter, RoutingCandidate

    def run(client):
        log = []
        router = ModelRouter(
            [
                RoutingCandidate(ex.ScriptedModel("small"), name="small"),
                RoutingCandidate(ex.ScriptedModel("frontier"), name="frontier"),
            ],
            strategy=h.DeciderRoutingStrategy(client=client, on_decision=log.append),
        )
        Agent(model=router, callback_handler=None)("hello")
        return log[0]

    assert run(Fixed(h.YesNo(False, 0.05, 0.95, 1.0))).chose == "small"
    unsure = run(Fixed(h.YesNo(False, 0.4, 0.6, 1.0)))  # "no", but below the 0.70 threshold
    assert (unsure.chose, unsure.reason) == ("frontier", "unsure-escalate")
    down = run(Fixed(ConnectionError("no server")))
    assert (down.chose, down.reason) == ("frontier", "decider-down")


def test_act_or_escalate_fails_over_to_the_other_model():
    h = load("act-or-escalate", "handler")
    import asyncio

    small, frontier = SimpleNamespace(name="small"), SimpleNamespace(name="frontier")
    ctx = SimpleNamespace(
        candidates=[small, frontier],
        messages=[],
        attempts=[SimpleNamespace(candidate=small, exception=RuntimeError("throttled"))],
    )
    pick = asyncio.run(h.DeciderRoutingStrategy(client=object()).select(ctx))
    assert pick is frontier


# ---- stale-order-check ------------------------------------------------------------------------


def test_stale_call_guard_blocks_only_the_stale_call_inside_a_real_agent():
    ex = load("stale-order-check", "example")
    ex.ORDERS["1001"]["refunded"] = False
    ex.ORDERS["1002"]["refunded"] = True
    log = []
    h = load("stale-order-check", "handler")
    guard = h.StaleCallGuard(
        ex.describe_world, tools={"issue_refund"}, client=ex.KeywordDecider(), on_decision=log.append
    )
    from strands import Agent

    agent = Agent(
        model=ex.ScriptedModel(), tools=[ex.issue_refund], interventions=[guard], callback_handler=None
    )
    ok = str(agent("Please refund order 1001."))
    stale = str(agent("Please refund order 1002."))
    assert "Refunded 40.00" in ok and ex.ORDERS["1001"]["refunded"]
    assert "DENIED" in stale and "changed since" in stale
    assert [d.blocked for d in log] == [False, True]


def test_stale_call_guard_options():
    h = load("stale-order-check", "handler")
    from strands.interventions import Deny, Guide, Proceed

    class No:
        def ask_yes_no(self, *a):
            return h.YesNo(False, 0.2, 0.8, 1.0)

    ev = SimpleNamespace(tool_use={"name": "t", "input": {"x": 1}})
    assert isinstance(h.StaleCallGuard(lambda e: "s", client=No()).before_tool_call(ev), Deny)
    assert isinstance(
        h.StaleCallGuard(lambda e: "s", client=No(), action="guide").before_tool_call(ev), Guide
    )
    assert isinstance(
        h.StaleCallGuard(lambda e: "s", client=No(), tools={"other"}).before_tool_call(ev), Proceed
    )
    assert isinstance(
        h.StaleCallGuard(lambda e: "s", client=No(), block_confidence=0.9).before_tool_call(ev), Proceed
    )
    assert h.StaleCallGuard(lambda e: "s", client=No()).on_error == "deny"
    assert h.StaleCallGuard(lambda e: "s", client=No(), fail_closed=False).on_error == "proceed"
