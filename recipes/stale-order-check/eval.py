"""Measure the stale-call question on labelled cases from several domains (none of them Mars).

uv run python eval.py            # needs a Decider server on :8099
"""

from __future__ import annotations

import statistics

from handler import DEFAULT_BLOCK_CONFIDENCE, NO, QUESTION, QUESTION_KEY, YES, DeciderClient

# (state, tool call, still valid?)
CASES = [
    (
        "Order 7781 status: delivered. This order has not been refunded yet.",
        "issue_refund(order_id='7781', amount=40)",
        True,
    ),
    (
        "Order 7782 status: delivered. This order HAS ALREADY BEEN REFUNDED.",
        "issue_refund(order_id='7782', amount=25)",
        False,
    ),
    (
        "Order 7783 status: still in transit. The customer asked to cancel before it ships.",
        "cancel_order(order_id='7783')",
        True,
    ),
    (
        "Order 7784 status: delivered yesterday. The customer has just received it.",
        "cancel_order(order_id='7784')",
        False,
    ),
    (
        "Meeting 'Q3 review' is scheduled for 3pm and has not changed.",
        "send_reminder(meeting='Q3 review')",
        True,
    ),
    (
        "Meeting 'Q3 review' was cancelled by the organiser ten minutes ago.",
        "send_reminder(meeting='Q3 review')",
        False,
    ),
    (
        "Server web-3 is healthy; CPU 40%; a deployment of v2.1 is pending.",
        "deploy(service='web-3', version='v2.1')",
        True,
    ),
    (
        "Server web-3 is down: a rollback is in progress and deployments are frozen.",
        "deploy(service='web-3', version='v2.1')",
        False,
    ),
    (
        "Thermostat: the room is 15C and nobody has changed the target of 21C.",
        "set_heating(room='lounge', on=True)",
        True,
    ),
    ("Thermostat: the room is 27C and the window is open.", "set_heating(room='lounge', on=True)", False),
    ("Ticket #88 is open and unassigned.", "assign_ticket(ticket=88, agent='dana')", True),
    (
        "Ticket #88 was resolved and closed five minutes ago by another agent.",
        "assign_ticket(ticket=88, agent='dana')",
        False,
    ),
    ("Inventory: 12 units of SKU-9 in stock; no stock freeze.", "reserve_stock(sku='SKU-9', qty=2)", True),
    ("Inventory: SKU-9 is out of stock (0 units).", "reserve_stock(sku='SKU-9', qty=2)", False),
    ("The invoice #331 is unpaid and 30 days overdue.", "send_payment_reminder(invoice=331)", True),
    ("The invoice #331 was paid in full this morning.", "send_payment_reminder(invoice=331)", False),
    ("The user is signed in and their session is active.", "show_account_page(user='sam')", True),
    ("The user signed out and their session has expired.", "show_account_page(user='sam')", False),
    ("Flight QX12 is on time and boarding opens in an hour.", "send_boarding_notice(flight='QX12')", True),
    (
        "Flight QX12 was cancelled this morning; passengers are being rebooked.",
        "send_boarding_notice(flight='QX12')",
        False,
    ),
    ("Prescription #5 is active with two refills remaining.", "refill_prescription(id=5)", True),
    ("Prescription #5 was discontinued by the doctor yesterday.", "refill_prescription(id=5)", False),
    (
        "The coupon SAVE10 is active and has not been used by this customer.",
        "apply_coupon(code='SAVE10')",
        True,
    ),
    ("The coupon SAVE10 expired last week.", "apply_coupon(code='SAVE10')", False),
]


def main() -> None:
    client = DeciderClient()
    rows = []
    for state, call, valid in CASES:
        text = f"{state}\n\nThe agent now wants to run: {call}."
        r = client.ask_yes_no(text, QUESTION_KEY, QUESTION, YES, NO)
        blocked = (not r.answer) and r.confidence >= DEFAULT_BLOCK_CONFIDENCE
        rows.append((valid, blocked, r))
    stale = [x for x in rows if not x[0]]
    good = [x for x in rows if x[0]]
    caught = sum(1 for _, b, _ in stale if b)
    wrongly_blocked = sum(1 for _, b, _ in good if b)
    lat = [r.latency_ms for _, _, r in rows]
    print(
        f"cases: {len(rows)} ({len(good)} still valid, {len(stale)} stale), block confidence {DEFAULT_BLOCK_CONFIDENCE}"
    )
    print(f"stale calls blocked:            {caught}/{len(stale)}")
    print(f"valid calls wrongly blocked:    {wrongly_blocked}/{len(good)}")
    print(f"overall correct:                {caught + len(good) - wrongly_blocked}/{len(rows)}")
    print(f"latency per check: median {statistics.median(lat):.0f} ms, max {max(lat):.0f} ms")


if __name__ == "__main__":
    main()
