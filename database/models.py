"""PostgreSQL schema (SQLAlchemy 2.0 models).

Conventions
- Jobber primary keys are Jobber EncodedIds (strings). jobNumber / quoteNumber /
  invoiceNumber are display values only, never keys.
- Pipedrive primary keys are Pipedrive integer IDs.
- All timestamps are stored timezone-aware in UTC.
- Every synced table keeps the raw API payload in `raw` so fields can be
  re-derived later without re-fetching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(14, 2)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def Ts(**kw):  # noqa: N802 - column factory
    return mapped_column(DateTime(timezone=True), **kw)


class Base(DeclarativeBase):
    pass


class SyncedMixin:
    raw: Mapped[dict | None] = mapped_column(JSONType)
    first_synced_at: Mapped[datetime] = Ts(default=utcnow)
    last_synced_at: Mapped[datetime] = Ts(default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# App users / auth / tokens
# ---------------------------------------------------------------------------
class AppUser(Base):
    __tablename__ = "app_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="VIEW_ONLY")  # ADMIN | MANAGEMENT | VIEW_ONLY
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = Ts()
    created_at: Mapped[datetime] = Ts(default=utcnow)
    last_login_at: Mapped[datetime | None] = Ts()


class OAuthToken(Base):
    """Encrypted OAuth tokens (Jobber rotates refresh tokens on every refresh)."""

    __tablename__ = "oauth_tokens"
    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None] = Ts()
    connected_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = Ts(default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Sync bookkeeping
# ---------------------------------------------------------------------------
class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), index=True)  # JOBBER | PIPEDRIVE | DERIVED
    entity: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = Ts(default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = Ts()
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")  # RUNNING | SUCCESS | FAILED
    records_read: Mapped[int] = mapped_column(Integer, default=0)
    records_created: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONType)


class SyncState(Base):
    """High-water marks for incremental sync. Only advanced after success."""

    __tablename__ = "sync_state"
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity: Mapped[str] = mapped_column(String(40), primary_key=True)
    watermark: Mapped[datetime | None] = Ts()
    updated_at: Mapped[datetime] = Ts(default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Jobber
# ---------------------------------------------------------------------------
class JobberPerson(SyncedMixin, Base):
    __tablename__ = "jobber_people"
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_account_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    is_account_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class JobberClient(SyncedMixin, Base):
    __tablename__ = "jobber_clients"
    client_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    company_name: Mapped[str | None] = mapped_column(String(255))
    is_company: Mapped[bool | None] = mapped_column(Boolean)
    is_lead: Mapped[bool | None] = mapped_column(Boolean)
    emails: Mapped[list | None] = mapped_column(JSONType)  # normalized lowercase
    phones: Mapped[list | None] = mapped_column(JSONType)  # normalized digits
    web_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = Ts()
    updated_at: Mapped[datetime | None] = Ts()


class JobberProperty(SyncedMixin, Base):
    __tablename__ = "jobber_properties"
    property_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    street1: Mapped[str | None] = mapped_column(String(255))
    street2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(120))
    province: Mapped[str | None] = mapped_column(String(60))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(60))
    web_uri: Mapped[str | None] = mapped_column(Text)


class JobberRequest(SyncedMixin, Base):
    __tablename__ = "jobber_requests"
    request_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(40), index=True)
    source: Mapped[str | None] = mapped_column(String(80))
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    property_id: Mapped[str | None] = mapped_column(String(80), index=True)
    salesperson_id: Mapped[str | None] = mapped_column(String(80))
    assessment_id: Mapped[str | None] = mapped_column(String(80))
    quote_ids: Mapped[list | None] = mapped_column(JSONType)
    job_ids: Mapped[list | None] = mapped_column(JSONType)
    has_quote: Mapped[bool] = mapped_column(Boolean, default=False)
    has_job: Mapped[bool] = mapped_column(Boolean, default=False)
    web_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = Ts(index=True)
    updated_at: Mapped[datetime | None] = Ts()


class JobberAssessment(SyncedMixin, Base):
    __tablename__ = "jobber_assessments"
    assessment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    property_id: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = Ts(index=True)
    end_at: Mapped[datetime | None] = Ts()
    completed_at: Mapped[datetime | None] = Ts()
    all_day: Mapped[bool | None] = mapped_column(Boolean)
    is_complete: Mapped[bool | None] = mapped_column(Boolean)
    # Derived: SCHEDULED | UNSCHEDULED | COMPLETED
    status: Mapped[str | None] = mapped_column(String(20))


class RequestAssignment(Base):
    """Users assigned to a request's assessment (= technician of the estimate)."""

    __tablename__ = "request_assignments"
    assessment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)


class JobberQuote(SyncedMixin, Base):
    __tablename__ = "jobber_quotes"
    quote_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    quote_number: Mapped[str | None] = mapped_column(String(40), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(40), index=True)
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    property_id: Mapped[str | None] = mapped_column(String(80), index=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    salesperson_id: Mapped[str | None] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime | None] = Ts(index=True)
    updated_at: Mapped[datetime | None] = Ts()
    sent_at: Mapped[datetime | None] = Ts()
    transitioned_at: Mapped[datetime | None] = Ts()
    approved_at: Mapped[datetime | None] = Ts()
    converted_at: Mapped[datetime | None] = Ts()
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax_amount: Mapped[Decimal | None] = mapped_column(Money)
    discount_amount: Mapped[Decimal | None] = mapped_column(Money)
    deposit_amount: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)
    converted_job_ids: Mapped[list | None] = mapped_column(JSONType)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_converted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    # ESTIMATE | SERVICE | UNLINKED (set by the relationship engine)
    flow_type: Mapped[str | None] = mapped_column(String(20), index=True)
    # --- Quote ORIGIN (services/quote_origin.py). Analytical only; never written to Jobber.
    # origin_flow_type: ESTIMATE | SERVICE | REVIEW_REQUIRED | OTHER
    # origin_method:    DIRECT | INFERRED | REVIEW_REQUIRED | UNLINKED
    origin_flow_type: Mapped[str | None] = mapped_column(String(20), index=True)
    origin_method: Mapped[str | None] = mapped_column(String(20), index=True)
    origin_request_id: Mapped[str | None] = mapped_column(String(80))
    origin_assessment_id: Mapped[str | None] = mapped_column(String(80))
    origin_service_job_id: Mapped[str | None] = mapped_column(String(80))
    origin_visit_id: Mapped[str | None] = mapped_column(String(80))
    origin_confidence: Mapped[float | None] = mapped_column(Float)
    origin_reason: Mapped[str | None] = mapped_column(Text)
    candidate_request_ids: Mapped[list | None] = mapped_column(JSONType)
    candidate_service_job_ids: Mapped[list | None] = mapped_column(JSONType)
    candidate_visit_ids: Mapped[list | None] = mapped_column(JSONType)
    origin_classified_at: Mapped[datetime | None] = Ts()
    # Business segment from the matched Pipedrive deal's Category:
    # RESIDENTIAL | PROPERTY_MANAGEMENT | GC_PROJECT | UNCATEGORIZED | NO_DEAL_MATCH
    segment: Mapped[str | None] = mapped_column(String(30), index=True)
    # Hash of title + line items, used to decide when to reclassify.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    web_uri: Mapped[str | None] = mapped_column(Text)


class JobberQuoteLineItem(Base):
    __tablename__ = "jobber_quote_line_items"
    line_item_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    quote_id: Mapped[str] = mapped_column(String(80), ForeignKey("jobber_quotes.quote_id", ondelete="CASCADE"), index=True)
    sort_order: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    total_price: Mapped[Decimal | None] = mapped_column(Money)
    optional: Mapped[bool | None] = mapped_column(Boolean)
    text_only: Mapped[bool | None] = mapped_column(Boolean)


class QuoteStatusHistory(Base):
    __tablename__ = "quote_status_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[str] = mapped_column(String(80), index=True)
    old_status: Mapped[str | None] = mapped_column(String(40))
    new_status: Mapped[str | None] = mapped_column(String(40))
    old_total: Mapped[Decimal | None] = mapped_column(Money)
    new_total: Mapped[Decimal | None] = mapped_column(Money)
    detected_at: Mapped[datetime] = Ts(default=utcnow)


class JobberJob(SyncedMixin, Base):
    __tablename__ = "jobber_jobs"
    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_number: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(40), index=True)
    job_type: Mapped[str | None] = mapped_column(String(20))
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    property_id: Mapped[str | None] = mapped_column(String(80), index=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    origin_quote_id: Mapped[str | None] = mapped_column(String(80), index=True)
    salesperson_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime | None] = Ts(index=True)
    updated_at: Mapped[datetime | None] = Ts()
    start_at: Mapped[datetime | None] = Ts()
    end_at: Mapped[datetime | None] = Ts()
    completed_at: Mapped[datetime | None] = Ts()
    total: Mapped[Decimal | None] = mapped_column(Money)
    invoiced_total: Mapped[Decimal | None] = mapped_column(Money)
    uninvoiced_total: Mapped[Decimal | None] = mapped_column(Money)
    invoice_ids: Mapped[list | None] = mapped_column(JSONType)
    # Derived
    is_direct_job: Mapped[bool] = mapped_column(Boolean, default=False)  # no request, no quote
    is_quote_convert: Mapped[bool] = mapped_column(Boolean, default=False)
    web_uri: Mapped[str | None] = mapped_column(Text)


class JobberVisit(SyncedMixin, Base):
    __tablename__ = "jobber_visits"
    visit_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(80), index=True)
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    property_id: Mapped[str | None] = mapped_column(String(80))
    invoice_id: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(30))
    start_at: Mapped[datetime | None] = Ts(index=True)
    end_at: Mapped[datetime | None] = Ts()
    completed_at: Mapped[datetime | None] = Ts()
    all_day: Mapped[bool | None] = mapped_column(Boolean)
    is_complete: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime | None] = Ts()


class VisitAssignment(Base):
    __tablename__ = "visit_assignments"
    visit_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True, index=True)


class JobberInvoice(SyncedMixin, Base):
    __tablename__ = "jobber_invoices"
    invoice_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(String(40))
    subject: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(30), index=True)
    client_id: Mapped[str | None] = mapped_column(String(80), index=True)
    job_ids: Mapped[list | None] = mapped_column(JSONType)
    visit_ids: Mapped[list | None] = mapped_column(JSONType)
    created_at: Mapped[datetime | None] = Ts()
    updated_at: Mapped[datetime | None] = Ts()
    issued_date: Mapped[datetime | None] = Ts()
    due_date: Mapped[datetime | None] = Ts()
    received_date: Mapped[datetime | None] = Ts()
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax_amount: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)
    payments_total: Mapped[Decimal | None] = mapped_column(Money)
    balance: Mapped[Decimal | None] = mapped_column(Money)
    web_uri: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Derived: relationships, classification
# ---------------------------------------------------------------------------
class Relationship(Base):
    """Genealogy edges. method: DIRECT | INFERRED | REVIEW_REQUIRED."""

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("from_type", "from_id", "to_type", "to_id", "rel_type", name="uq_relationship"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_type: Mapped[str] = mapped_column(String(20))
    from_id: Mapped[str] = mapped_column(String(80), index=True)
    to_type: Mapped[str] = mapped_column(String(20))
    to_id: Mapped[str] = mapped_column(String(80), index=True)
    # e.g. REQUEST_QUOTE, REQUEST_JOB, REQUEST_ASSESSMENT, QUOTE_CONVERTED_JOB,
    # JOB_VISIT, JOB_INVOICE, SERVICE_JOB_REPAIR_QUOTE
    rel_type: Mapped[str] = mapped_column(String(40), index=True)
    method: Mapped[str] = mapped_column(String(20), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    reason: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # A human can confirm or reject an INFERRED/REVIEW_REQUIRED edge later.
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = Ts()
    created_at: Mapped[datetime] = Ts(default=utcnow)
    updated_at: Mapped[datetime] = Ts(default=utcnow, onupdate=utcnow)


class QuoteClassification(Base):
    __tablename__ = "quote_classifications"
    quote_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    department: Mapped[str] = mapped_column(String(30))
    category: Mapped[str] = mapped_column(String(30))
    system: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20))  # RULES | LLM | MANUAL
    model: Mapped[str | None] = mapped_column(String(60))
    source_hash: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str | None] = mapped_column(Text)
    classified_at: Mapped[datetime] = Ts(default=utcnow)


# ---------------------------------------------------------------------------
# Pipedrive
# ---------------------------------------------------------------------------
class PipedriveUser(SyncedMixin, Base):
    __tablename__ = "pipedrive_users"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool | None] = mapped_column(Boolean)


class PipedrivePipeline(SyncedMixin, Base):
    __tablename__ = "pipedrive_pipelines"
    pipeline_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)


class PipedriveStage(SyncedMixin, Base):
    __tablename__ = "pipedrive_stages"
    stage_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pipeline_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    name: Mapped[str | None] = mapped_column(String(255))  # trimmed
    order_nr: Mapped[int | None] = mapped_column(Integer)


class PipedrivePerson(SyncedMixin, Base):
    __tablename__ = "pipedrive_people"
    person_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    org_id: Mapped[int | None] = mapped_column(BigInteger)
    owner_id: Mapped[int | None] = mapped_column(BigInteger)
    emails: Mapped[list | None] = mapped_column(JSONType)  # normalized
    phones: Mapped[list | None] = mapped_column(JSONType)  # normalized
    add_time: Mapped[datetime | None] = Ts()
    update_time: Mapped[datetime | None] = Ts()


class PipedriveDeal(SyncedMixin, Base):
    __tablename__ = "pipedrive_deals"
    deal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    person_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    org_id: Mapped[int | None] = mapped_column(BigInteger)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(255))
    pipeline_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stage_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(20), index=True)  # open | won | lost | deleted
    value: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(10))
    add_time: Mapped[datetime | None] = Ts()
    update_time: Mapped[datetime | None] = Ts()
    stage_change_time: Mapped[datetime | None] = Ts()
    won_time: Mapped[datetime | None] = Ts()
    lost_time: Mapped[datetime | None] = Ts()
    last_activity_date: Mapped[str | None] = mapped_column(String(10))
    next_activity_date: Mapped[str | None] = mapped_column(String(10))
    next_activity_time: Mapped[str | None] = mapped_column(String(8))
    next_activity_id: Mapped[int | None] = mapped_column(BigInteger)
    last_activity_id: Mapped[int | None] = mapped_column(BigInteger)
    # Only filled if a "Jobber Quote ID" custom field exists in Pipedrive.
    jobber_quote_id: Mapped[str | None] = mapped_column(String(80), index=True)
    category: Mapped[str | None] = mapped_column(String(80))  # Pipedrive "Category" label
    segment: Mapped[str | None] = mapped_column(String(60))  # derived from category
    person_emails: Mapped[list | None] = mapped_column(JSONType)  # normalized, from embedded person
    person_phones: Mapped[list | None] = mapped_column(JSONType)


class PipedriveActivity(SyncedMixin, Base):
    __tablename__ = "pipedrive_activities"
    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    deal_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    person_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    type: Mapped[str | None] = mapped_column(String(40))
    subject: Mapped[str | None] = mapped_column(Text)
    done: Mapped[bool | None] = mapped_column(Boolean)
    due_date: Mapped[str | None] = mapped_column(String(10))
    due_time: Mapped[str | None] = mapped_column(String(8))
    marked_as_done_time: Mapped[datetime | None] = Ts()
    add_time: Mapped[datetime | None] = Ts()
    update_time: Mapped[datetime | None] = Ts()


class DealStageHistory(Base):
    __tablename__ = "deal_stage_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(BigInteger, index=True)
    old_stage_id: Mapped[int | None] = mapped_column(BigInteger)
    new_stage_id: Mapped[int | None] = mapped_column(BigInteger)
    old_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str | None] = mapped_column(String(20))
    old_value: Mapped[Decimal | None] = mapped_column(Money)
    new_value: Mapped[Decimal | None] = mapped_column(Money)
    detected_at: Mapped[datetime] = Ts(default=utcnow)


# ---------------------------------------------------------------------------
# Matching, audit, reconciliation, alerts
# ---------------------------------------------------------------------------
class DealMatch(Base):
    """Jobber Quote <-> Pipedrive Deal.
    match_method: JOBBER_QUOTE_ID | CONFIRMED | CONTACT_MATCH | REVIEW_REQUIRED."""

    __tablename__ = "deal_matches"
    __table_args__ = (UniqueConstraint("quote_id", "deal_id", name="uq_deal_match"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[str] = mapped_column(String(80), index=True)
    deal_id: Mapped[int] = mapped_column(BigInteger, index=True)
    match_method: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = Ts()
    created_at: Mapped[datetime] = Ts(default=utcnow)
    updated_at: Mapped[datetime] = Ts(default=utcnow, onupdate=utcnow)


class ProcessAudit(Base):
    __tablename__ = "process_audit"
    __table_args__ = (Index("ix_process_audit_open", "opportunity_key", "gap_type", "resolved_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_key: Mapped[str] = mapped_column(String(100), index=True)  # quote:<id> | request:<id> | job:<id>
    opportunity_type: Mapped[str | None] = mapped_column(String(20))  # QUOTE | REQUEST | SERVICE_JOB
    quote_id: Mapped[str | None] = mapped_column(String(80))
    client_id: Mapped[str | None] = mapped_column(String(80))
    deal_id: Mapped[int | None] = mapped_column(BigInteger)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(255))
    segment: Mapped[str | None] = mapped_column(String(30))
    flow_type: Mapped[str | None] = mapped_column(String(20))
    severity: Mapped[str | None] = mapped_column(String(10))  # ACTION | INFO
    details: Mapped[dict | None] = mapped_column(JSONType)
    expected_stage: Mapped[str | None] = mapped_column(String(30))
    expected_status: Mapped[str | None] = mapped_column(String(20))
    actual_stage: Mapped[str | None] = mapped_column(String(60))
    actual_status: Mapped[str | None] = mapped_column(String(20))
    gap_type: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text)
    last_jobber_event_at: Mapped[datetime | None] = Ts()
    last_pipedrive_update_at: Mapped[datetime | None] = Ts()
    first_detected_at: Mapped[datetime] = Ts(default=utcnow)
    last_seen_at: Mapped[datetime] = Ts(default=utcnow)
    resolved_at: Mapped[datetime | None] = Ts()


class Reconciliation(Base):
    __tablename__ = "reconciliation"
    __table_args__ = (UniqueConstraint("quote_id", "deal_id", name="uq_reconciliation"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[str] = mapped_column(String(80))
    deal_id: Mapped[int | None] = mapped_column(BigInteger)
    quote_value: Mapped[Decimal | None] = mapped_column(Money)
    deal_value: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    value_status: Mapped[str | None] = mapped_column(String(30))  # OK | VALUE_MISMATCH
    status_check: Mapped[str | None] = mapped_column(String(40))  # OK | JOBBER_CONVERTED_PIPE_NOT_WON | ...
    match_method: Mapped[str | None] = mapped_column(String(30))
    quote_status: Mapped[str | None] = mapped_column(String(40))
    deal_status: Mapped[str | None] = mapped_column(String(20))
    deal_stage: Mapped[str | None] = mapped_column(String(60))
    quotes_on_deal: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime] = Ts(default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alert_open", "alert_type", "entity_type", "entity_id", "resolved_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(10), default="INFO")  # INFO | WARN | HIGH
    message: Mapped[str] = mapped_column(Text)
    owner_label: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict | None] = mapped_column(JSONType)
    first_detected_at: Mapped[datetime] = Ts(default=utcnow)
    last_seen_at: Mapped[datetime] = Ts(default=utcnow)
    resolved_at: Mapped[datetime | None] = Ts()
