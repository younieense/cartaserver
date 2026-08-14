from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    Absence,
    AccountantOperation,
    ActionLog,
    Attendance,
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
from .security import money


def parse_money(raw: Any) -> Decimal:
    if raw is None:
        raise ValueError("Сумма обязательна")
    text = str(raw).strip().replace(" ", "").replace(",", ".")
    if not text:
        raise ValueError("Сумма обязательна")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("Некорректная сумма") from exc
    if value < 0:
        raise ValueError("Сумма должна быть неотрицательной")
    # max 2 decimal places
    if value.as_tuple().exponent < -2:
        raise ValueError("Допускается не более двух знаков после запятой")
    return value.quantize(Decimal("0.01"))


def fmt_date(d: date | None) -> str | None:
    return d.strftime("%d.%m.%Y") if d else None


def fmt_time(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


def user_dto(u: User) -> dict:
    return {
        "id": u.id,
        "login": u.login,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
    }


def vault_dto(v: MoneyVault) -> dict:
    return {"id": v.id, "name": v.name, "is_active": v.is_active, "is_cash": v.is_cash}


def position_dto(p: Position) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "work_start_time": fmt_time(p.work_start_time),
        "work_end_time": fmt_time(p.work_end_time),
        "no_schedule": bool(p.no_schedule),
        "is_active": bool(p.is_active),
    }


def employee_dto(e: Employee) -> dict:
    return {
        "id": e.id,
        "full_name": e.full_name,
        "position_id": e.position_id,
        "position_name": e.position.name if e.position else None,
        "is_active": e.is_active,
    }


def movement_dto(m: MoneyMovement) -> dict:
    return {
        "id": m.id,
        "vault_id": m.vault_id,
        "vault_name": m.vault.name if m.vault else "",
        "amount": money(m.amount),
    }


def operation_dto(op: MoneyOperation) -> dict:
    total = sum((m.amount for m in op.movements), Decimal("0"))
    return {
        "id": op.id,
        "shift_id": op.shift_id,
        "direction": op.direction,
        "category": op.category,
        "title": op.title,
        "comment": op.comment or "",
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "total": money(total),
        "movements": [movement_dto(m) for m in op.movements],
        "display": format_operation_line(op),
    }


def format_operation_line(op: MoneyOperation) -> str:
    total = sum((m.amount for m in op.movements), Decimal("0"))
    sign = "+" if op.direction == "income" else "-"
    label = "ПРИХОД" if op.direction == "income" else "РАСХОД"

    if op.direction == "income":
        description = (op.category or op.title or "").strip()
    else:
        # вид расхода + название
        parts_desc = [p for p in [(op.category or "").strip(), (op.title or "").strip()] if p]
        if len(parts_desc) == 2 and parts_desc[0] == parts_desc[1]:
            description = parts_desc[0]
        else:
            description = " / ".join(parts_desc)

    if op.comment and op.comment.strip():
        description = f"{description} ({op.comment.strip()})" if description else f"({op.comment.strip()})"

    header = f"{label}: {sign}{float(total):.2f} руб."
    if description:
        header = f"{header} {description}"

    lines = [header]
    for m in op.movements:
        amount = money(m.amount) or 0
        vault = m.vault.name if m.vault else "?"
        lines.append(f"    {sign}{amount:.2f} {vault}")
    return "\n".join(lines)


def attendance_dto(a: Attendance) -> dict:
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "full_name": a.employee.full_name if a.employee else "",
        "arrival_time": fmt_time(a.arrival_time),
        "departure_time": fmt_time(a.departure_time),
        "has_record": a.arrival_time is not None or a.departure_time is not None,
    }


def absence_dto(a: Absence) -> dict:
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "full_name": a.employee.full_name if a.employee else "",
        "kind": a.kind,
    }


def cash_income_total(shift: Shift) -> Decimal:
    """Sum of income movements into cash vault minus expense from cash vault."""
    total = Decimal("0")
    for op in shift.operations:
        for m in op.movements:
            if not m.vault or not m.vault.is_cash:
                continue
            if op.direction == "income":
                total += m.amount
            else:
                total -= m.amount
    return total


def expected_cash_end(shift: Shift) -> Decimal | None:
    """Касса начало + приходы в кассу − расходы из кассы."""
    if shift.cash_start is None:
        return None
    return Decimal(str(shift.cash_start)) + cash_income_total(shift)


def apply_cash_mismatch(shift: Shift) -> None:
    """Пересчитать и записать признак расхождения кассы на конец дня."""
    expected = expected_cash_end(shift)
    shift.cash_expected_end = expected
    if shift.cash_start is None or shift.cash_end is None or expected is None:
        shift.cash_mismatch = False
        return
    entered = Decimal(str(shift.cash_end)).quantize(Decimal("0.01"))
    expected_q = expected.quantize(Decimal("0.01"))
    shift.cash_mismatch = entered != expected_q


def shift_summary_dto(shift: Shift, *, detailed: bool = False) -> dict:
    cash_start = money(shift.cash_start)
    cash_end = money(shift.cash_end)
    income = cash_income_total(shift)
    expected = expected_cash_end(shift)
    expected_f = money(expected)
    if shift.cash_start is not None and shift.cash_end is not None and expected is not None:
        mismatch = Decimal(str(shift.cash_end)).quantize(Decimal("0.01")) != expected.quantize(
            Decimal("0.01")
        )
    else:
        mismatch = bool(getattr(shift, "cash_mismatch", False))
    data = {
        "id": shift.id,
        "shift_date": shift.shift_date.isoformat(),
        "shift_date_display": fmt_date(shift.shift_date),
        "title": f"Смена от {fmt_date(shift.shift_date)}",
        "is_open": shift.is_open,
        "opened_at": shift.opened_at.isoformat() if shift.opened_at else None,
        "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
        "cash_start": cash_start,
        "cash_end": cash_end,
        "cash_start_set": shift.cash_start is not None,
        "cash_end_set": shift.cash_end is not None,
        "cash_mismatch": mismatch,
        "cash_expected_end": expected_f,
        "cash_difference": (
            money(Decimal(str(shift.cash_end)) - expected)
            if shift.cash_end is not None and expected is not None
            else None
        ),
    }
    if detailed:
        data["cash_flow_summary"] = {
            "cash_start": cash_start,
            "cash_movements": money(income),
            "expected_cash_end": expected_f,
            "cash_end": cash_end,
            "cash_mismatch": mismatch,
            "cash_difference": data["cash_difference"],
            "formula_text": _formula_text(cash_start, income, cash_end, expected_f, mismatch),
        }
        data["attendances"] = [attendance_dto(a) for a in sorted(shift.attendances, key=lambda x: x.employee_id)]
        data["absences"] = [absence_dto(a) for a in shift.absences]
        data["operations"] = [
            operation_dto(op)
            for op in sorted(shift.operations, key=lambda o: o.created_at or datetime.min)
        ]
    return data


def _formula_text(cash_start, income, cash_end, expected=None, mismatch: bool = False) -> str:
    cs = f"{cash_start:.2f}" if cash_start is not None else "—"
    inc = f"{float(income):+.2f}"
    ce = f"{cash_end:.2f}" if cash_end is not None else "—"
    text = (
        f"Касса начало {cs} + движения по «Касса, наличные» {inc} "
        f"= ожидаемый конец {f'{expected:.2f}' if expected is not None else '—'}; "
        f"факт конец дня {ce}"
    )
    if mismatch and cash_end is not None and expected is not None:
        diff = float(cash_end) - float(expected)
        text += f" ⚠ РАСХОЖДЕНИЕ {diff:+.2f}"
    return text


def log_dto(log: ActionLog) -> dict:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_login": log.user_login,
        "action": log.action,
        "entity": log.entity,
        "entity_id": log.entity_id,
        "details": log.details,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def expense_tree_dto(categories: list[ExpenseCategory]) -> list[dict]:
    result = [{"name": "Инкассация", "items": ["Инкассация"]}]
    for cat in sorted(categories, key=lambda c: c.sort_order):
        items = [i.name for i in cat.items]
        if not items:
            items = [cat.name]
        result.append({"name": cat.name, "items": items})
    return result


def accountant_operation_dto(op: AccountantOperation) -> dict:
    direction_label = "Приход" if op.direction == "income" else "Расход"
    return {
        "id": op.id,
        "direction": op.direction,
        "direction_label": direction_label,
        "method": "cash",
        "method_label": "Кассовый",
        "category": op.category,
        "title": op.title,
        "comment": op.comment or "",
        "counterparty": op.counterparty or "",
        "amount": money(op.amount),
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "created_by": op.created_by,
        "display_text": (
            f"{direction_label} · {op.category}"
            + (f" / {op.title}" if op.title and op.title != op.category else "")
            + f"\n{float(op.amount):.2f} руб."
            + (f" · {op.counterparty}" if op.counterparty else "")
            + (f"\n{op.comment}" if op.comment else "")
        ),
    }


def debt_dto(d: Debt) -> dict:
    kind_label = "Дебиторская" if d.kind == "receivable" else "Кредиторская"
    direction_label = "Приход" if d.direction == "income" else "Расход"
    return {
        "id": d.id,
        "kind": d.kind,
        "kind_label": kind_label,
        "direction": d.direction,
        "direction_label": direction_label,
        "category": d.category,
        "title": d.title,
        "comment": d.comment or "",
        "counterparty": d.counterparty or "",
        "amount": money(d.amount),
        "is_settled": bool(d.is_settled),
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "created_by": d.created_by,
        "display_text": (
            f"{kind_label} · {d.category}"
            + (f" / {d.title}" if d.title and d.title != d.category else "")
            + f"\n{float(d.amount):.2f} руб."
            + (f" · {d.counterparty}" if d.counterparty else "")
            + (f"\n{d.comment}" if d.comment else "")
        ),
    }
