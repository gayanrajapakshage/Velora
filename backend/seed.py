"""Create tables from init.sql and seed tour dates with 8x12 seat grids."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from db import get_engine, get_session
from models import Seat, Showing, Venue

ROW_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"]
COLS = 12

# Invented 96-seat rooms. Stadium names would not match this grid.
# old_title is only used to rename a leftover showing from an earlier seed.
TOURS = [
    {
        "old_title": "Rear Window (1954) — 70mm Revival",
        "venue": "Coral Annex",
        "city": "Miami, FL",
        "title": "Chappell Roan: The Midwest Princess Tour",
        "auditorium": "Pink Room",
        "starts_at": datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc),
    },
    {
        "venue": "The Ribbon Room",
        "city": "Nashville, TN",
        "title": "Taylor Swift: The Eras Tour",
        "auditorium": "Gallery",
        "starts_at": datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc),
    },
    {
        "venue": "Nightjar Hall",
        "city": "Chicago, IL",
        "title": "Beyonce: Renaissance World Tour",
        "auditorium": "Gold Chamber",
        "starts_at": datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc),
    },
    {
        "venue": "Glass Orchid",
        "city": "Austin, TX",
        "title": "Olivia Rodrigo: GUTS World Tour",
        "auditorium": "Main Room",
        "starts_at": datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
    },
    {
        "venue": "Silver Finch",
        "city": "Atlanta, GA",
        "title": "Harry Styles: Love On Tour",
        "auditorium": "Salon",
        "starts_at": datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc),
    },
    {
        "venue": "The Marigold",
        "city": "Portland, OR",
        "title": "Billie Eilish: Hit Me Hard and Soft",
        "auditorium": "Black Box",
        "starts_at": datetime(2026, 12, 6, 3, 30, tzinfo=timezone.utc),
    },
    {
        "venue": "Velvet Parlor",
        "city": "Brooklyn, NY",
        "title": "Sabrina Carpenter: Short n' Sweet",
        "auditorium": "Parlor Floor",
        "starts_at": datetime(2026, 12, 12, 1, 0, tzinfo=timezone.utc),
    },
    {
        "venue": "Amber Vault",
        "city": "Denver, CO",
        "title": "Charli XCX: Sweat",
        "auditorium": "Lower Hall",
        "starts_at": datetime(2026, 12, 20, 3, 0, tzinfo=timezone.utc),
    },
]


def apply_schema() -> None:
    sql = Path(__file__).with_name("init.sql").read_text()
    engine = get_engine()
    with engine.begin() as conn:
        # psycopg2 runs the whole script in one call, inside one
        # transaction. Splitting on ";" would break on semicolons that
        # appear inside SQL comments.
        conn.exec_driver_sql(sql)


def seed_showing() -> None:
    with get_session() as session:
        for tour in TOURS:
            titles = [tour["title"]]
            if tour.get("old_title"):
                titles.append(tour["old_title"])
            existing = session.scalar(
                select(Showing).where(Showing.title.in_(titles))
            )
            if existing:
                venue = session.get(Venue, existing.venue_id)
                if venue:
                    venue.name = tour["venue"]
                    venue.city = tour["city"]
                existing.title = tour["title"]
                existing.auditorium = tour["auditorium"]
                existing.starts_at = tour["starts_at"]
                print(f"updated showing {existing.id}: {existing.title}")
                continue

            venue = Venue(name=tour["venue"], city=tour["city"])
            session.add(venue)
            session.flush()

            showing = Showing(
                venue_id=venue.id,
                title=tour["title"],
                auditorium=tour["auditorium"],
                starts_at=tour["starts_at"],
                row_count=len(ROW_LABELS),
                col_count=COLS,
            )
            session.add(showing)
            session.flush()

            seats = [
                Seat(
                    showing_id=showing.id,
                    seat_id=f"{label}{col}",
                    row_label=label,
                    row_index=row_index,
                    col_index=col,
                    status="available",
                )
                for row_index, label in enumerate(ROW_LABELS)
                for col in range(1, COLS + 1)
            ]
            session.add_all(seats)
            print(f"seeded {venue.name}: {showing.title} ({len(seats)} seats)")

        session.commit()


if __name__ == "__main__":
    apply_schema()
    seed_showing()
