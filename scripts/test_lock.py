import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db.session import engine
from sqlalchemy import text

with engine.connect() as conn:
    res = conn.execute(text("SELECT GET_LOCK('social_ai_scheduler_leader', 0)")).scalar()
    print('Lock acquired:', res)
    rel = conn.execute(text("SELECT RELEASE_LOCK('social_ai_scheduler_leader')")).scalar()
    print('Lock released:', rel)
