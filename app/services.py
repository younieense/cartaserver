from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Absence,
    AccountantOperation,
    ActionLog,
    Attendance,
    AttendanceEvent,
    Debt,
    Employee,
    ExpenseCategory,
    MoneyMovement,
    MoneyOperation,
    MoneyVault,
    Position,
    Shift,
    User,
)
from .serializers import (
    accountant_operation_dto,
    apply_cash_mismatch,
    debt_dto,
    employee_dto,
    expense_tree_dto,
    expected_cash_end,
    fmt_time,
    log_dto,
    operation_dto,
    parse_money,
    position_dto,
    shift_summary_dto,
    user_dto,
    vault_dto,
)
from .timeutil import now_local, shift_date_for, shift_window, to_utc


SHIFT_EAGER = (
    selectinload(Shift.attendances).selectinload(Attendance.employee),
    selectinload(Shift.absences).selectinload(Absence.employee),
    selectinload(Shift.operations)
    .selectinload(MoneyOperation.movements)
    .selectinload(MoneyMovement.vault),
)


async def add_log(
    session: AsyncSession,
    user: User | None,
    action: str,
    entity: str = "",
    entity_id: int | None = None,
    details: str = "",
) -> None:
    session.add(
        ActionLog(
            user_id=user.id if user else None,
            user_login=user.login if user else "",
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
        )
    )


async def ensure_current_shift(session: AsyncSession) -> Shift:
    """Open today's shift at 05:00 boundary; close previous open shifts."""
    today = shift_date_for()
    opened, closes = shift_window(today)

    # Close any open shifts that are older than current
    result = await session.execute(select(Shift).where(Shift.is_open.is_(True)))
    for sh in result.scalars().all():
        if sh.shift_date < today:
            sh.is_open = False
            sh.closed_at = to_utc(closes - (closes - opened))  # will set properly below
            _, next_open = shift_window(sh.shift_date)
            sh.closed_at = to_utc(next_open)

    existing = (
        await session.execute(select(Shift).options(*SHIFT_EAGER).where(Shift.shift_date == today))
    ).scalar_one_or_none()
    if existing:
        if not existing.is_open:
            # Should not reopen past shifts automatically if somehow closed early
            if existing.shift_date == today:
                existing.is_open = True
                existing.closed_at = None
        await session.commit()
        return await get_shift(session, existing.id)

    shift = Shift(
        shift_date=today,
        opened_at=to_utc(opened),
        is_open=True,
        cash_start=None,
        cash_end=None,
    )
    session.add(shift)
    await session.commit()
    return await get_shift(session, shift.id)


async def get_shift(session: AsyncSession, shift_id: int) -> Shift:
    shift = (
        await session.execute(select(Shift).options(*SHIFT_EAGER).where(Shift.id == shift_id))
    ).scalar_one_or_none()
    if not shift:
        raise ValueError("Смена не найдена")
    return shift


async def enrich_shift_attendances(session: AsyncSession, shift: Shift) -> list[dict]:
    """All active employees with today's attendance status for shift screen."""
    board = await attendance_board(session, shift.id)
    return board["employees"]


async def get_shift_by_date(session: AsyncSession, d: date) -> Shift | None:
    return (
        await session.execute(select(Shift).options(*SHIFT_EAGER).where(Shift.shift_date == d))
    ).scalar_one_or_none()


def assert_can_edit(user: User, shift: Shift) -> None:
    if shift.is_open:
        return
    if user.role == "superuser":
        return
    raise PermissionError("Закрытые смены может редактировать только суперпользователь")


def parse_hhmm(raw: str | None) -> time | None:
    if raw is None or str(raw).strip() == "":
        return None
    text_value = str(raw).strip()
    try:
        hh, mm = text_value.split(":")
        return time(int(hh), int(mm))
    except Exception as exc:
        raise ValueError("Время должно быть в формате ЧЧ:ММ") from exc


async def list_shifts(session: AsyncSession, *, detailed: bool) -> list[dict]:
    await ensure_current_shift(session)
    rows = (
        await session.execute(
            select(Shift).options(*SHIFT_EAGER).order_by(Shift.shift_date.desc())
        )
    ).scalars().all()
    return [shift_summary_dto(s, detailed=detailed) for s in rows]


async def set_cash(
    session: AsyncSession,
    user: User,
    shift_id: int,
    cash_start: Any = None,
    cash_end: Any = None,
    update_start: bool = False,
    update_end: bool = False,
    force_save: bool = False,
) -> dict:
    shift = await get_shift(session, shift_id)
    assert_can_edit(user, shift)
    if update_start:
        shift.cash_start = parse_money(cash_start) if cash_start not in (None, "") else None
    if update_end:
        shift.cash_end = parse_money(cash_end) if cash_end not in (None, "") else None

    expected = expected_cash_end(shift)
    mismatch = False
    if (
        update_end
        and shift.cash_start is not None
        and shift.cash_end is not None
        and expected is not None
    ):
        entered = Decimal(str(shift.cash_end)).quantize(Decimal("0.01"))
        expected_q = expected.quantize(Decimal("0.01"))
        if entered != expected_q:
            mismatch = True
            difference = entered - expected_q
            if not force_save:
                cash_start_f = float(shift.cash_start)
                cash_end_f = float(entered)
                expected_f = float(expected_q)
                diff_f = float(difference)
                await session.rollback()
                return {
                    "needs_confirmation": True,
                    "shift_id": shift_id,
                    "cash_start": cash_start_f,
                    "cash_end": cash_end_f,
                    "expected_cash_end": expected_f,
                    "difference": diff_f,
                    "message": (
                        "Сумма «Касса, конец дня» не сходится с расчётом:\n"
                        f"начало {cash_start_f:.2f} "
                        f"+ приходы/−расходы по «Касса, наличные» "
                        f"= ожидание {expected_f:.2f} руб.,\n"
                        f"введено {cash_end_f:.2f} руб. "
                        f"(разница {diff_f:+.2f} руб.).\n\n"
                        "Сохранить всё равно? Расхождение будет отмечено в отчётах."
                    ),
                }

    if update_end or (update_start and shift.cash_end is not None):
        apply_cash_mismatch(shift)

    await add_log(
        session,
        user,
        "cash.update" + (".forced_mismatch" if mismatch and force_save else ""),
        "shift",
        shift.id,
        f"start={shift.cash_start} end={shift.cash_end} expected={expected} mismatch={mismatch}",
    )
    await session.commit()
    return {
        "needs_confirmation": False,
        **shift_summary_dto(await get_shift(session, shift_id), detailed=True),
    }


async def refresh_shift_cash_mismatch(session: AsyncSession, shift_id: int) -> None:
    await session.flush()
    session.expire_all()
    shift = await get_shift(session, shift_id)
    apply_cash_mismatch(shift)


async def _events_for(session: AsyncSession, shift_id: int, employee_id: int) -> list[AttendanceEvent]:
    return list(
        (
            await session.execute(
                select(AttendanceEvent)
                .where(
                    AttendanceEvent.shift_id == shift_id,
                    AttendanceEvent.employee_id == employee_id,
                )
                .order_by(AttendanceEvent.id)
            )
        ).scalars().all()
    )


async def _absence_for(session: AsyncSession, shift_id: int, employee_id: int) -> Absence | None:
    return (
        await session.execute(
            select(Absence).where(Absence.shift_id == shift_id, Absence.employee_id == employee_id)
        )
    ).scalar_one_or_none()


def _events_payload(events: list[AttendanceEvent]) -> list[dict]:
    return [
        {
            "id": ev.id,
            "type": ev.event_type,
            "time": fmt_time(ev.event_time),
        }
        for ev in events
    ]


def _next_action(events: list[AttendanceEvent], absence: Absence | None) -> str | None:
    if absence is not None:
        return None
    if not events:
        return "arrival"
    if events[-1].event_type == "arrival":
        return "departure"
    return "arrival"


def _employee_attendance_payload(
    employee: Employee,
    events: list[AttendanceEvent],
    absence: Absence | None,
) -> dict:
    return {
        **employee_dto(employee),
        "employee_id": employee.id,
        "events": _events_payload(events),
        "absence": absence.kind if absence else None,
        "has_attendance": bool(events) or bool(absence),
        "has_record": bool(events) or bool(absence),
        "next_action": _next_action(events, absence),
        "arrival_time": next((fmt_time(e.event_time) for e in events if e.event_type == "arrival"), None),
        "departure_time": next(
            (fmt_time(e.event_time) for e in reversed(events) if e.event_type == "departure"),
            None,
        ),
    }


async def upsert_arrival(
    session: AsyncSession, user: User, shift_id: int, employee_id: int, arrival: str
) -> dict:
    shift = await get_shift(session, shift_id)
    assert_can_edit(user, shift)
    emp = await session.get(Employee, employee_id)
    if not emp or not emp.is_active:
        raise ValueError("Сотрудник не найден")
    if await _absence_for(session, shift_id, employee_id) is not None:
        raise ValueError("У сотрудника отмечено отсутствие — сначала снимите его")
    events = await _events_for(session, shift_id, employee_id)
    if _next_action(events, None) != "arrival":
        raise ValueError("Сейчас можно отметить только уход")
    event_time = parse_hhmm(arrival) or now_local().time().replace(second=0, microsecond=0)
    session.add(
        AttendanceEvent(
            shift_id=shift_id,
            employee_id=employee_id,
            event_type="arrival",
            event_time=event_time,
        )
    )
    await add_log(session, user, "attendance.arrival", "attendance_event", employee_id, str(event_time))
    await session.commit()
    emp = (
        await session.execute(
            select(Employee).options(selectinload(Employee.position)).where(Employee.id == employee_id)
        )
    ).scalar_one()
    events = await _events_for(session, shift_id, employee_id)
    return _employee_attendance_payload(emp, events, None)


async def upsert_departure(
    session: AsyncSession, user: User, shift_id: int, employee_id: int, departure: str
) -> dict:
    shift = await get_shift(session, shift_id)
    assert_can_edit(user, shift)
    emp = await session.get(Employee, employee_id)
    if not emp or not emp.is_active:
        raise ValueError("Сотрудник не найден")
    if await _absence_for(session, shift_id, employee_id) is not None:
        raise ValueError("У сотрудника отмечено отсутствие — сначала снимите его")
    events = await _events_for(session, shift_id, employee_id)
    if _next_action(events, None) != "departure":
        raise ValueError("Сначала отметьте приход")
    event_time = parse_hhmm(departure) or now_local().time().replace(second=0, microsecond=0)
    session.add(
        AttendanceEvent(
            shift_id=shift_id,
            employee_id=employee_id,
            event_type="departure",
            event_time=event_time,
        )
    )
    await add_log(session, user, "attendance.departure", "attendance_event", employee_id, str(event_time))
    await session.commit()
    emp = (
        await session.execute(
            select(Employee).options(selectinload(Employee.position)).where(Employee.id == employee_id)
        )
    ).scalar_one()
    events = await _events_for(session, shift_id, employee_id)
    return _employee_attendance_payload(emp, events, None)


async def clear_attendance(
    session: AsyncSession, user: User, shift_id: int, employee_id: int
) -> dict:
    shift = await get_shift(session, shift_id)
    assert_can_edit(user, shift)
    events = await _events_for(session, shift_id, employee_id)
    for ev in events:
        await session.delete(ev)
    if events:
        await add_log(session, user, "attendance.clear", "attendance_event", employee_id, "")
        await session.commit()
    return {"ok": True}


async def set_absence(
    session: AsyncSession, user: User, shift_id: int, employee_id: int, kind: str | None
) -> dict:
    shift = await get_shift(session, shift_id)
    assert_can_edit(user, shift)
    if kind not in (None, "", "day_off", "sick", "vacation"):
        raise ValueError("Неизвестный тип отсутствия")
    emp = await session.get(Employee, employee_id)
    if not emp or not emp.is_active:
        raise ValueError("Сотрудник не найден")

    existing = await _absence_for(session, shift_id, employee_id)
    if not kind:
        if existing:
            await session.delete(existing)
            await add_log(session, user, "absence.remove", "absence", existing.id, "")
    else:
        for ev in await _events_for(session, shift_id, employee_id):
            await session.delete(ev)
        if existing:
            existing.kind = kind
        else:
            session.add(Absence(shift_id=shift_id, employee_id=employee_id, kind=kind))
        await add_log(session, user, "absence.set", "absence", employee_id, kind)
    await session.commit()
    emp = (
        await session.execute(
            select(Employee).options(selectinload(Employee.position)).where(Employee.id == employee_id)
        )
    ).scalar_one()
    events = await _events_for(session, shift_id, employee_id)
    absence = await _absence_for(session, shift_id, employee_id)
    return _employee_attendance_payload(emp, events, absence)


async def create_operation(
    session: AsyncSession,
    user: User,
    shift_id: int,
    direction: str,
    category: str,
    title: str,
    comment: str,
    movements: list[dict],
) -> dict:
    shift = await get_shift(session, shift_id)
    assert_can_edit(user, shift)
    if direction not in ("income", "expense"):
        raise ValueError("Некорректное направление операции")
    if not category or not str(category).strip():
        raise ValueError("Выберите вид операции")
    if not title or not str(title).strip():
        raise ValueError("Выберите название операции")
    if not movements:
        raise ValueError("Добавьте хотя бы одно движение по денежному хранилищу")

    op = MoneyOperation(
        shift_id=shift_id,
        direction=direction,
        category=str(category).strip(),
        title=str(title).strip(),
        comment=(comment or "").strip(),
        created_by=user.id,
    )
    session.add(op)
    await session.flush()

    for mv in movements:
        vault_id = int(mv["vault_id"])
        vault = await session.get(MoneyVault, vault_id)
        if not vault or not vault.is_active:
            raise ValueError("Денежное хранилище не найдено")
        amount = parse_money(mv.get("amount"))
        if amount <= 0:
            raise ValueError("Сумма движения должна быть больше нуля")
        session.add(MoneyMovement(operation_id=op.id, vault_id=vault_id, amount=amount))

    await add_log(session, user, f"money.{direction}", "money_operation", op.id, f"{category}/{title}")
    await refresh_shift_cash_mismatch(session, shift_id)
    await session.commit()
    op = (
        await session.execute(
            select(MoneyOperation)
            .options(
                selectinload(MoneyOperation.movements).selectinload(MoneyMovement.vault)
            )
            .where(MoneyOperation.id == op.id)
        )
    ).scalar_one()
    return operation_dto(op)


async def update_operation(
    session: AsyncSession,
    user: User,
    operation_id: int,
    category: str | None = None,
    title: str | None = None,
    comment: str | None = None,
    movements: list[dict] | None = None,
) -> dict:
    op = (
        await session.execute(
            select(MoneyOperation)
            .options(
                selectinload(MoneyOperation.movements).selectinload(MoneyMovement.vault),
                selectinload(MoneyOperation.shift),
            )
            .where(MoneyOperation.id == operation_id)
        )
    ).scalar_one_or_none()
    if not op:
        raise ValueError("Операция не найдена")
    assert_can_edit(user, op.shift)
    if category is not None:
        op.category = category.strip()
    if title is not None:
        op.title = title.strip()
    if comment is not None:
        op.comment = comment.strip()
    if movements is not None:
        if not movements:
            raise ValueError("Добавьте хотя бы одно движение")
        for old in list(op.movements):
            await session.delete(old)
        await session.flush()
        for mv in movements:
            vault_id = int(mv["vault_id"])
            amount = parse_money(mv.get("amount"))
            if amount <= 0:
                raise ValueError("Сумма движения должна быть больше нуля")
            session.add(MoneyMovement(operation_id=op.id, vault_id=vault_id, amount=amount))
    await add_log(session, user, "money.update", "money_operation", op.id, "")
    await refresh_shift_cash_mismatch(session, op.shift_id)
    await session.commit()
    return operation_dto(
        (
            await session.execute(
                select(MoneyOperation)
                .options(selectinload(MoneyOperation.movements).selectinload(MoneyMovement.vault))
                .where(MoneyOperation.id == operation_id)
            )
        ).scalar_one()
    )


async def delete_operation(session: AsyncSession, user: User, operation_id: int) -> dict:
    op = (
        await session.execute(
            select(MoneyOperation).options(selectinload(MoneyOperation.shift)).where(MoneyOperation.id == operation_id)
        )
    ).scalar_one_or_none()
    if not op:
        raise ValueError("Операция не найдена")
    assert_can_edit(user, op.shift)
    shift_id = op.shift_id
    await add_log(session, user, "money.delete", "money_operation", op.id, "")
    await session.delete(op)
    await session.flush()
    await refresh_shift_cash_mismatch(session, shift_id)
    await session.commit()
    return {"ok": True}


async def delete_shift(session: AsyncSession, user: User, shift_id: int) -> dict:
    shift = await get_shift(session, shift_id)
    if not shift.is_open and user.role != "superuser":
        raise PermissionError("Обычный пользователь не может удалять закрытые смены")
    await add_log(session, user, "shift.delete", "shift", shift.id, f"date={shift.shift_date}")
    await session.delete(shift)
    await session.commit()
    return {"ok": True}


# --- Admin CRUD ---

async def admin_list(session: AsyncSession) -> dict:
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    positions = (
        await session.execute(select(Position).order_by(Position.is_active.desc(), Position.name))
    ).scalars().all()
    employees = (
        await session.execute(select(Employee).options(selectinload(Employee.position)).order_by(Employee.full_name))
    ).scalars().all()
    vaults = (await session.execute(select(MoneyVault).order_by(MoneyVault.id))).scalars().all()
    cats = (
        await session.execute(select(ExpenseCategory).options(selectinload(ExpenseCategory.items)).order_by(ExpenseCategory.sort_order))
    ).scalars().all()
    return {
        "users": [user_dto(u) for u in users],
        "positions": [position_dto(p) for p in positions],
        "employees": [employee_dto(e) for e in employees],
        "vaults": [vault_dto(v) for v in vaults],
        "expense_categories": expense_tree_dto(cats),
        "income_types": ["Оплата за сделку", "Другое"],
    }


async def create_user(session: AsyncSession, actor: User, login: str, password: str, role: str, display_name: str) -> dict:
    from .security import hash_password

    if actor.role != "superuser":
        raise PermissionError("Недостаточно прав")
    if role not in ("user", "superuser", "accountant"):
        raise ValueError("Роль должна быть user, superuser или accountant")
    if not login or not password:
        raise ValueError("Логин и пароль обязательны")
    exists = (await session.execute(select(User).where(User.login == login.strip()))).scalar_one_or_none()
    if exists:
        raise ValueError("Пользователь с таким логином уже существует")
    u = User(
        login=login.strip(),
        password_hash=hash_password(password),
        role=role,
        display_name=display_name.strip() or login.strip(),
    )
    session.add(u)
    await add_log(session, actor, "admin.user.create", "user", None, login)
    await session.commit()
    await session.refresh(u)
    return user_dto(u)


async def create_position(
    session: AsyncSession,
    actor: User,
    name: str,
    work_start_time: str | None = None,
    work_end_time: str | None = None,
    no_schedule: bool = False,
) -> dict:
    if actor.role != "superuser":
        raise PermissionError("Недостаточно прав")
    if not name.strip():
        raise ValueError("Название должности обязательно")
    start = None if no_schedule else parse_hhmm(work_start_time)
    end = None if no_schedule else parse_hhmm(work_end_time)
    if not no_schedule and (start is None or end is None):
        raise ValueError("Укажите время начала и конца рабочего дня или отметьте «без графика»")
    p = Position(
        name=name.strip(),
        work_start_time=start,
        work_end_time=end,
        no_schedule=bool(no_schedule),
        is_active=True,
    )
    session.add(p)
    await add_log(
        session,
        actor,
        "admin.position.create",
        "position",
        None,
        f"{name}; no_schedule={no_schedule}; {work_start_time}-{work_end_time}",
    )
    await session.commit()
    await session.refresh(p)
    return position_dto(p)


async def set_position_active(
    session: AsyncSession, actor: User, position_id: int, is_active: bool
) -> dict:
    if actor.role != "superuser":
        raise PermissionError("Недостаточно прав")
    p = await session.get(Position, position_id)
    if not p:
        raise ValueError("Должность не найдена")
    p.is_active = bool(is_active)
    await add_log(
        session,
        actor,
        "admin.position.archive" if not is_active else "admin.position.restore",
        "position",
        p.id,
        p.name,
    )
    await session.commit()
    await session.refresh(p)
    return position_dto(p)


async def create_employee(session: AsyncSession, actor: User, full_name: str, position_id: int | None) -> dict:
    if actor.role != "superuser":
        raise PermissionError("Недостаточно прав")
    if not full_name.strip():
        raise ValueError("ФИО обязательно")
    if position_id is not None:
        pos = await session.get(Position, position_id)
        if not pos or not pos.is_active:
            raise ValueError("Выберите активную должность")
    e = Employee(full_name=full_name.strip(), position_id=position_id, is_active=True)
    session.add(e)
    await add_log(session, actor, "admin.employee.create", "employee", None, full_name)
    await session.commit()
    e = (
        await session.execute(select(Employee).options(selectinload(Employee.position)).where(Employee.id == e.id))
    ).scalar_one()
    return employee_dto(e)


async def create_vault(session: AsyncSession, actor: User, name: str, is_cash: bool = False) -> dict:
    if actor.role != "superuser":
        raise PermissionError("Недостаточно прав")
    if not name.strip():
        raise ValueError("Название хранилища обязательно")
    v = MoneyVault(name=name.strip(), is_cash=is_cash, is_active=True)
    session.add(v)
    await add_log(session, actor, "admin.vault.create", "vault", None, name)
    await session.commit()
    await session.refresh(v)
    return vault_dto(v)


async def list_logs(
    session: AsyncSession,
    actor: User,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    if actor.role != "superuser":
        raise PermissionError("Недостаточно прав")
    q = select(ActionLog).order_by(ActionLog.created_at.desc()).limit(1000)
    rows = (await session.execute(q)).scalars().all()
    # Optional date filter on client; basic filter here by ISO date prefix
    result = []
    for row in rows:
        if date_from and row.created_at and row.created_at.date().isoformat() < date_from:
            continue
        if date_to and row.created_at and row.created_at.date().isoformat() > date_to:
            continue
        result.append(log_dto(row))
    return result


async def attendance_board(session: AsyncSession, shift_id: int) -> dict:
    shift = await get_shift(session, shift_id)
    employees = (
        await session.execute(
            select(Employee)
            .options(selectinload(Employee.position))
            .where(Employee.is_active.is_(True))
            .order_by(Employee.full_name)
        )
    ).scalars().all()
    all_events = list(
        (
            await session.execute(
                select(AttendanceEvent)
                .where(AttendanceEvent.shift_id == shift_id)
                .order_by(AttendanceEvent.id)
            )
        ).scalars().all()
    )
    events_map: dict[int, list[AttendanceEvent]] = {}
    for ev in all_events:
        events_map.setdefault(ev.employee_id, []).append(ev)
    abs_map = {a.employee_id: a for a in shift.absences}
    items = [
        _employee_attendance_payload(e, events_map.get(e.id, []), abs_map.get(e.id))
        for e in employees
    ]
    return {"shift_id": shift.id, "employees": items}


def assert_accountant_access(user: User) -> None:
    if user.role not in ("accountant", "superuser"):
        raise PermissionError("Доступ только для бухгалтера или суперпользователя")


async def create_accountant_entry(
    session: AsyncSession,
    user: User,
    *,
    direction: str,
    method: str,
    category: str,
    title: str,
    comment: str = "",
    counterparty: str = "",
    amount: Any = None,
) -> dict:
    assert_accountant_access(user)
    if direction not in ("income", "expense"):
        raise ValueError("Некорректное направление")
    if method not in ("cash", "accrual"):
        raise ValueError("Метод должен быть cash или accrual")
    if not category or not str(category).strip():
        raise ValueError("Выберите вид операции")
    if not title or not str(title).strip():
        raise ValueError("Укажите название операции")
    value = parse_money(amount)
    if value <= 0:
        raise ValueError("Сумма должна быть больше нуля")

    category_s = str(category).strip()
    title_s = str(title).strip()
    comment_s = (comment or "").strip()
    counterparty_s = (counterparty or "").strip()

    if method == "cash":
        op = AccountantOperation(
            direction=direction,
            category=category_s,
            title=title_s,
            comment=comment_s,
            counterparty=counterparty_s,
            amount=value,
            created_by=user.id,
        )
        session.add(op)
        await session.flush()
        await add_log(
            session,
            user,
            f"accountant.{direction}.cash",
            "accountant_operation",
            op.id,
            f"{category_s}/{title_s} {value}",
        )
        await session.commit()
        await session.refresh(op)
        return {"type": "operation", "item": accountant_operation_dto(op)}

    kind = "receivable" if direction == "income" else "payable"
    debt = Debt(
        kind=kind,
        direction=direction,
        category=category_s,
        title=title_s,
        comment=comment_s,
        counterparty=counterparty_s,
        amount=value,
        created_by=user.id,
    )
    session.add(debt)
    await session.flush()
    await add_log(
        session,
        user,
        f"accountant.{direction}.accrual",
        "debt",
        debt.id,
        f"{kind} {category_s}/{title_s} {value}",
    )
    await session.commit()
    await session.refresh(debt)
    return {"type": "debt", "item": debt_dto(debt)}


async def list_accountant_operations(session: AsyncSession, user: User) -> list[dict]:
    assert_accountant_access(user)
    rows = (
        await session.execute(select(AccountantOperation).order_by(AccountantOperation.id.desc()))
    ).scalars().all()
    return [accountant_operation_dto(op) for op in rows]


async def delete_accountant_operation(session: AsyncSession, user: User, operation_id: int) -> dict:
    assert_accountant_access(user)
    op = await session.get(AccountantOperation, operation_id)
    if not op:
        raise ValueError("Операция не найдена")
    await add_log(session, user, "accountant.operation.delete", "accountant_operation", op.id, "")
    await session.delete(op)
    await session.commit()
    return {"ok": True}


async def list_debts(session: AsyncSession, user: User, kind: str | None = None) -> list[dict]:
    assert_accountant_access(user)
    stmt = select(Debt).order_by(Debt.id.desc())
    if kind:
        if kind not in ("receivable", "payable"):
            raise ValueError("kind должен быть receivable или payable")
        stmt = stmt.where(Debt.kind == kind)
    rows = (await session.execute(stmt)).scalars().all()
    return [debt_dto(d) for d in rows]


async def delete_debt(session: AsyncSession, user: User, debt_id: int) -> dict:
    assert_accountant_access(user)
    debt = await session.get(Debt, debt_id)
    if not debt:
        raise ValueError("Задолженность не найдена")
    await add_log(session, user, "accountant.debt.delete", "debt", debt.id, debt.kind)
    await session.delete(debt)
    await session.commit()
    return {"ok": True}
