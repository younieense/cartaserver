from __future__ import annotations

import json
import logging
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import ExpenseCategory, ExpenseItem, MoneyVault, Position, User

logger = logging.getLogger("carta.security")


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
    """Первичная инициализация БД: справочники + один суперпользователь из env."""
    users = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    if users is not None:
        return

    settings = get_settings()
    login = (settings.admin_login or "").strip()
    password = settings.admin_password or ""
    if not login or not password:
        raise RuntimeError(
            "База пуста: задайте ADMIN_LOGIN и ADMIN_PASSWORD в окружении "
            "(или в файле .env), чтобы создать учётную запись администратора."
        )

    session.add(
        User(
            login=login,
            password_hash=hash_password(password),
            display_name=(settings.admin_display_name or login).strip() or login,
            role="superuser",
        )
    )
    logger.info("Создан суперпользователь из ADMIN_LOGIN=%s", login)

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


async def ensure_admin_user(session: AsyncSession) -> None:
    """
    Если заданы ADMIN_LOGIN/ADMIN_PASSWORD и такого логина ещё нет — создать суперпользователя.
    Старых admin/user/accountant не трогает (см. purge_default_users).
    """
    settings = get_settings()
    login = (settings.admin_login or "").strip()
    password = settings.admin_password or ""
    if not login or not password:
        return

    existing = (
        await session.execute(select(User).where(User.login == login))
    ).scalar_one_or_none()
    if existing is not None:
        return

    session.add(
        User(
            login=login,
            password_hash=hash_password(password),
            display_name=(settings.admin_display_name or login).strip() or login,
            role="superuser",
        )
    )
    await session.commit()
    logger.info("Добавлен суперпользователь ADMIN_LOGIN=%s", login)


DEMO_LOGINS = ("admin", "user", "accountant")


async def purge_default_users(session: AsyncSession) -> None:
    """Удаляет демо-логины admin/user/accountant, если CARTA_PURGE_DEFAULT_USERS=1."""
    settings = get_settings()
    if not settings.carta_purge_default_users:
        return

    keep = (settings.admin_login or "").strip().lower()
    rows = (
        await session.execute(select(User).where(User.login.in_(DEMO_LOGINS)))
    ).scalars().all()
    removed = []
    for u in rows:
        if u.login.lower() == keep:
            # не удаляем, если ваш ADMIN_LOGIN совпадает с admin
            continue
        removed.append(u.login)
        await session.delete(u)
    if removed:
        await session.commit()
        logger.warning("Удалены демо-пользователи: %s", ", ".join(removed))
    else:
        logger.info("CARTA_PURGE_DEFAULT_USERS=1, демо-пользователей не найдено")


def money(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
