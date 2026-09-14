"""
Skema Database SQLAlchemy untuk Sistem Pita Media.
Mendukung pelacakan lengkap: Jobs, Contents, Provenance, QC, Publications,
PublishingReceipts, ABExperiments, Metrics, Cost, Audit.
"""

from datetime import datetime, timezone
import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    JSON,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    idempotency_key = Column(String(64), unique=True, nullable=False, index=True)
    pilar = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="PENDING", index=True)
    retry_count = Column(Integer, default=0)
    is_exploration = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    contents = relationship("Content", back_populates="job", cascade="all, delete-orphan")
    receipts = relationship("PublishingReceipt", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Job id={self.id} pilar={self.pilar} status={self.status}>"


class Content(Base):
    __tablename__ = "contents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False, index=True)
    pilar = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    caption = Column(Text, nullable=False)
    media_type = Column(String(30), nullable=False)  # 'video' atau 'carousel'
    media_paths = Column(JSON, nullable=False, default=list)  # List path file
    extra_metadata = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    # Relationships
    job = relationship("Job", back_populates="contents")
    provenance = relationship("Provenance", back_populates="content", uselist=False, cascade="all, delete-orphan")
    qc_records = relationship("QCRecord", back_populates="content", cascade="all, delete-orphan")
    publications = relationship("Publication", back_populates="content", cascade="all, delete-orphan")
    receipts = relationship("PublishingReceipt", back_populates="content", cascade="all, delete-orphan")
    metrics = relationship("PerformanceMetric", back_populates="content", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Content id={self.id} pilar={self.pilar} title={self.title[:30]}>"


class Provenance(Base):
    __tablename__ = "provenance"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), unique=True, nullable=False)
    prompt_version = Column(String(50), default="1.0.0")
    raw_prompts = Column(JSON, nullable=False, default=dict)
    model_name = Column(String(100), nullable=False)
    model_version = Column(String(50), nullable=False)
    veo_params = Column(JSON, nullable=True, default=dict)
    ffmpeg_params = Column(JSON, nullable=True, default=dict)
    seed = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    content = relationship("Content", back_populates="provenance")


class QCRecord(Base):
    __tablename__ = "qc_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=False, index=True)
    iteration_number = Column(Integer, default=1, nullable=False)
    verdict = Column(String(50), nullable=False)  # PASSED, REPAIR_REQUIRED, BLOCKED, NEEDS_ATTENTION
    total_score = Column(Float, nullable=False)
    scores_breakdown = Column(JSON, nullable=False, default=dict)
    feedback_text = Column(Text, nullable=False)
    repair_plan = Column(Text, nullable=True)
    diff_before_after = Column(JSON, nullable=True)
    safety_gate_passed = Column(Boolean, default=True, nullable=False)
    safety_violations = Column(JSON, nullable=True, default=list)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    content = relationship("Content", back_populates="qc_records")


class Publication(Base):
    __tablename__ = "publications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)  # facebook, instagram, threads, mock
    post_url = Column(String(500), nullable=True)
    remote_post_id = Column(String(150), nullable=True)
    external_post_id = Column(String(150), nullable=True)
    publish_status = Column(String(50), nullable=False, default="PENDING")
    verification_hash = Column(String(64), nullable=True, index=True)
    published_at = Column(DateTime, default=utc_now, nullable=False)
    verified_at = Column(DateTime, nullable=True)

    content = relationship("Content", back_populates="publications")


class PublishingReceipt(Base):
    __tablename__ = "publishing_receipts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=True, index=True)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=True, index=True)
    platform = Column(String(50), nullable=False, index=True)  # facebook, instagram, threads, mock
    external_post_id = Column(String(150), nullable=True, index=True)
    post_id = Column(String(150), nullable=True)
    permalink = Column(String(500), nullable=True)
    publish_time = Column(DateTime, default=utc_now, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    status = Column(String(50), nullable=False, default="PUBLISHING", index=True)  # PUBLISHING, PUBLISHED, VERIFIED, FAILED, WAITING, NEEDS_ATTENTION, SIMULATED_SUCCESS
    app_mode = Column(String(20), default="DRY_RUN")
    attempt_count = Column(Integer, default=1, nullable=False)
    verification_status = Column(String(50), default="PENDING")  # VERIFIED, PENDING, FAILED
    verified = Column(Boolean, default=False)
    metrics = Column(JSON, nullable=True, default=dict)
    response_metadata = Column(JSON, nullable=True, default=dict)
    error_message = Column(Text, nullable=True)

    content = relationship("Content", back_populates="receipts")
    job = relationship("Job", back_populates="receipts")


class ABExperiment(Base):
    __tablename__ = "ab_experiments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    experiment_type = Column(String(50), nullable=True)
    hypothesis = Column(Text, nullable=False)
    variable_tested = Column(String(50), nullable=True, default="hook")  # hook, title, slides_count, cta, visual_style, posting_time
    content_id_a = Column(String(36), nullable=True)
    content_id_b = Column(String(36), nullable=True)
    control_payload = Column(JSON, nullable=True, default=dict)
    variant_payload = Column(JSON, nullable=True, default=dict)
    metrics_a = Column(JSON, nullable=True, default=dict)
    metrics_b = Column(JSON, nullable=True, default=dict)
    primary_metric = Column(String(50), default="likes")
    sample_size = Column(Integer, default=10)
    status = Column(String(50), default="RUNNING")  # RUNNING, CONCLUDED, CANCELLED, COMPLETED
    winner = Column(String(50), nullable=True)  # VARIANT_A, VARIANT_B, CONTROL, VARIANT, INCONCLUSIVE
    lesson_learned = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    concluded_at = Column(DateTime, nullable=True)


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=False, index=True)
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    retention_rate = Column(Float, default=0.0)
    profitability_score = Column(Float, default=0.0)
    views_count = Column(Integer, default=0)
    likes_count = Column(Integer, default=0)
    shares_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    calculated_roi_score = Column(Float, default=1.0)
    snapshot_window = Column(String(20), default="24h")  # 1h, 6h, 24h, 72h, 7d, 30d
    recorded_at = Column(DateTime, default=utc_now, nullable=False)
    captured_at = Column(DateTime, default=utc_now, nullable=False)

    content = relationship("Content", back_populates="metrics")


class CostRecord(Base):
    __tablename__ = "cost_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(String(36), nullable=True, index=True)
    service = Column(String(50), nullable=False)  # gemini_text, xai_grok, veo_video, imagen_image
    token_count = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0.0)
    estimated_cost_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    level = Column(String(20), default="INFO", nullable=False)  # INFO, WARNING, ERROR, CRITICAL
    component = Column(String(100), nullable=False, index=True)
    job_id = Column(String(36), nullable=True, index=True)
    content_id = Column(String(36), nullable=True)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)



class LongTermMemoryItem(Base):
    __tablename__ = "long_term_memories"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=True, index=True)
    pilar = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    theme = Column(String(100), nullable=True)
    subtheme = Column(String(100), nullable=True)
    characters = Column(JSON, nullable=True, default=list)
    story_plot = Column(Text, nullable=True)
    transformation_type = Column(String(100), nullable=True)
    miniature_object = Column(String(150), nullable=True)
    creation_materials = Column(JSON, nullable=True, default=list)
    hook_text = Column(Text, nullable=True)
    hook_type = Column(String(50), nullable=True)  # question, cliffhanger, shocking_fact, curiosity_gap, emotional_relatable
    visual_style = Column(String(100), nullable=True)
    music_recommendation = Column(String(150), nullable=True)
    platforms_published = Column(JSON, nullable=True, default=list)
    posting_time_utc = Column(DateTime, nullable=True)
    performance_summary = Column(JSON, nullable=True, default=dict)
    lessons_learned = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True, default=list)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    category = Column(String(50), nullable=False, index=True)  # hook, visual_style, posting_time, slide_count, topic_fatigue, pilar_trend, model_qc, cost_roi, emotional_trigger
    title = Column(String(255), nullable=False)
    insight_text = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)
    sample_size = Column(Integer, default=0, nullable=False)
    confidence_score = Column(Float, default=0.5, nullable=False)  # 0.0 - 1.0
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    source_metrics = Column(JSON, nullable=True, default=dict)
    status = Column(String(20), default="ACTIVE", nullable=False, index=True)  # ACTIVE, WEAK, EXPIRED
    decay_factor = Column(Float, default=1.0)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class ContentPostmortem(Base):
    __tablename__ = "content_postmortems"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=False, index=True)
    pilar = Column(String(50), nullable=False)
    trigger_reason = Column(String(50), nullable=False)  # HIGH_PERFORMER, LOW_PERFORMER, HIGH_COST, VIRAL_SHARES, HIGH_COMMENTS, ANOMALY
    performance_metrics = Column(JSON, nullable=False, default=dict)
    why_worked = Column(Text, nullable=True)
    why_failed = Column(Text, nullable=True)
    what_to_repeat = Column(Text, nullable=True)
    what_to_avoid = Column(Text, nullable=True)
    what_to_test_next = Column(Text, nullable=True)
    distilled_knowledge_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    version_num = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    pilar_distribution = Column(JSON, nullable=False)
    posting_schedule = Column(JSON, nullable=True, default=dict)
    hook_strategy = Column(JSON, nullable=True, default=dict)
    content_style = Column(JSON, nullable=True, default=dict)
    provider_routing = Column(JSON, nullable=True, default=dict)
    experimentation_policy = Column(JSON, nullable=True, default=dict)
    reason = Column(Text, nullable=False)
    expected_result = Column(Text, nullable=True)
    is_active = Column(Boolean, default=False, nullable=False, index=True)
    is_proven_good = Column(Boolean, default=False)
    created_by = Column(String(50), default="SYSTEM_LEARNING")  # SYSTEM_LEARNING, ADMIN_MANUAL, AUTO_ROLLBACK
    created_at = Column(DateTime, default=utc_now, nullable=False)


class ContentPrediction(Base):
    __tablename__ = "content_predictions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    content_id = Column(String(36), ForeignKey("contents.id"), nullable=False, index=True)
    candidate_idea_title = Column(String(255), nullable=True)
    pilar = Column(String(50), nullable=False)
    predicted_engagement = Column(Float, default=0.0)
    predicted_share_score = Column(Float, default=0.0)
    predicted_retention = Column(String(20), default="MEDIUM")  # LOW, MEDIUM, HIGH, VERY_HIGH
    predicted_novelty_score = Column(Float, default=0.0)
    fatigue_risk_score = Column(Float, default=0.0)
    overall_idea_score = Column(Float, default=0.0)
    selection_reason = Column(Text, nullable=True)
    confidence_level = Column(String(20), default="MEDIUM")  # LOW, MEDIUM, HIGH, VERY_HIGH
    confidence_score = Column(Float, default=0.5)
    
    # Actual realization data after publishing
    actual_engagement = Column(Float, nullable=True)
    actual_share_score = Column(Float, nullable=True)
    actual_retention = Column(String(20), nullable=True)
    prediction_error_delta = Column(Float, nullable=True)
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class AudienceInsight(Base):
    __tablename__ = "audience_insights"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    platform = Column(String(50), nullable=False, index=True)
    raw_comment_id = Column(String(100), nullable=True)
    category = Column(String(50), nullable=False, index=True)  # POSITIVE, NEGATIVE, QUESTION, REQUEST, CONFUSION, HUMOR, EMOTIONAL_RESPONSE, CONTENT_IDEA, SPAM, OTHER
    sentiment = Column(String(20), nullable=False)  # POSITIVE, NEUTRAL, NEGATIVE
    sentiment_score = Column(Float, default=0.0)
    topic_cluster = Column(String(100), nullable=True, index=True)
    sanitized_text = Column(Text, nullable=False)
    actionable_idea = Column(Text, nullable=True)
    safety_passed = Column(Boolean, default=True)
    privacy_cleansed = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)


class PromptVersionMetric(Base):
    __tablename__ = "prompt_version_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    prompt_name = Column(String(100), nullable=False, index=True)  # e.g., ideator_prompt, cerita_narrative, mini_diorama
    version = Column(String(50), nullable=False, index=True)
    template_text = Column(Text, nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    total_invocations = Column(Integer, default=0)
    avg_qc_score = Column(Float, default=0.0)
    avg_repair_count = Column(Float, default=0.0)
    avg_cost_usd = Column(Float, default=0.0)
    avg_engagement_rate = Column(Float, default=0.0)
    avg_share_rate = Column(Float, default=0.0)
    publish_success_rate = Column(Float, default=1.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class ModelPerformanceMetric(Base):
    __tablename__ = "model_performance_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(50), nullable=False, index=True)
    model_name = Column(String(100), nullable=False, index=True)
    task_type = Column(String(50), nullable=False)  # text_generation, image_generation, video_generation, reasoning
    total_calls = Column(Integer, default=0)
    successful_calls = Column(Integer, default=0)
    failed_calls = Column(Integer, default=0)
    avg_latency_seconds = Column(Float, default=0.0)
    avg_cost_per_call_usd = Column(Float, default=0.0)
    avg_qc_score = Column(Float, default=0.0)
    repair_rate = Column(Float, default=0.0)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class AutonomyLog(Base):
    __tablename__ = "autonomy_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    previous_level = Column(String(30), nullable=False)
    new_level = Column(String(30), nullable=False)
    action_type = Column(String(50), nullable=False)  # MANUAL_CHANGE, AUTO_DOWNGRADE, MATURITY_RECOMMENDATION, ADMIN_APPROVE, ADMIN_DECLINE, ROLLBACK
    trigger_reason = Column(Text, nullable=False)
    maturity_score = Column(Float, default=0.0)
    strategy_confidence = Column(Float, default=0.0)
    changed_by = Column(String(50), default="ADMIN")  # ADMIN, SYSTEM_SAFETY_MONITOR
    details = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)


# Index gabungan untuk query performa
Index("idx_jobs_pilar_status", Job.pilar, Job.status)
Index("idx_qc_content_iteration", QCRecord.content_id, QCRecord.iteration_number)
Index("idx_receipts_content_platform", PublishingReceipt.content_id, PublishingReceipt.platform)
Index("idx_knowledge_category_status", KnowledgeItem.category, KnowledgeItem.status)
Index("idx_ltm_pilar_created", LongTermMemoryItem.pilar, LongTermMemoryItem.created_at)

