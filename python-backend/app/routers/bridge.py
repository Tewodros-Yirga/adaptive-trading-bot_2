from fastapi import APIRouter

from ..services.bridge_client import bridge_client

router = APIRouter()


@router.get("/bridge/account")
def bridge_account():
    return bridge_client.get_account()


@router.get("/bridge/positions")
def bridge_positions():
    return bridge_client.get_positions()
