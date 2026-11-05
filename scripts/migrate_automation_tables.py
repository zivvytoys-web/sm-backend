import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import engine
from sqlalchemy import text

def run_migration():
    with engine.connect() as conn:
        cols = [c[0] for c in conn.execute(text('DESCRIBE business_schedules')).fetchall()]
        print('Current business_schedules columns:', cols)
        
        if 'generation_date' not in cols:
            conn.execute(text('ALTER TABLE business_schedules ADD COLUMN generation_date VARCHAR(10) NULL AFTER brand_profile_id'))
            print('Added generation_date to business_schedules')
        if 'posting_date' not in cols:
            conn.execute(text('ALTER TABLE business_schedules ADD COLUMN posting_date VARCHAR(10) NULL AFTER generation_time'))
            print('Added posting_date to business_schedules')
        if 'auto_schedule' not in cols:
            conn.execute(text('ALTER TABLE business_schedules ADD COLUMN auto_schedule TINYINT(1) NOT NULL DEFAULT 1 AFTER auto_approve'))
            print('Added auto_schedule to business_schedules')
        if 'selected_social_account_ids' not in cols:
            conn.execute(text('ALTER TABLE business_schedules ADD COLUMN selected_social_account_ids JSON NULL AFTER target_platforms'))
            print('Added selected_social_account_ids to business_schedules')

        run_cols = [c[0] for c in conn.execute(text('DESCRIBE pipeline_runs')).fetchall()]
        print('Current pipeline_runs columns:', run_cols)
        if 'stage' not in run_cols:
            conn.execute(text("ALTER TABLE pipeline_runs ADD COLUMN stage VARCHAR(50) NOT NULL DEFAULT 'Initializing' AFTER status"))
            print('Added stage to pipeline_runs')
        if 'progress_percent' not in run_cols:
            conn.execute(text('ALTER TABLE pipeline_runs ADD COLUMN progress_percent INT NOT NULL DEFAULT 0 AFTER stage'))
            print('Added progress_percent to pipeline_runs')

        conn.commit()
        print('Migration finished cleanly!')

if __name__ == '__main__':
    run_migration()
