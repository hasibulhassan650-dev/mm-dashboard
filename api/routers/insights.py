"""Liquidity insights: the regime, what moved it this week, and what is scheduled next. See insights_logic.py.

Read-only. Everything here is computed from Bangladesh Bank's own data; news events are listed beside the numbers and never change one.
"""
from fastapi import APIRouter

from db import get_session

router = APIRouter()


@router.get("")
def get_insights():
    from .insights_logic import insights_block
    session = get_session()
    try:
        return insights_block(session)
    finally:
        session.close()
