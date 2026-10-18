from roverthinking.planners.base import Planner, PlannerContext
from roverthinking.planners.rules import RulesPlanner

PLANNERS = ("rules", "steward", "llm_only")


def make_planner(name: str, ctx: PlannerContext | None = None) -> Planner:
    if name == "rules":
        return RulesPlanner()
    if name == "steward":
        from roverthinking.planners.steward import Steward

        if ctx is None or ctx.decider is None:
            raise ValueError("the steward planner needs a decider in its context")
        return Steward(ctx)
    if name == "llm_only":
        from roverthinking.planners.llm_only import LlmOnlyPlanner

        if ctx is None or ctx.escalate is None:
            raise ValueError("the llm_only planner needs a way to reach Earth")
        return LlmOnlyPlanner(ctx)
    raise ValueError(f"planner {name!r} is not available; use one of {PLANNERS}")
