from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

_settings = get_settings()
engine = create_async_engine(
    _settings.sqlite_url,
    echo=False,
    connect_args={"check_same_thread": False},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _migrate_positions(conn) -> None:
    """Ensure position columns and drop UNIQUE(name) if present."""
    result = await conn.execute(text("PRAGMA table_info(positions)"))
    cols = {row[1]: row for row in result.fetchall()}
    if not cols:
        return

    if "work_start_time" not in cols:
        await conn.execute(text("ALTER TABLE positions ADD COLUMN work_start_time TIME"))
    if "work_end_time" not in cols:
        await conn.execute(text("ALTER TABLE positions ADD COLUMN work_end_time TIME"))
    if "no_schedule" not in cols:
        await conn.execute(
            text("ALTER TABLE positions ADD COLUMN no_schedule BOOLEAN DEFAULT 0 NOT NULL")
        )
    if "is_active" not in cols:
        await conn.execute(
            text("ALTER TABLE positions ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL")
        )

    # Detect UNIQUE on name via index list
    idx_rows = (await conn.execute(text("PRAGMA index_list(positions)"))).fetchall()
    has_unique_name = False
    for idx in idx_rows:
        # idx: seq, name, unique, origin, partial
        if not idx[2]:
            continue
        idx_name = idx[1]
        info = (await conn.execute(text(f"PRAGMA index_info('{idx_name}')"))).fetchall()
        col_names = []
        for info_row in info:
            cid = info_row[1]
            # prefer mapping via table_info
            for col_name, col_row in cols.items():
                if col_row[0] == cid or (len(info_row) > 2 and info_row[2] == col_name):
                    col_names.append(col_name)
                    break
            else:
                if len(info_row) > 2 and info_row[2]:
                    col_names.append(info_row[2])
        if col_names == ["name"] or (len(col_names) == 1 and col_names[0] == "name"):
            has_unique_name = True
            break
        # sqlite_autoindex_positions_1 typically unique on name
        if "autoindex" in str(idx_name).lower() and idx[2]:
            has_unique_name = True
            break

    if not has_unique_name:
        return

    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    await conn.execute(
        text(
            """
            CREATE TABLE positions_new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                work_start_time TIME,
                work_end_time TIME,
                no_schedule BOOLEAN DEFAULT 0 NOT NULL,
                is_active BOOLEAN DEFAULT 1 NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            INSERT INTO positions_new (id, name, work_start_time, work_end_time, no_schedule, is_active)
            SELECT id, name, work_start_time, work_end_time,
                   COALESCE(no_schedule, 0), COALESCE(is_active, 1)
            FROM positions
            """
        )
    )
    await conn.execute(text("DROP TABLE positions"))
    await conn.execute(text("ALTER TABLE positions_new RENAME TO positions"))
    await conn.execute(text("PRAGMA foreign_keys=ON"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_positions(conn)
        await _migrate_attendance_events(conn)
        await _migrate_cash_mismatch(conn)


async def _migrate_cash_mismatch(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(shifts)"))
    cols = {row[1] for row in result.fetchall()}
    if not cols:
        return
    if "cash_mismatch" not in cols:
        await conn.execute(
            text("ALTER TABLE shifts ADD COLUMN cash_mismatch BOOLEAN DEFAULT 0 NOT NULL")
        )
    if "cash_expected_end" not in cols:
        await conn.execute(text("ALTER TABLE shifts ADD COLUMN cash_expected_end NUMERIC(14, 2)"))


async def _migrate_attendance_events(conn) -> None:
    """Create attendance_events and copy legacy attendances once."""
    tables = {
        row[0]
        for row in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
    }
    if "attendance_events" not in tables:
        return
    count = (await conn.execute(text("SELECT COUNT(*) FROM attendance_events"))).scalar() or 0
    if count > 0:
        return
    if "attendances" not in tables:
        return
    rows = (
        await conn.execute(
            text(
                "SELECT shift_id, employee_id, arrival_time, departure_time FROM attendances "
                "ORDER BY id"
            )
        )
    ).fetchall()
    for shift_id, employee_id, arrival, departure in rows:
        if arrival:
            await conn.execute(
                text(
                    "INSERT INTO attendance_events (shift_id, employee_id, event_type, event_time) "
                    "VALUES (:s, :e, 'arrival', :t)"
                ),
                {"s": shift_id, "e": employee_id, "t": arrival},
            )
        if departure:
            await conn.execute(
                text(
                    "INSERT INTO attendance_events (shift_id, employee_id, event_type, event_time) "
                    "VALUES (:s, :e, 'departure', :t)"
                ),
                {"s": shift_id, "e": employee_id, "t": departure},
            )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
