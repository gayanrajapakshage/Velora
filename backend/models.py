from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db import Base

SEAT_STATUSES = ("available", "held", "booked")


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)


class Showing(Base):
    __tablename__ = "showings"

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    auditorium: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    row_count: Mapped[int] = mapped_column(nullable=False)
    col_count: Mapped[int] = mapped_column(nullable=False)


class Seat(Base):
    """One physical seat in one showing.

    status carries the durable outcome only ('available' or 'booked').
    Holds are ephemeral: they live in Redis under a real TTL and are
    never written here, so no restart can resurrect a stale hold.
    """

    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("showing_id", "seat_id"),
        CheckConstraint(
            "status IN ('available', 'held', 'booked')", name="seats_status_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    showing_id: Mapped[int] = mapped_column(
        ForeignKey("showings.id"), nullable=False
    )
    seat_id: Mapped[str] = mapped_column(Text, nullable=False)
    row_label: Mapped[str] = mapped_column(Text, nullable=False)
    row_index: Mapped[int] = mapped_column(nullable=False)
    col_index: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="available")


class Booking(Base):
    """Who booked which seat, and when.

    The unique constraint on (showing_id, seat_id) is a second line
    of defense behind the Lua script. Redis decides who wins a contested
    seat; if that ever regressed, Postgres would reject the duplicate
    rather than sell one seat twice.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("showing_id", "seat_id"),
        ForeignKeyConstraint(
            ["showing_id", "seat_id"], ["seats.showing_id", "seats.seat_id"]
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    showing_id: Mapped[int] = mapped_column(nullable=False)
    seat_id: Mapped[str] = mapped_column(Text, nullable=False)
    holder_id: Mapped[str] = mapped_column(Text, nullable=False)
    holder_name: Mapped[str] = mapped_column(Text, nullable=False)
    booked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
