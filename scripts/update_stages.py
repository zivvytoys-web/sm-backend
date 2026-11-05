import os

path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../app/services/pipeline_service.py'))
with open(path, 'r', encoding='utf-8') as f:
    text = f.read().replace('\r\n', '\n')

old_calc = """def calculate_utc_scheduled_time(posting_time_str: str, timezone_str: str, target_date: Optional[date] = None) -> datetime:
    \"\"\"
    Converts a business local posting time (e.g. '18:00' Asia/Kolkata) to a UTC datetime.
    \"\"\"
    local_tz = pytz.timezone(timezone_str or "Asia/Kolkata")
    # Use the business timezone for the calendar date. Using the server's UTC
    # date caused evening posts to be assigned to the wrong day for some zones.
    target_d = target_date or datetime.now(local_tz).date()
    try:
        hour, minute = map(int, posting_time_str.split(":"))
    except Exception:
        hour, minute = 18, 0

    local_dt = local_tz.localize(datetime(target_d.year, target_d.month, target_d.day, hour, minute, 0))
    utc_dt = local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    # If today's posting window has passed, this run belongs to tomorrow's
    # posting window rather than an arbitrary two-hour offset.
    now_local = datetime.now(local_tz)
    if local_dt <= now_local:
        next_local = local_dt + timedelta(days=1)
        utc_dt = next_local.astimezone(pytz.UTC).replace(tzinfo=None)

    return utc_dt"""

new_calc = """def calculate_utc_scheduled_time(posting_time_str: str, timezone_str: str, target_date: Optional[Any] = None) -> datetime:
    \"\"\"
    Converts a business local posting time (e.g. '18:00' Asia/Kolkata) to a UTC datetime.
    Supports either datetime.date or 'YYYY-MM-DD' string for target_date.
    \"\"\"
    local_tz = pytz.timezone(timezone_str or "Asia/Kolkata")
    explicit_date = False
    if isinstance(target_date, str) and target_date.strip():
        try:
            target_d = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
            explicit_date = True
        except Exception:
            target_d = datetime.now(local_tz).date()
    elif isinstance(target_date, date):
        target_d = target_date
        explicit_date = True
    else:
        target_d = datetime.now(local_tz).date()

    try:
        hour, minute = map(int, posting_time_str.split(":"))
    except Exception:
        hour, minute = 18, 0

    local_dt = local_tz.localize(datetime(target_d.year, target_d.month, target_d.day, hour, minute, 0))
    utc_dt = local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    now_local = datetime.now(local_tz)
    # Only auto-roll over to tomorrow if target_date was not explicitly specified by user
    if not explicit_date and local_dt <= now_local:
        next_local = local_dt + timedelta(days=1)
        utc_dt = next_local.astimezone(pytz.UTC).replace(tzinfo=None)

    return utc_dt"""

if old_calc in text:
    text = text.replace(old_calc, new_calc)

# Update stage tracking during step 2
old_step2 = """        # 3. Step 2: Auto Topic & Creative Brief
        logger.info(f"[Pipeline] Synthesizing creative brief for brand '{brand.name}'...")"""

new_step2 = """        # 3. Step 2: Auto Topic & Creative Brief
        run_record.stage = "Analyzing Brand"
        run_record.progress_percent = 25
        db.commit()
        logger.info(f"[Pipeline] Synthesizing creative brief for brand '{brand.name}'...")"""

if old_step2 in text:
    text = text.replace(old_step2, new_step2)

# Update stage tracking during step 3
old_step3 = """        # 4. Step 3: Parallel Generation (Text + Images + Video)
        logger.info(f"[Pipeline] Launching parallel asset generation for run {run_record.id}...")"""

new_step3 = """        # 4. Step 3: Parallel Generation (Text + Images + Video)
        run_record.stage = "Generating Content"
        run_record.progress_percent = 50
        db.commit()
        logger.info(f"[Pipeline] Launching parallel asset generation for run {run_record.id}...")"""

if old_step3 in text:
    text = text.replace(old_step3, new_step3)

# Update stage tracking during step 4
old_step4 = """        # 5. Step 4: Quality & Policy Check
        platform_copy_dict = captions_res.model_dump()"""

new_step4 = """        # 5. Step 4: Quality & Policy Check
        run_record.stage = "Validating Content"
        run_record.progress_percent = 85
        db.commit()
        platform_copy_dict = captions_res.model_dump()"""

assert old_step4 in text, "old_step4 not found"
text = text.replace(old_step4, new_step4)

# Update post status creation for independent auto_approve and auto_schedule
old_post_create = """        # 6. Step 5: Post & Platform Variants Creation
        utc_sched_time = calculate_utc_scheduled_time(
            posting_time_str=schedule.posting_time,
            timezone_str=schedule.timezone,
            # Let the helper derive the date in the business timezone.
            target_date=None
        )

        initial_post_status = "scheduled" if (schedule.auto_approve and quality_audit.passed) else "pending_approval"

        auto_post = AutonomousPost(
            brief_id=brief_model.id,
            brand_profile_id=business_id,
            status=initial_post_status,
            scheduled_time=utc_sched_time,
            quality_report=quality_audit.to_dict(),
            auto_approved=(schedule.auto_approve and quality_audit.passed)
        )"""

new_post_create = """        # 6. Step 5: Post & Platform Variants Creation
        utc_sched_time = calculate_utc_scheduled_time(
            posting_time_str=schedule.posting_time,
            timezone_str=schedule.timezone,
            target_date=getattr(schedule, "posting_date", None)
        )

        is_approved = bool(schedule.auto_approve and quality_audit.passed)
        auto_schedule_enabled = getattr(schedule, "auto_schedule", True)

        if is_approved:
            if auto_schedule_enabled:
                initial_post_status = "scheduled"
            else:
                initial_post_status = "approved"
        else:
            initial_post_status = "pending_approval"

        auto_post = AutonomousPost(
            brief_id=brief_model.id,
            brand_profile_id=business_id,
            status=initial_post_status,
            scheduled_time=utc_sched_time,
            quality_report=quality_audit.to_dict(),
            auto_approved=is_approved
        )"""

assert old_post_create in text, "old_post_create not found"
text = text.replace(old_post_create, new_post_create)

# Update completion stage
old_complete = """        # 7. Step 6: Finalize Pipeline Run
        run_record.status = "completed"
        run_record.finished_at = datetime.utcnow()
        run_record.quality_score = quality_audit.score"""

new_complete = """        # 7. Step 6: Finalize Pipeline Run
        run_record.status = "completed"
        run_record.stage = "Scheduled" if initial_post_status == "scheduled" else "Awaiting Approval" if initial_post_status == "pending_approval" else "Approved"
        run_record.progress_percent = 100
        run_record.finished_at = datetime.utcnow()
        run_record.quality_score = quality_audit.score"""

assert old_complete in text, "old_complete not found"
text = text.replace(old_complete, new_complete)

# Update failure stage
old_fail = """        if run_record:
            run_record.status = "failed"
            run_record.error = str(e)"""

new_fail = """        if run_record:
            run_record.status = "failed"
            run_record.stage = "Failed"
            run_record.error = str(e)"""

if old_fail in text:
    text = text.replace(old_fail, new_fail)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated pipeline_service.py with stage tracking and approval logic successfully!")
