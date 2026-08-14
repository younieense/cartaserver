from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .db import SessionLocal
from .models import User
from .security import verify_password
from . import services
from .serializers import shift_summary_dto, user_dto

logger = logging.getLogger("carta.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[WebSocket, User | None] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.connections[ws] = None

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.connections.pop(ws, None)

    async def set_user(self, ws: WebSocket, user: User | None) -> None:
        async with self._lock:
            if ws in self.connections:
                self.connections[ws] = user

    async def broadcast(self, message: dict, *, exclude: WebSocket | None = None) -> None:
        raw = json.dumps(message, ensure_ascii=False, default=str)
        async with self._lock:
            targets = list(self.connections.keys())
        dead: list[WebSocket] = []
        for ws in targets:
            if ws is exclude:
                continue
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    def connected_count(self) -> int:
        return len(self.connections)


manager = ConnectionManager()


async def _reply(ws: WebSocket, req_id: str | None, type_: str, payload: Any) -> None:
    await ws.send_text(
        json.dumps({"id": req_id, "type": type_, "payload": payload}, ensure_ascii=False, default=str)
    )


async def _error(ws: WebSocket, req_id: str | None, message: str, code: str = "error") -> None:
    await _reply(ws, req_id, "error", {"message": message, "code": code})


async def _push_shift(session, shift_id: int, exclude: WebSocket | None = None) -> None:
    shift = await services.get_shift(session, shift_id)
    payload = shift_summary_dto(shift, detailed=True)
    payload["attendances"] = await services.enrich_shift_attendances(session, shift)
    await manager.broadcast(
        {"type": "event", "event": "shift_updated", "payload": payload},
        exclude=exclude,
    )


async def handle_message(ws: WebSocket, raw: str) -> None:
    req_id = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await _error(ws, None, "Некорректный JSON")
        return

    req_id = data.get("id")
    msg_type = data.get("type")
    payload = data.get("payload") or {}

    user = manager.connections.get(ws)

    try:
        if msg_type == "ping":
            await _reply(ws, req_id, "pong", {"ok": True})
            return

        if msg_type == "auth":
            async with SessionLocal() as session:
                login = str(payload.get("login", "")).strip()
                password = str(payload.get("password", ""))
                db_user = (
                    await session.execute(select(User).where(User.login == login, User.is_active.is_(True)))
                ).scalar_one_or_none()
                if not db_user or not verify_password(password, db_user.password_hash):
                    await _error(ws, req_id, "Неверный логин или пароль", "auth_failed")
                    return
                await manager.set_user(ws, db_user)
                await services.ensure_current_shift(session)
                await services.add_log(session, db_user, "auth.login", "user", db_user.id, "")
                await session.commit()
                await _reply(ws, req_id, "auth_ok", {"user": user_dto(db_user)})
                await manager.broadcast(
                    {"type": "event", "event": "presence", "payload": {"connected": manager.connected_count()}},
                )
            return

        if user is None:
            await _error(ws, req_id, "Требуется авторизация", "unauthorized")
            return

        async with SessionLocal() as session:
            # refresh user
            db_user = await session.get(User, user.id)
            if not db_user or not db_user.is_active:
                await _error(ws, req_id, "Пользователь деактивирован", "unauthorized")
                return

            if msg_type == "get_current_shift":
                shift = await services.ensure_current_shift(session)
                payload_out = shift_summary_dto(shift, detailed=True)
                payload_out["attendances"] = await services.enrich_shift_attendances(session, shift)
                await _reply(ws, req_id, "current_shift", payload_out)
                return

            if msg_type == "get_shift":
                shift = await services.get_shift(session, int(payload["shift_id"]))
                payload_out = shift_summary_dto(shift, detailed=True)
                payload_out["attendances"] = await services.enrich_shift_attendances(session, shift)
                await _reply(ws, req_id, "shift", payload_out)
                return

            if msg_type == "list_shifts":
                detailed = db_user.role == "superuser"
                items = await services.list_shifts(session, detailed=detailed)
                await _reply(ws, req_id, "shifts", {"items": items})
                return

            if msg_type == "set_cash":
                result = await services.set_cash(
                    session,
                    db_user,
                    int(payload["shift_id"]),
                    cash_start=payload.get("cash_start"),
                    cash_end=payload.get("cash_end"),
                    update_start=bool(payload.get("update_start", True)),
                    update_end=bool(payload.get("update_end", True)),
                    force_save=bool(payload.get("force_save", False)),
                )
                if result.get("needs_confirmation"):
                    await _reply(ws, req_id, "cash_mismatch_warning", result)
                else:
                    await _reply(ws, req_id, "cash_updated", result)
                    await _push_shift(session, int(payload["shift_id"]), exclude=None)
                return

            if msg_type == "attendance_board":
                board = await services.attendance_board(session, int(payload["shift_id"]))
                await _reply(ws, req_id, "attendance_board", board)
                return

            if msg_type == "set_arrival":
                result = await services.upsert_arrival(
                    session, db_user, int(payload["shift_id"]), int(payload["employee_id"]), payload.get("time")
                )
                await _reply(ws, req_id, "attendance_updated", result)
                await _push_shift(session, int(payload["shift_id"]))
                return

            if msg_type == "set_departure":
                result = await services.upsert_departure(
                    session, db_user, int(payload["shift_id"]), int(payload["employee_id"]), payload.get("time")
                )
                await _reply(ws, req_id, "attendance_updated", result)
                await _push_shift(session, int(payload["shift_id"]))
                return

            if msg_type == "set_absence":
                result = await services.set_absence(
                    session, db_user, int(payload["shift_id"]), int(payload["employee_id"]), payload.get("kind")
                )
                await _reply(ws, req_id, "absence_updated", result)
                await _push_shift(session, int(payload["shift_id"]))
                return

            if msg_type == "create_operation":
                result = await services.create_operation(
                    session,
                    db_user,
                    int(payload["shift_id"]),
                    payload.get("direction"),
                    payload.get("category"),
                    payload.get("title"),
                    payload.get("comment", ""),
                    payload.get("movements") or [],
                )
                await _reply(ws, req_id, "operation_created", result)
                await _push_shift(session, int(payload["shift_id"]))
                return

            if msg_type == "update_operation":
                result = await services.update_operation(
                    session,
                    db_user,
                    int(payload["operation_id"]),
                    category=payload.get("category"),
                    title=payload.get("title"),
                    comment=payload.get("comment"),
                    movements=payload.get("movements"),
                )
                await _reply(ws, req_id, "operation_updated", result)
                op_shift = result.get("shift_id")
                if op_shift:
                    await _push_shift(session, op_shift)
                return

            if msg_type == "delete_operation":
                op_id = int(payload["operation_id"])
                # need shift id before delete
                from .models import MoneyOperation

                op = await session.get(MoneyOperation, op_id)
                shift_id = op.shift_id if op else None
                result = await services.delete_operation(session, db_user, op_id)
                await _reply(ws, req_id, "operation_deleted", result)
                if shift_id:
                    await _push_shift(session, shift_id)
                return

            if msg_type == "delete_shift":
                result = await services.delete_shift(session, db_user, int(payload["shift_id"]))
                await _reply(ws, req_id, "shift_deleted", result)
                await manager.broadcast({"type": "event", "event": "shifts_changed", "payload": {}})
                return

            if msg_type == "admin_list":
                if db_user.role != "superuser":
                    raise PermissionError("Недостаточно прав")
                result = await services.admin_list(session)
                await _reply(ws, req_id, "admin_list", result)
                return

            if msg_type == "create_user":
                result = await services.create_user(
                    session,
                    db_user,
                    payload.get("login", ""),
                    payload.get("password", ""),
                    payload.get("role", "user"),
                    payload.get("display_name", ""),
                )
                await _reply(ws, req_id, "user_created", result)
                return

            if msg_type == "create_position":
                result = await services.create_position(
                    session,
                    db_user,
                    payload.get("name", ""),
                    work_start_time=payload.get("work_start_time"),
                    work_end_time=payload.get("work_end_time"),
                    no_schedule=bool(payload.get("no_schedule", False)),
                )
                await _reply(ws, req_id, "position_created", result)
                return

            if msg_type == "set_position_active":
                result = await services.set_position_active(
                    session,
                    db_user,
                    int(payload["position_id"]),
                    bool(payload.get("is_active", False)),
                )
                await _reply(ws, req_id, "position_updated", result)
                return

            if msg_type == "create_employee":
                pid = payload.get("position_id")
                result = await services.create_employee(
                    session, db_user, payload.get("full_name", ""), int(pid) if pid else None
                )
                await _reply(ws, req_id, "employee_created", result)
                return

            if msg_type == "create_vault":
                result = await services.create_vault(
                    session, db_user, payload.get("name", ""), bool(payload.get("is_cash", False))
                )
                await _reply(ws, req_id, "vault_created", result)
                return

            if msg_type == "list_logs":
                result = await services.list_logs(
                    session, db_user, payload.get("date_from"), payload.get("date_to")
                )
                await _reply(ws, req_id, "logs", {"items": result})
                return

            if msg_type == "generate_report":
                from .reports import generate_report

                result = await generate_report(
                    session,
                    db_user,
                    str(payload.get("report_type", "")),
                    date_from=payload.get("date_from"),
                    date_to=payload.get("date_to"),
                )
                await _reply(ws, req_id, "report_file", result)
                return

            if msg_type == "get_refs":
                result = await services.admin_list(session)
                await _reply(
                    ws,
                    req_id,
                    "refs",
                    {
                        "vaults": [v for v in result["vaults"] if v["is_active"]],
                        "employees": [e for e in result["employees"] if e["is_active"]],
                        "positions": [p for p in result["positions"] if p.get("is_active", True)],
                        "expense_categories": result["expense_categories"],
                        "income_types": result["income_types"],
                    },
                )
                return

            if msg_type == "create_accountant_entry":
                result = await services.create_accountant_entry(
                    session,
                    db_user,
                    direction=str(payload.get("direction", "")),
                    method=str(payload.get("method", "")),
                    category=payload.get("category", ""),
                    title=payload.get("title", ""),
                    comment=payload.get("comment", ""),
                    counterparty=payload.get("counterparty", ""),
                    amount=payload.get("amount"),
                )
                reply_type = (
                    "accountant_operation_created"
                    if result.get("type") == "operation"
                    else "debt_created"
                )
                await _reply(ws, req_id, reply_type, result)
                await manager.broadcast(
                    {
                        "type": "event",
                        "event": "accountant_data_changed",
                        "payload": {"kind": result.get("type")},
                    }
                )
                return

            if msg_type == "list_accountant_operations":
                items = await services.list_accountant_operations(session, db_user)
                await _reply(ws, req_id, "accountant_operations", {"items": items})
                return

            if msg_type == "delete_accountant_operation":
                result = await services.delete_accountant_operation(
                    session, db_user, int(payload["operation_id"])
                )
                await _reply(ws, req_id, "accountant_operation_deleted", result)
                await manager.broadcast(
                    {
                        "type": "event",
                        "event": "accountant_data_changed",
                        "payload": {"kind": "operation"},
                    }
                )
                return

            if msg_type == "list_debts":
                kind = payload.get("kind")
                items = await services.list_debts(
                    session, db_user, str(kind) if kind else None
                )
                await _reply(ws, req_id, "debts", {"items": items})
                return

            if msg_type == "delete_debt":
                result = await services.delete_debt(session, db_user, int(payload["debt_id"]))
                await _reply(ws, req_id, "debt_deleted", result)
                await manager.broadcast(
                    {
                        "type": "event",
                        "event": "accountant_data_changed",
                        "payload": {"kind": "debt"},
                    }
                )
                return

            await _error(ws, req_id, f"Неизвестный тип сообщения: {msg_type}", "unknown_type")

    except PermissionError as exc:
        await _error(ws, req_id, str(exc), "forbidden")
    except ValueError as exc:
        await _error(ws, req_id, str(exc), "validation")
    except Exception as exc:
        logger.error("WS handler error: %s\n%s", exc, traceback.format_exc())
        await _error(ws, req_id, f"Внутренняя ошибка сервера: {exc}", "internal")


async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "payload": {
                        "app": "CARTA",
                        "protocol": 1,
                        "connected": True,
                    },
                },
                ensure_ascii=False,
            )
        )
        while True:
            raw = await ws.receive_text()
            await handle_message(ws, raw)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WS disconnected with error: %s", exc)
    finally:
        await manager.disconnect(ws)
        await manager.broadcast(
            {"type": "event", "event": "presence", "payload": {"connected": manager.connected_count()}}
        )
