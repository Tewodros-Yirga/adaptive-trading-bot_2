"""
Bridge router — MT5 bridge account and position data.
"""
from fastapi import APIRouter

from ..services.bridge_client import bridge_client

router = APIRouter(prefix="/bridge", tags=["bridge"])


@router.get("/account")
def get_account():
    return bridge_client.get_account()


@router.get("/positions")
def get_positions():
    return bridge_client.get_positions()
