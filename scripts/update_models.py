import os

path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../app/models/autonomous_pipeline.py'))
with open(path, 'r', encoding='utf-8') as f:
    text = f.read().replace('\r\n', '\n')

old_sched = """    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    generation_time = Column(String(10), default="09:00", nullable=False)  # HH:MM (e.g. 09:00)
    posting_time = Column(String(10), default="18:00", nullable=False)     # HH:MM (e.g. 18:00)
    timezone = Column(String(50), default="Asia/Kolkata", nullable=False)  # e.g. Asia/Kolkata
    
    auto_approve = Column(Boolean, default=False, nullable=False)          # If true, auto-schedules passing posts
    is_active = Column(Boolean, default=True, nullable=False)              # Master toggle for autonomous pipeline
    
    target_platforms = Column(JSON, default=lambda: ["instagram", "facebook", "linkedin", "youtube"], nullable=False)
    notification_settings = Column(JSON, default=lambda: {"in_app": True, "email": False, "whatsapp": False}, nullable=False)"""

new_sched = """    brand_profile_id = Column(Integer, ForeignKey("brand_profiles.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    generation_date = Column(String(10), nullable=True)                    # Optional future generation date (YYYY-MM-DD)
    generation_time = Column(String(10), default="09:00", nullable=False)  # HH:MM (e.g. 09:00)
    posting_date = Column(String(10), nullable=True)                       # Optional target posting date (YYYY-MM-DD)
    posting_time = Column(String(10), default="18:00", nullable=False)     # HH:MM (e.g. 18:00)
    timezone = Column(String(50), default="Asia/Kolkata", nullable=False)  # e.g. Asia/Kolkata
    
    auto_approve = Column(Boolean, default=False, nullable=False)          # If true, auto-approves passing content
    auto_schedule = Column(Boolean, default=True, nullable=False)          # Independent toggle: if true, automatically creates publishing jobs
    is_active = Column(Boolean, default=True, nullable=False)              # Master toggle for autonomous pipeline
    
    target_platforms = Column(JSON, default=lambda: ["instagram", "facebook", "linkedin", "youtube"], nullable=False)
    selected_social_account_ids = Column(JSON, default=list, nullable=True)
    notification_settings = Column(JSON, default=lambda: {"in_app": True, "email": False, "whatsapp": False}, nullable=False)"""

assert old_sched in text, "old_sched not found"
text = text.replace(old_sched, new_sched)

old_run = """    status = Column(String(30), default="pending", nullable=False)  # pending, running, completed, failed
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)"""

new_run = """    status = Column(String(30), default="pending", nullable=False)  # pending, running, completed, failed
    stage = Column(String(50), default="Initializing", nullable=False)  # Initializing, Analyzing Brand, Generating Content, Creating Media, Validating Content, Awaiting Approval, Scheduled, Publishing, Completed, Failed
    progress_percent = Column(Integer, default=0, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)"""

assert old_run in text, "old_run not found"
text = text.replace(old_run, new_run)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated app/models/autonomous_pipeline.py successfully!")
