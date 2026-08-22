"""
Simulated ZAR cash-in confirmation and fiat cash-out processing.
No real payment rails are touched — everything here is a mock/status simulation.
"""


def simulate_cash_in(remittance_id: str, method: str) -> bool:
    """
    method: agent_cash | bank_transfer | card
    Returns True once "confirmed" (mocked, or via admin.confirm_zar_payment).
    """
    # TODO: mark remittance.status = cash_in_confirmed
    raise NotImplementedError


def simulate_cash_out(cash_out_id: str) -> str:
    """
    Progresses a cash-out through requested -> approved -> completed (or failed).
    """
    # TODO: implement simple state machine, return final status
    raise NotImplementedError
