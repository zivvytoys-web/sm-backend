import sys
from app.db.session import engine
from sqlalchemy import text

def run_migration():
    with engine.connect() as conn:
        cols = [r[0] for r in conn.execute(text("SHOW COLUMNS FROM scheduled_posts")).fetchall()]
        print("Existing columns in scheduled_posts:", cols)

        if "organization_id" not in cols:
            print("Adding organization_id...")
            conn.execute(text("ALTER TABLE scheduled_posts ADD COLUMN organization_id INT NULL"))
            conn.execute(text("ALTER TABLE scheduled_posts ADD INDEX idx_sched_org_time (organization_id, scheduled_time)"))
            conn.commit()
            print("organization_id added.")
        else:
            print("organization_id exists.")

        if "user_id" not in cols:
            print("Adding user_id...")
            conn.execute(text("ALTER TABLE scheduled_posts ADD COLUMN user_id INT NULL"))
            conn.commit()
            print("user_id added.")
        else:
            print("user_id exists.")

        # Backfill existing posts
        conn.execute(text("""
            UPDATE scheduled_posts sp
            LEFT JOIN media_assets ma ON sp.media_asset_id = ma.id
            LEFT JOIN contents c ON sp.content_id = c.id
            LEFT JOIN brand_profiles bp ON c.brand_profile_id = bp.id
            SET sp.organization_id = COALESCE(ma.organization_id, bp.organization_id, 2)
            WHERE sp.organization_id IS NULL
        """))
        conn.commit()
        print("Existing scheduled_posts backfilled.")

        # Verify backfill
        count_null = conn.execute(text("SELECT COUNT(*) FROM scheduled_posts WHERE organization_id IS NULL")).scalar()
        print("Posts with NULL organization_id:", count_null)

if __name__ == "__main__":
    run_migration()
