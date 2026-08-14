from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")  # user|superuser|accountant
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # duplicates allowed
    work_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    work_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    no_schedule: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    position_id: Mapped[Optional[int]] = mapped_column(ForeignKey("positions.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    position: Mapped[Optional[Position]] = relationship()


class MoneyVault(Base):
    __tablename__ = "money_vaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_cash: Mapped[bool] = mapped_column(Boolean, default=False)  # касса наличные


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    items: Mapped[list[ExpenseItem]] = relationship(back_populates="category")


class ExpenseItem(Base):
    __tablename__ = "expense_items"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_expense_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("expense_categories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    category: Mapped[ExpenseCategory] = relationship(back_populates="items")


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (UniqueConstraint("shift_date", name="uq_shift_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    cash_start: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    cash_end: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    cash_mismatch: Mapped[bool] = mapped_column(Boolean, default=False)
    cash_expected_end: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)

    attendances: Mapped[list[Attendance]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    absences: Mapped[list[Absence]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    operations: Mapped[list[MoneyOperation]] = relationship(
        back_populates="shift", cascade="all, delete-orphan", order_by="MoneyOperation.created_at"
    )


class Attendance(Base):
    """Legacy single-pair row; kept for migration. New writes go to AttendanceEvent."""

    __tablename__ = "attendances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)
    arrival_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    departure_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    shift: Mapped[Shift] = relationship(back_populates="attendances")
    employee: Mapped[Employee] = relationship()


class AttendanceEvent(Base):
    __tablename__ = "attendance_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # arrival|departure
    event_time: Mapped[time] = mapped_column(Time, nullable=False)

    employee: Mapped[Employee] = relationship()


class Absence(Base):
    __tablename__ = "absences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # day_off|sick|vacation

    shift: Mapped[Shift] = relationship(back_populates="absences")
    employee: Mapped[Employee] = relationship()


class MoneyOperation(Base):
    __tablename__ = "money_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # income|expense
    category: Mapped[str] = mapped_column(String(256), nullable=False)  # income type or expense category
    title: Mapped[str] = mapped_column(String(512), nullable=False)  # income subtype or expense name
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    shift: Mapped[Shift] = relationship(back_populates="operations")
    movements: Mapped[list[MoneyMovement]] = relationship(
        back_populates="operation", cascade="all, delete-orphan"
    )


class MoneyMovement(Base):
    __tablename__ = "money_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("money_operations.id"), nullable=False)
    vault_id: Mapped[int] = mapped_column(ForeignKey("money_vaults.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    operation: Mapped[MoneyOperation] = relationship(back_populates="movements")
    vault: Mapped[MoneyVault] = relationship()


class AccountantOperation(Base):
    """Денежные операции бухгалтера (кассовый метод), вне смены."""

    __tablename__ = "accountant_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # income|expense
    category: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    counterparty: Mapped[str] = mapped_column(String(256), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class Debt(Base):
    """Задолженности (метод начисления): дебиторская / кредиторская."""

    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # receivable|payable
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # income|expense
    category: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    counterparty: Mapped[str] = mapped_column(String(256), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    is_settled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    user_login: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
