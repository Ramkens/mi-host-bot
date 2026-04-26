"""Aiogram routers — register them all in dispatcher."""
from __future__ import annotations

from aiogram import Router

from app.handlers import admin as admin_h
from app.handlers import cardinal as cardinal_h
from app.handlers import instances as instances_h
from app.handlers import payment as payment_h
from app.handlers import referral as referral_h
from app.handlers import script as script_h
from app.handlers import start as start_h
from app.handlers import support as support_h


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start_h.router)
    root.include_router(payment_h.router)
    root.include_router(instances_h.router)
    root.include_router(cardinal_h.router)
    root.include_router(script_h.router)
    root.include_router(referral_h.router)
    root.include_router(admin_h.router)
    root.include_router(support_h.router)
    return root
