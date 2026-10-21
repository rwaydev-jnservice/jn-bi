"""Jobber GraphQL node -> database row dicts. Pure functions (no DB)."""

from __future__ import annotations

import hashlib
import json

from services.normalize import money, norm_email, norm_phone, parse_ts, uniq

QUOTE_CONVERT_SOURCES = {"QUOTE_CONVERT", "QUOTE_INDEX_CONVERT"}


def _id(obj) -> str | None:
    return obj.get("id") if isinstance(obj, dict) else None


def _ids(conn) -> list[str]:
    return [n["id"] for n in ((conn or {}).get("nodes") or []) if n and n.get("id")]


def person(node: dict) -> dict:
    name = node.get("name") or {}
    status = node.get("status")
    clean = lambda v: " ".join(v.split()) if v else v  # noqa: E731 - Jobber names can carry stray spaces
    return {
        "user_id": node["id"],
        "name": clean(name.get("full")),
        "first_name": clean(name.get("first")),
        "last_name": clean(name.get("last")),
        "email": norm_email((node.get("email") or {}).get("raw")),
        "status": status,
        "is_active": status == "ACTIVATED",
        "is_account_owner": bool(node.get("isAccountOwner")),
        "is_account_admin": bool(node.get("isAccountAdmin")),
        "raw": node,
    }


def client(node: dict) -> dict:
    return {
        "client_id": node["id"],
        "name": node.get("name"),
        "first_name": node.get("firstName"),
        "last_name": node.get("lastName"),
        "company_name": node.get("companyName"),
        "is_company": node.get("isCompany"),
        "is_lead": node.get("isLead"),
        "emails": uniq(norm_email(e.get("address")) for e in node.get("emails") or []),
        "phones": uniq(norm_phone(p.get("normalizedPhoneNumber") or p.get("number")) for p in node.get("phones") or []),
        "web_uri": node.get("jobberWebUri"),
        "created_at": parse_ts(node.get("createdAt")),
        "updated_at": parse_ts(node.get("updatedAt")),
        "raw": node,
    }


def property_(node: dict | None) -> dict | None:
    if not node or not node.get("id"):
        return None
    addr = node.get("address") or {}
    return {
        "property_id": node["id"],
        "client_id": _id(node.get("client")),
        "name": node.get("name"),
        "street1": addr.get("street1"),
        "street2": addr.get("street2"),
        "city": addr.get("city"),
        "province": addr.get("province"),
        "postal_code": addr.get("postalCode"),
        "country": addr.get("country"),
        "web_uri": node.get("jobberWebUri"),
        "raw": node,
    }


def assessment(node: dict | None, request_id: str | None = None) -> tuple[dict, list[str]] | None:
    if not node or not node.get("id"):
        return None
    completed = parse_ts(node.get("completedAt"))
    start = parse_ts(node.get("startAt"))
    status = "COMPLETED" if (node.get("isComplete") or completed) else ("SCHEDULED" if start else "UNSCHEDULED")
    row = {
        "assessment_id": node["id"],
        "request_id": _id(node.get("request")) or request_id,
        "client_id": _id(node.get("client")),
        "property_id": _id(node.get("property")),
        "title": node.get("title"),
        "start_at": start,
        "end_at": parse_ts(node.get("endAt")),
        "completed_at": completed,
        "all_day": node.get("allDay"),
        "is_complete": node.get("isComplete"),
        "status": status,
        "raw": node,
    }
    return row, _ids(node.get("assignedUsers"))


def request(node: dict) -> dict:
    quote_ids, job_ids = _ids(node.get("quotes")), _ids(node.get("jobs"))
    return {
        "request_id": node["id"],
        "title": node.get("title"),
        "status": node.get("requestStatus"),
        "source": node.get("source"),
        "client_id": _id(node.get("client")),
        "property_id": _id(node.get("property")),
        "salesperson_id": _id(node.get("salesperson")),
        "assessment_id": _id(node.get("assessment")),
        "quote_ids": quote_ids,
        "job_ids": job_ids,
        "has_quote": bool(quote_ids),
        "has_job": bool(job_ids),
        "web_uri": node.get("jobberWebUri"),
        "created_at": parse_ts(node.get("createdAt")),
        "updated_at": parse_ts(node.get("updatedAt")),
        "raw": node,
    }


def quote_content_hash(title: str | None, line_items: list[dict]) -> str:
    parts = [title or ""] + [f"{li.get('name') or ''}|{li.get('description') or ''}" for li in line_items]
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def quote(node: dict) -> tuple[dict, list[dict]]:
    amounts = node.get("amounts") or {}
    last = node.get("lastTransitioned") or {}
    status = node.get("quoteStatus")
    sent_at = parse_ts(node.get("sentAt"))
    items = [li for li in ((node.get("lineItems") or {}).get("nodes") or []) if li]
    line_rows = [
        {
            "line_item_id": li["id"],
            "quote_id": node["id"],
            "sort_order": li.get("sortOrder"),
            "name": li.get("name"),
            "description": li.get("description"),
            "quantity": li.get("quantity"),
            "unit_price": money(li.get("unitPrice")),
            "total_price": money(li.get("totalPrice")),
            "optional": li.get("optional"),
            "text_only": li.get("textOnly"),
        }
        for li in items
    ]
    row = {
        "quote_id": node["id"],
        "quote_number": str(node.get("quoteNumber")) if node.get("quoteNumber") is not None else None,
        "title": node.get("title"),
        "status": status,
        "client_id": _id(node.get("client")),
        "property_id": _id(node.get("property")),
        "request_id": _id(node.get("request")),
        "salesperson_id": _id(node.get("salesperson")),
        "created_at": parse_ts(node.get("createdAt")),
        "updated_at": parse_ts(node.get("updatedAt")),
        "sent_at": sent_at,
        "transitioned_at": parse_ts(node.get("transitionedAt")),
        "approved_at": parse_ts(last.get("approvedAt")),
        "converted_at": parse_ts(last.get("convertedAt")),
        "subtotal": money(amounts.get("subtotal")),
        "tax_amount": money(amounts.get("taxAmount")),
        "discount_amount": money(amounts.get("discountAmount")),
        "deposit_amount": money(amounts.get("depositAmount")),
        "total": money(amounts.get("total")),
        "converted_job_ids": _ids(node.get("jobs")),
        "is_sent": sent_at is not None,
        "is_converted": status == "converted",
        "is_approved": status in ("approved", "converted") or bool(last.get("approvedAt")),
        "is_draft": status == "draft",
        "content_hash": quote_content_hash(node.get("title"), items),
        "web_uri": node.get("jobberWebUri"),
        "raw": node,
    }
    return row, line_rows


def job(node: dict) -> dict:
    request_id = _id(node.get("request"))
    origin_quote_id = _id(node.get("quote"))
    source = node.get("source")
    return {
        "job_id": node["id"],
        "job_number": node.get("jobNumber"),
        "title": node.get("title"),
        "source": source,
        "status": node.get("jobStatus"),
        "job_type": node.get("jobType"),
        "client_id": _id(node.get("client")),
        "property_id": _id(node.get("property")),
        "request_id": request_id,
        "origin_quote_id": origin_quote_id,
        "salesperson_id": _id(node.get("salesperson")),
        "created_at": parse_ts(node.get("createdAt")),
        "updated_at": parse_ts(node.get("updatedAt")),
        "start_at": parse_ts(node.get("startAt")),
        "end_at": parse_ts(node.get("endAt")),
        "completed_at": parse_ts(node.get("completedAt")),
        "total": money(node.get("total")),
        "invoiced_total": money(node.get("invoicedTotal")),
        "uninvoiced_total": money(node.get("uninvoicedTotal")),
        "invoice_ids": _ids(node.get("invoices")),
        "is_direct_job": request_id is None and origin_quote_id is None,
        "is_quote_convert": source in QUOTE_CONVERT_SOURCES or origin_quote_id is not None,
        "web_uri": node.get("jobberWebUri"),
        "raw": node,
    }


def visit(node: dict) -> tuple[dict, list[str]]:
    row = {
        "visit_id": node["id"],
        "job_id": _id(node.get("job")),
        "client_id": _id(node.get("client")),
        "property_id": _id(node.get("property")),
        "invoice_id": _id(node.get("invoice")),
        "title": node.get("title"),
        "status": node.get("visitStatus"),
        "start_at": parse_ts(node.get("startAt")),
        "end_at": parse_ts(node.get("endAt")),
        "completed_at": parse_ts(node.get("completedAt")),
        "all_day": node.get("allDay"),
        "is_complete": node.get("isComplete"),
        "created_at": parse_ts(node.get("createdAt")),
        "raw": node,
    }
    return row, _ids(node.get("assignedUsers"))


def invoice(node: dict) -> dict:
    amounts = node.get("amounts") or {}
    return {
        "invoice_id": node["id"],
        "invoice_number": str(node.get("invoiceNumber")) if node.get("invoiceNumber") is not None else None,
        "subject": node.get("subject"),
        "status": node.get("invoiceStatus"),
        "client_id": _id(node.get("client")),
        "job_ids": _ids(node.get("jobs")),
        "visit_ids": _ids(node.get("visits")),
        "created_at": parse_ts(node.get("createdAt")),
        "updated_at": parse_ts(node.get("updatedAt")),
        "issued_date": parse_ts(node.get("issuedDate")),
        "due_date": parse_ts(node.get("dueDate")),
        "received_date": parse_ts(node.get("receivedDate")),
        "subtotal": money(amounts.get("subtotal")),
        "tax_amount": money(amounts.get("taxAmount")),
        "total": money(amounts.get("total")),
        "payments_total": money(amounts.get("paymentsTotal")),
        "balance": money(amounts.get("invoiceBalance")),
        "web_uri": node.get("jobberWebUri"),
        "raw": node,
    }
