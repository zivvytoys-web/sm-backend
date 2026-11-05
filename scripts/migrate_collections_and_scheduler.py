import sys
import os

# Ensure backend root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from app.db.session import engine, SessionLocal
from app.db.base import Base
# Import all models to ensure metadata is complete
import app.models

def migrate():
    print("=" * 60)
    print("MIGRATING DATABASE FOR MEDIA COLLECTIONS & CONTENT SCHEDULER")
    print("=" * 60)

    # 1. Create any missing tables using Base.metadata.create_all
    print("\n[1] Creating missing tables (collections, collection_media, schedule_rules)...")
    Base.metadata.create_all(bind=engine)
    print("  [PASS] Tables created or verified.")

    db = SessionLocal()
    try:
        # 2. Add columns to media_assets if not present
        print("\n[2] Checking and updating media_assets columns...")
        res = db.execute(text("SHOW COLUMNS FROM media_assets;")).fetchall()
        existing_media_cols = [r[0] for r in res]

        if "title" not in existing_media_cols:
            db.execute(text("ALTER TABLE media_assets ADD COLUMN title VARCHAR(255) NULL;"))
            print("  + Added title to media_assets")
        if "description" not in existing_media_cols:
            db.execute(text("ALTER TABLE media_assets ADD COLUMN description TEXT NULL;"))
            print("  + Added description to media_assets")
        if "alt_text" not in existing_media_cols:
            db.execute(text("ALTER TABLE media_assets ADD COLUMN alt_text VARCHAR(500) NULL;"))
            print("  + Added alt_text to media_assets")
        if "tags" not in existing_media_cols:
            db.execute(text("ALTER TABLE media_assets ADD COLUMN tags JSON NULL;"))
            print("  + Added tags to media_assets")

        # 3. Add columns to scheduled_posts if not present
        print("\n[3] Checking and updating scheduled_posts columns...")
        res = db.execute(text("SHOW COLUMNS FROM scheduled_posts;")).fetchall()
        existing_sp_cols = [r[0] for r in res]

        if "title" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN title VARCHAR(255) NULL;"))
            print("  + Added title to scheduled_posts")
        if "caption" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN caption TEXT NULL;"))
            print("  + Added caption to scheduled_posts")
        if "hashtags" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN hashtags JSON NULL;"))
            print("  + Added hashtags to scheduled_posts")
        if "internal_tags" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN internal_tags JSON NULL;"))
            print("  + Added internal_tags to scheduled_posts")
        if "platform" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN platform VARCHAR(50) DEFAULT 'instagram' NOT NULL;"))
            print("  + Added platform to scheduled_posts")
        if "post_format" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN post_format VARCHAR(50) DEFAULT 'post' NOT NULL;"))
            print("  + Added post_format to scheduled_posts")
        if "error_message" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN error_message TEXT NULL;"))
            print("  + Added error_message to scheduled_posts")
        if "media_asset_id" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN media_asset_id INT NULL, ADD CONSTRAINT fk_sp_media_asset FOREIGN KEY (media_asset_id) REFERENCES media_assets(id) ON DELETE SET NULL;"))
            print("  + Added media_asset_id to scheduled_posts")
        if "schedule_rule_id" not in existing_sp_cols:
            db.execute(text("ALTER TABLE scheduled_posts ADD COLUMN schedule_rule_id INT NULL, ADD CONSTRAINT fk_sp_schedule_rule FOREIGN KEY (schedule_rule_id) REFERENCES schedule_rules(id) ON DELETE SET NULL;"))
            print("  + Added schedule_rule_id to scheduled_posts")

        # Make content_id and social_account_id nullable in scheduled_posts if they aren't
        try:
            db.execute(text("ALTER TABLE scheduled_posts MODIFY COLUMN content_id INT NULL;"))
            db.execute(text("ALTER TABLE scheduled_posts MODIFY COLUMN social_account_id INT NULL;"))
            print("  [PASS] Ensured content_id and social_account_id are nullable in scheduled_posts")
        except Exception as e:
            print(f"  Note on column modification: {e}")

        db.commit()
        print("\n" + "=" * 60)
        print("MIGRATION COMPLETED SUCCESSFULLY!")
        print("=" * 60)
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
