"""Tests against real PostgreSQL (embedded) after Alembic migration."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from database.models import (
    JobberJob, JobberPerson, JobberQuote, JobberQuoteLineItem, JobberVisit, OAuthToken,
    QuoteStatusHistory, Relationship, SyncRun, SyncState, VisitAssignment,
)
from jobber import oauth
from services import relationships
from services.auth import AuthError, authenticate, create_user, ensure_bootstrap_admin
from services.freshness import source_freshness
from services.sync_jobber import JobberSync
from database.models import JobberAssessment, JobberClient, JobberInvoice, JobberRequest, RequestAssignment
from tests.fixtures import CLIENT, JOB_2718, JOB_2729, PROPERTY, QUOTE_105733, WESLEY, prop, quote_node, regression_nodes


# --- auth -------------------------------------------------------------------
def test_create_and_authenticate(session):
    create_user(session, "Owner@JNService.com ", "Owner", "correct horse battery", "MANAGEMENT")
    u = authenticate(session, "owner@jnservice.com", "correct horse battery")
    assert u.role == "MANAGEMENT" and u.last_login_at is not None
    with pytest.raises(AuthError):
        authenticate(session, "owner@jnservice.com", "wrong password!!")


def test_lockout_after_5_failures(session):
    create_user(session, "a@b.com", "A", "longpassword123")
    now = datetime.now(timezone.utc)
    for _ in range(5):
        with pytest.raises(AuthError):
            authenticate(session, "a@b.com", "nope-nope-nope", now=now)
    with pytest.raises(AuthError, match="Too many"):
        authenticate(session, "a@b.com", "longpassword123", now=now)
    assert authenticate(session, "a@b.com", "longpassword123", now=now + timedelta(minutes=16))


def test_short_password_and_bad_role_rejected(session):
    with pytest.raises(AuthError):
        create_user(session, "x@y.com", "X", "short")
    with pytest.raises(AuthError):
        create_user(session, "x@y.com", "X", "longpassword123", "SUPERUSER")


def test_bootstrap_admin_only_when_empty(session):
    assert ensure_bootstrap_admin(session, "admin@x.com", "longpassword123").role == "ADMIN"
    assert ensure_bootstrap_admin(session, "other@x.com", "longpassword123") is None


# --- oauth --------------------------------------------------------------------
def test_state_signing():
    st = oauth.make_state("admin@x.com")
    assert oauth.verify_state(st) == "admin@x.com"
    assert oauth.verify_state(st + "x") is None
    assert oauth.verify_state(st, now=datetime.now().timestamp() + 3600) is None


def _jwt(exp: datetime) -> str:
    import base64, json
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg({'exp': int(exp.timestamp())})}.sig"


def test_refresh_rotates_and_encrypts_tokens(session):
    calls = []
    exp = datetime.now(timezone.utc) + timedelta(hours=1)

    def handler(req):
        calls.append(req.content.decode())
        n = len(calls)
        return httpx.Response(200, json={"access_token": _jwt(exp), "refresh_token": f"refresh-{n}"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    oauth.exchange_code(session, "the-code", "admin@x.com", http=http)
    row = session.get(OAuthToken, "jobber")
    assert "refresh-1" not in (row.refresh_token_enc or "")  # stored encrypted
    assert abs((row.access_expires_at - exp).total_seconds()) < 2
    # valid token -> no network call
    oauth.get_access_token(session, http=http)
    assert len(calls) == 1
    # forced refresh -> sends latest refresh token, stores rotated one
    oauth.get_access_token(session, force_refresh=True, http=http)
    assert "refresh_token=refresh-1" in calls[1] and "grant_type=refresh_token" in calls[1]
    oauth.get_access_token(session, force_refresh=True, http=http)
    assert "refresh_token=refresh-2" in calls[2]


# --- sync -------------------------------------------------------------------
class FakeJobber:
    def __init__(self, data, fail=()):
        self.data, self.fail, self.calls = data, set(fail), []

    def paginate(self, document, root, variables=None, page_size=None, stop_when=None, max_pages=None):
        self.calls.append((root, variables))
        if root in self.fail:
            raise RuntimeError(f"boom {root}")
        for n in self.data.get(root, []):
            if stop_when and stop_when(n):
                return
            yield n


def _data():
    jobs, quotes, visits = regression_nodes()
    users = [{"id": WESLEY, "name": {"full": "Wesley Santos", "first": "Wesley", "last": "Santos"}, "email": {"raw": "W@x.com"},
              "status": "ACTIVATED", "isAccountOwner": False, "isAccountAdmin": False}]
    clients = [{"id": CLIENT, "name": "Marcus Pierre PRO", "firstName": "Marcus", "lastName": "Pierre", "companyName": None,
                "isCompany": False, "isLead": False, "jobberWebUri": "https://secure.getjobber.com/clients/1",
                "createdAt": "2026-01-05T12:00:00Z", "updatedAt": "2026-04-10T12:00:00Z",
                "emails": [{"address": " Marcus@Example.com ", "primary": True}],
                "phones": [{"number": "(617) 555-0100", "normalizedPhoneNumber": "+16175550100", "primary": True}]}]
    requests = [{"id": "req-1", "title": "AC estimate", "requestStatus": "converted", "source": "ONLINE_BOOKING",
                 "createdAt": "2026-04-01T12:00:00Z", "updatedAt": "2026-04-02T12:00:00Z", "jobberWebUri": "https://x/r/1",
                 # different property than the 2718 regression chain (same property would make it ambiguous, by design)
                 "client": {"id": CLIENT}, "property": prop("prop-rental-unit"), "salesperson": {"id": WESLEY},
                 "assessment": {"id": "asm-1", "title": "Estimate", "startAt": "2026-04-03T14:00:00Z", "endAt": "2026-04-03T15:00:00Z",
                                "completedAt": "2026-04-03T15:00:00Z", "allDay": False, "isComplete": True,
                                "request": {"id": "req-1"}, "client": {"id": CLIENT}, "property": {"id": PROPERTY},
                                "assignedUsers": {"nodes": [{"id": WESLEY}]}},
                 "quotes": {"nodes": []}, "jobs": {"nodes": []}}]
    invoices = [{"id": "inv-1", "invoiceNumber": "501", "subject": "Invoice", "invoiceStatus": "paid",
                 "createdAt": "2026-04-16T12:00:00Z", "updatedAt": "2026-04-16T12:00:00Z", "issuedDate": "2026-04-16T12:00:00Z",
                 "dueDate": None, "receivedDate": None, "jobberWebUri": "https://x/i/1",
                 "amounts": {"subtotal": 2450, "taxAmount": 0, "total": 2450, "paymentsTotal": 2450, "invoiceBalance": 0},
                 "client": {"id": CLIENT}, "jobs": {"nodes": [{"id": JOB_2729}]}, "visits": {"nodes": []}}]
    return {"users": users, "clients": clients, "requests": requests, "jobs": jobs, "quotes": quotes, "visits": visits,
            "invoices": invoices, "scheduledItems": []}


def test_full_sync_then_relationships(session):
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    fake = FakeJobber(_data())
    result = JobberSync(session, fake, now=now).run()
    assert set(result.values()) == {"SUCCESS"}
    assert session.get(JobberPerson, WESLEY).name == "Wesley Santos"
    assert session.get(JobberJob, JOB_2718).is_direct_job is True
    assert session.get(JobberJob, JOB_2729).is_quote_convert is True
    q = session.get(JobberQuote, QUOTE_105733)
    assert q.quote_number == "105733" and float(q.total) == 2450.0 and q.is_converted and q.is_sent
    assert session.scalars(select(JobberQuoteLineItem)).all()[0].name == "Inducer motor"
    assert session.scalars(select(VisitAssignment.user_id).where(VisitAssignment.visit_id == "visit-2718-1")).all() == [WESLEY]
    assert session.get(SyncState, ("JOBBER", "quotes")).watermark is not None
    c = session.get(JobberClient, CLIENT)
    assert c.emails == ["marcus@example.com"] and c.phones == ["6175550100"]
    assert session.get(JobberRequest, "req-1").assessment_id == "asm-1"
    assert session.get(JobberAssessment, "asm-1").status == "COMPLETED"
    assert session.scalars(select(RequestAssignment.user_id).where(RequestAssignment.assessment_id == "asm-1")).all() == [WESLEY]
    assert session.get(JobberInvoice, "inv-1").job_ids == [JOB_2729]

    counts = relationships.rebuild(session)
    session.commit()
    assert counts["INFERRED"] == 1
    inferred = session.scalar(select(Relationship).where(Relationship.from_id == JOB_2718, Relationship.to_id == QUOTE_105733))
    assert inferred.method == "INFERRED" and inferred.is_active
    direct = session.scalar(select(Relationship).where(Relationship.from_id == QUOTE_105733, Relationship.to_id == JOB_2729))
    assert direct.method == "DIRECT"
    assert session.get(JobberQuote, QUOTE_105733).flow_type == "SERVICE"


def test_quote_origin_switch_off_then_on(session, monkeypatch):
    from config import get_settings
    from services import quote_origin

    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    JobberSync(session, FakeJobber(_data()), now=now).run()
    relationships.rebuild(session)
    legacy_flow = session.get(JobberQuote, QUOTE_105733).flow_type

    # OFF (rollback mode): origin fields filled, dashboards' flow_type untouched
    monkeypatch.setattr(get_settings(), "use_quote_origin_engine", False)
    relationships.rebuild(session)
    legacy_flow = session.get(JobberQuote, QUOTE_105733).flow_type
    counts = quote_origin.rebuild(session)
    q = session.get(JobberQuote, QUOTE_105733)
    assert (q.origin_flow_type, q.origin_method, q.origin_service_job_id) == ("SERVICE", "INFERRED", JOB_2718)
    assert q.origin_visit_id == "visit-2718-1" and q.flow_type == legacy_flow
    assert counts["SERVICE/INFERRED"] == 1

    # ON: relationships + flow_type come from the origin engine
    monkeypatch.setattr(get_settings(), "use_quote_origin_engine", True)
    relationships.rebuild(session)
    quote_origin.rebuild(session)
    session.commit()
    edge = session.scalar(select(Relationship).where(Relationship.from_id == JOB_2718, Relationship.to_id == QUOTE_105733,
                                                     Relationship.is_active.is_(True)))
    assert edge.method == "INFERRED" and edge.signals["origin_visit_id"] == "visit-2718-1"
    assert session.scalar(select(Relationship).where(Relationship.from_id == QUOTE_105733, Relationship.to_id == JOB_2729)).method == "DIRECT"
    assert session.get(JobberQuote, QUOTE_105733).flow_type == "SERVICE"


def test_audit_and_reconciliation_end_to_end(session):
    from database.models import PipedriveDeal, PipedriveStage, ProcessAudit, Reconciliation
    from services import deal_matching, process_audit, reconciliation

    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    data = _data()
    data["quotes"] = [quote_node(QUOTE_105733, 105733, status="awaiting_response", jobs=[])]  # sent, not converted
    JobberSync(session, FakeJobber(data), now=now).run()
    relationships.rebuild(session)
    session.add(PipedriveStage(stage_id=4, pipeline_id=1, name="Quote"))
    session.add(PipedriveDeal(deal_id=77, title="Marcus Pierre - furnace", status="open", stage_id=4, pipeline_id=1, owner_id=7,
                              owner_name="Cauet Passos", value=2000, add_time=datetime(2026, 4, 9, tzinfo=timezone.utc),
                              update_time=datetime(2026, 4, 11, tzinfo=timezone.utc), category="Small Jobs", segment="RESIDENTIAL",
                              person_emails=["marcus@example.com"], person_phones=[]))
    session.commit()
    assert deal_matching.rebuild(session)["CONTACT_MATCH"] == 1
    counts = process_audit.rebuild(session, now=now)
    gap = session.scalar(select(ProcessAudit).where(ProcessAudit.opportunity_key == f"quote:{QUOTE_105733}"))
    assert gap.gap_type == "PIPE_STAGE_NEGOTIATE_REQUIRED" and gap.owner_name == "Cauet Passos" and gap.severity == "ACTION"
    assert gap.details["technicians"] == ["Wesley Santos"] and gap.segment == "RESIDENTIAL"
    assert counts["_opportunities"] >= 2  # quote + request with assessment
    rec = session.scalar(select(Reconciliation).where(Reconciliation.quote_id == QUOTE_105733)) if reconciliation.rebuild(session) else None
    assert rec.value_status == "VALUE_MISMATCH" and rec.status_check == "OK"

    # Deal moved to Negotiate in Pipedrive -> the gap is resolved on the next run
    session.get(PipedriveStage, 4).name = "Negotiate"
    process_audit.rebuild(session, now=now)
    assert session.scalar(select(ProcessAudit).where(ProcessAudit.opportunity_key == f"quote:{QUOTE_105733}",
                                                     ProcessAudit.gap_type == "PIPE_STAGE_NEGOTIATE_REQUIRED")).resolved_at is not None


def test_resync_is_idempotent_and_records_status_change(session):
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    data = _data()
    data["quotes"] = [quote_node(QUOTE_105733, 105733, status="awaiting_response", jobs=[])]
    JobberSync(session, FakeJobber(data), now=now).run(["quotes"])
    data["quotes"] = [quote_node(QUOTE_105733, 105733, status="converted", jobs=[JOB_2729], updated="2026-04-15T12:00:00Z")]
    JobberSync(session, FakeJobber(data), now=now).run(["quotes"])
    JobberSync(session, FakeJobber(data), now=now).run(["quotes"])  # unchanged
    hist = session.scalars(select(QuoteStatusHistory).order_by(QuoteStatusHistory.id)).all()
    assert [(h.old_status, h.new_status) for h in hist] == [(None, "awaiting_response"), ("awaiting_response", "converted")]
    runs = session.scalars(select(SyncRun).order_by(SyncRun.id)).all()
    assert [(r.records_created, r.records_updated) for r in runs] == [(1, 0), (0, 1), (0, 0)]


def test_incremental_uses_watermark_with_overlap(session):
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    JobberSync(session, FakeJobber(_data()), now=now).run(["quotes"])
    fake = FakeJobber(_data())
    JobberSync(session, fake, now=now).run(["quotes"])
    after = fake.calls[0][1]["filter"]["updatedAt"]["after"]
    assert after == "2026-04-10T17:20:00Z"  # watermark (17:30) minus 10 min overlap


def test_failed_entity_is_recorded_and_flagged(session):
    now = datetime.now(timezone.utc)
    result = JobberSync(session, FakeJobber(_data(), fail={"visits"}), now=now).run(["users", "visits"])
    assert result == {"users": "SUCCESS", "visits": "FAILED"}
    failed = session.scalar(select(SyncRun).where(SyncRun.entity == "visits"))
    assert "boom visits" in failed.errors
    assert session.get(SyncState, ("JOBBER", "visits")) is None  # watermark not advanced
    f = source_freshness(session, "JOBBER")
    assert f.failing_entities == ["visits"] and "FAILED" in f.warning()
    assert session.scalars(select(JobberVisit)).all() == []
