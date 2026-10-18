"""Measure how well Decider 2B routes: 20 simple and 20 hard customer-support requests, labelled by hand.

uv run python eval.py            # needs a Decider server on :8099
"""

from __future__ import annotations

import statistics

from handler import DEFAULT_THRESHOLD, NO, QUESTION, QUESTION_KEY, YES, DeciderClient

SIMPLE = [
    "What time do you open on Saturdays?",
    "Can you reset my password?",
    "Thanks, that's all!",
    "Where is my order #4412?",
    "Do you ship to Canada?",
    "How do I change my email address?",
    "Is the store open on Christmas Day?",
    "What's your phone number?",
    "Hi there!",
    "Can I pay with PayPal?",
    "How long does standard shipping take?",
    "Where can I find my invoice?",
    "Do you sell gift cards?",
    "What is your return window?",
    "Please cancel my newsletter.",
    "Is the blue jacket in stock?",
    "Can I get a receipt by email?",
    "What are your delivery fees?",
    "Hello, are you a bot?",
    "How do I track my parcel?",
]
HARD = [
    "I was charged twice for two orders, one arrived damaged, and the chatbot promised a refund in 3 days. "
    "Explain what went wrong and what I'm owed, and draft the email to my bank.",
    "Compare your premium and basic plans for a 40-person team with strict data-residency needs and tell "
    "me which to buy, with trade-offs.",
    "My business account was suspended after a chargeback I never made; walk me through the dispute "
    "process, the legal deadlines, and write a firm but polite escalation to your fraud team.",
    "Design a rollout plan to migrate our 12,000 customers from the old billing system to yours without "
    "downtime, including rollback criteria.",
    "I'm furious. Third time my delivery has been lost, nobody calls me back, and I have a medical "
    "product inside. What are you going to do about it right now?",
    "Reconcile these three invoices against my usage report and tell me where the overcharge came from.",
    "Write a detailed apology and compensation proposal for a customer whose wedding dress arrived two "
    "days after the wedding.",
    "Our legal team needs a summary of how you store and process EU customer data, and any gaps against "
    "GDPR article 28.",
    "Explain why my subscription renewed at a higher price after a promotional period, and whether that "
    "is legal in my country.",
    "Help me plan a bulk order of 500 mixed items across 4 warehouses with staggered delivery windows and "
    "a budget cap.",
    "A customer says our product injured their child. Draft the internal escalation and a careful, "
    "empathetic reply that does not admit liability.",
    "Diagnose why our API integration returns intermittent 502 errors only under load, and propose a fix.",
    "Create a retention offer strategy for customers who cancel after the first month, with expected "
    "cost and risk.",
    "I need you to untangle a refund that was partially issued to an expired card, partially store credit, "
    "and partially still pending, and tell me the exact steps to get my money.",
    "Rewrite our returns policy so it is legally compliant in the UK and Germany but still discourages "
    "abuse, and explain each change.",
    "Negotiate on our behalf: a supplier wants to raise prices 18%. Draft a counter-offer with three "
    "options and the reasoning behind each.",
    "Walk me through resolving a duplicate account problem where order history, loyalty points, and "
    "saved cards are split across two profiles.",
    "Summarise this 6-page complaint thread and tell me the three root causes and who owns each fix.",
    "Plan a crisis communication for a data-breach notification to affected customers, in stages.",
    "Evaluate whether we should switch carriers given these delivery-time statistics and customer "
    "complaints, and justify the recommendation.",
]


def main() -> None:
    client = DeciderClient()
    rows = []
    for text, hard in [(t, False) for t in SIMPLE] + [(t, True) for t in HARD]:
        r = client.ask_yes_no(text, QUESTION_KEY, QUESTION, YES, NO)
        small = (not r.answer) and r.confidence >= DEFAULT_THRESHOLD
        rows.append((hard, r, "small" if small else "frontier"))
    n = len(rows)
    right = sum(1 for hard, r, _ in rows if r.answer == hard)
    lat = [r.latency_ms for _, r, _ in rows]
    easy_small = sum(1 for hard, _, pick in rows if not hard and pick == "small")
    hard_frontier = sum(1 for hard, _, pick in rows if hard and pick == "frontier")
    bad = sum(1 for hard, _, pick in rows if hard and pick == "small")
    print(f"requests: {n}  (20 simple, 20 hard), threshold {DEFAULT_THRESHOLD}")
    print(f"Decider's yes/no matches the label: {right}/{n} = {right / n:.0%}")
    print(f"simple requests kept on the small model:   {easy_small}/20")
    print(f"hard requests escalated to the frontier:   {hard_frontier}/20")
    print(f"hard requests wrongly left on small model: {bad}/20")
    print(
        f"latency per decision: median {statistics.median(lat):.0f} ms, p90 {sorted(lat)[int(0.9 * n)]:.0f} ms"
    )
    for lo, hi in ((0.5, 0.7), (0.7, 0.9), (0.9, 1.01)):
        b = [(h, r) for h, r, _ in rows if lo <= r.confidence < hi]
        if b:
            print(
                f"  confidence {lo:.1f}-{min(hi, 1):.1f}: n={len(b):2d} correct={sum(1 for h, r in b if r.answer == h) / len(b):.0%}"
            )


if __name__ == "__main__":
    main()
