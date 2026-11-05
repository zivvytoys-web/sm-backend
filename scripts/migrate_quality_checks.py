import sys
import os
sys.path.insert(0, os.path.abspath("."))
from sqlalchemy import text
from app.db.session import SessionLocal

def run_migration():
    db = SessionLocal()
    try:
        # 1. Create quality_reports table
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS quality_reports (
            id INT AUTO_INCREMENT PRIMARY KEY,
            asset_type VARCHAR(32) NOT NULL,
            media_asset_id INT NULL,
            content_id INT NULL,
            organization_id INT NOT NULL,
            brand_profile_id INT NULL,
            decision VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            quality_score FLOAT NOT NULL DEFAULT 100.0,
            technical_score FLOAT NOT NULL DEFAULT 100.0,
            brand_score FLOAT NOT NULL DEFAULT 100.0,
            content_accuracy_score FLOAT NOT NULL DEFAULT 100.0,
            duplicate_status VARCHAR(32) NOT NULL DEFAULT 'UNIQUE',
            similarity_score FLOAT NOT NULL DEFAULT 0.0,
            matching_asset_id INT NULL,
            matching_content_id INT NULL,
            failed_checks JSON NULL,
            issues JSON NULL,
            suggestions JSON NULL,
            match_details JSON NULL,
            metrics JSON NULL,
            retry_count INT NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_qr_org (organization_id),
            INDEX idx_qr_decision (decision),
            INDEX idx_qr_dup (duplicate_status),
            INDEX idx_qr_media (media_asset_id),
            INDEX idx_qr_content (content_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        db.execute(text(create_table_sql))
        db.commit()
        print("quality_reports table created/verified.")

        # 2. Add columns to media_assets if not exist
        existing_ma_cols = [r[0] for r in db.execute(text("DESCRIBE media_assets;")).fetchall()]
        cols_to_add_ma = [
            ("sha256_hash", "VARCHAR(64) NULL"),
            ("perceptual_hash", "VARCHAR(64) NULL"),
            ("validation_status", "VARCHAR(32) NOT NULL DEFAULT 'PENDING'"),
            ("quality_score", "FLOAT NOT NULL DEFAULT 100.0"),
            ("is_duplicate", "TINYINT(1) NOT NULL DEFAULT 0"),
            ("duplicate_of_id", "INT NULL")
        ]
        for col_name, col_def in cols_to_add_ma:
            if col_name not in existing_ma_cols:
                db.execute(text(f"ALTER TABLE media_assets ADD COLUMN {col_name} {col_def};"))
                print(f"Added column {col_name} to media_assets.")

        # Add index on sha256_hash and perceptual_hash for fast duplicate lookup
        indexes_ma = [r[2] for r in db.execute(text("SHOW INDEX FROM media_assets;")).fetchall()]
        if "idx_ma_sha256" not in indexes_ma:
            db.execute(text("CREATE INDEX idx_ma_sha256 ON media_assets (sha256_hash);"))
            print("Added idx_ma_sha256 to media_assets.")
        if "idx_ma_phash" not in indexes_ma:
            db.execute(text("CREATE INDEX idx_ma_phash ON media_assets (perceptual_hash);"))
            print("Added idx_ma_phash to media_assets.")

        # 3. Add columns to contents if not exist
        existing_c_cols = [r[0] for r in db.execute(text("DESCRIBE contents;")).fetchall()]
        cols_to_add_c = [
            ("sha256_hash", "VARCHAR(64) NULL"),
            ("validation_status", "VARCHAR(32) NOT NULL DEFAULT 'PENDING'"),
            ("quality_score", "FLOAT NOT NULL DEFAULT 100.0"),
            ("is_duplicate", "TINYINT(1) NOT NULL DEFAULT 0")
        ]
        for col_name, col_def in cols_to_add_c:
            if col_name not in existing_c_cols:
                db.execute(text(f"ALTER TABLE contents ADD COLUMN {col_name} {col_def};"))
                print(f"Added column {col_name} to contents.")

        indexes_c = [r[2] for r in db.execute(text("SHOW INDEX FROM contents;")).fetchall()]
        if "idx_c_sha256" not in indexes_c:
            db.execute(text("CREATE INDEX idx_c_sha256 ON contents (sha256_hash);"))
            print("Added idx_c_sha256 to contents.")
        if "idx_c_val_status" not in indexes_c:
            db.execute(text("CREATE INDEX idx_c_val_status ON contents (validation_status);"))
            print("Added idx_c_val_status to contents.")

        # 4. Add columns to analytics if not exist
        existing_a_cols = [r[0] for r in db.execute(text("DESCRIBE analytics;")).fetchall()]
        if "views" not in existing_a_cols:
            db.execute(text("ALTER TABLE analytics ADD COLUMN views BIGINT NOT NULL DEFAULT 0;"))
            print("Added column views to analytics.")

        db.commit()
        print("Quality Check, Analytics, and Duplicate Prevention migration completed successfully!")
    finally:
        db.close()

if __name__ == "__main__":
    run_migration()
