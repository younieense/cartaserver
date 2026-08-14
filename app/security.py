from __future__ import annotations

import json
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ExpenseCategory, ExpenseItem, MoneyVault, Position, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


async def seed_if_empty(session: AsyncSession) -> None:
    users = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    if users is not None:
        return

    session.add_all(
        [
            User(
                login="admin",
                password_hash=hash_password("admin"),
                display_name="Администратор",
                role="superuser",
            ),
            User(
                login="user",
                password_hash=hash_password("user"),
                display_name="Сотрудник",
                role="user",
            ),
            User(
                login="accountant",
                password_hash=hash_password("accountant"),
                display_name="Бухгалтер",
                role="accountant",
            ),
        ]
    )
    session.add(MoneyVault(name="Касса, наличные", is_cash=True, is_active=True))
    session.add(
        Position(
            name="Механик",
            work_start_time=dtime(9, 0),
            work_end_time=dtime(18, 0),
            no_schedule=False,
            is_active=True,
        )
    )
    session.add(
        Position(
            name="Приёмщик",
            work_start_time=dtime(9, 0),
            work_end_time=dtime(18, 0),
            no_schedule=False,
            is_active=True,
        )
    )

    data_path = Path(__file__).resolve().parent.parent / "data" / "expense_categories.json"
    if data_path.exists():
        categories = json.loads(data_path.read_text(encoding="utf-8"))
        for order, (cat_name, items) in enumerate(categories.items()):
            cat = ExpenseCategory(name=cat_name, sort_order=order)
            session.add(cat)
            await session.flush()
            for item_name in items:
                session.add(ExpenseItem(category_id=cat.id, name=item_name))

    await session.commit()


async def ensure_accountant_user(session: AsyncSession) -> None:
    """Create default accountant account on existing databases if missing."""
    existing = (
        await session.execute(select(User).where(User.login == "accountant"))
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        User(
            login="accountant",
            password_hash=hash_password("accountant"),
            display_name="Бухгалтер",
            role="accountant",
        )
    )
    await session.commit()


def money(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
