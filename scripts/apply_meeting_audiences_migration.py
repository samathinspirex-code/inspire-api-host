"""Create the many-class audience table used by shared Zoom meetings."""
import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.execute(text("""
            ALTER TABLE lms_online_meetings
            ADD COLUMN IF NOT EXISTS audience_type VARCHAR(20) NOT NULL DEFAULT 'class'
        """))
        await connection.execute(text("""
            ALTER TABLE lms_online_meetings
            ADD COLUMN IF NOT EXISTS audience_label VARCHAR(255)
        """))
        await connection.execute(text("""
            CREATE TABLE IF NOT EXISTS lms_meeting_audience_classes (
                meeting_id INTEGER NOT NULL REFERENCES lms_online_meetings(meeting_id) ON DELETE CASCADE,
                class_id INTEGER NOT NULL REFERENCES lms_classes(class_id) ON DELETE CASCADE,
                PRIMARY KEY (meeting_id, class_id)
            )
        """))
        await connection.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_lms_meeting_audience_class
            ON lms_meeting_audience_classes(class_id, meeting_id)
        """))
        await connection.execute(text("""
            INSERT INTO lms_meeting_audience_classes (meeting_id, class_id)
            SELECT meeting_id, class_id FROM lms_online_meetings
            ON CONFLICT DO NOTHING
        """))


if __name__ == "__main__":
    asyncio.run(main())
