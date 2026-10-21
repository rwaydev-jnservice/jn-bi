"""Jobber-shaped fixture nodes (same shape our queries return) for the
confirmed real example: Job #2718 -> Quote #105733 -> Job #2729.
IDs are the real EncodedIds; timestamps are illustrative."""

JOB_2718 = "Z2lkOi8vSm9iYmVyL0pvYi8xNTg2OTIyMzA="
QUOTE_105733 = "Z2lkOi8vSm9iYmVyL1F1b3RlLzY2NTk1MDY3"
JOB_2729 = "Z2lkOi8vSm9iYmVyL0pvYi8xNTg4MDkwMDE="
CLIENT = "Z2lkOi8vSm9iYmVyL0NsaWVudC8xNTQ5MjA3NDM="
PROPERTY = "Z2lkOi8vSm9iYmVyL1Byb3BlcnR5LzE2MDc2MDYzMg=="
WESLEY = "user-wesley"
CAUET = "user-cauet"


def prop(pid=PROPERTY, cid=CLIENT):
    return {"id": pid, "name": None, "jobberWebUri": "https://secure.getjobber.com/properties/1",
            "client": {"id": cid}, "address": {"street1": "1 Main St", "street2": "", "city": "Boston", "province": "MA", "postalCode": "02101", "country": "US"}}


def job_node(jid, number, source, quote_id=None, request_id=None, created="2026-04-10T13:00:00Z", completed=None,
             updated=None, client=CLIENT, prop_id=PROPERTY, total=189.0):
    return {
        "id": jid, "jobNumber": number, "title": "Troubleshooting" if source != "QUOTE_CONVERT" else "Furnace repair",
        "source": source, "jobStatus": "requires_invoicing", "jobType": "ONE_OFF",
        "createdAt": created, "updatedAt": updated or created, "startAt": created, "endAt": None, "completedAt": completed,
        "total": total, "invoicedTotal": 0.0, "uninvoicedTotal": total,
        "jobberWebUri": f"https://secure.getjobber.com/work_orders/{number}",
        "client": {"id": client}, "property": prop(prop_id, client),
        "request": {"id": request_id} if request_id else None,
        "quote": {"id": quote_id} if quote_id else None,
        "salesperson": None, "invoices": {"nodes": []},
    }


def quote_node(qid, number, status="converted", request_id=None, created="2026-04-10T17:30:00Z", sent="2026-04-10T18:00:00Z",
               jobs=(), client=CLIENT, prop_id=PROPERTY, total=2450.0, salesperson=CAUET, updated=None):
    return {
        "id": qid, "quoteNumber": str(number), "title": "Furnace repair - replace inducer motor", "quoteStatus": status,
        "createdAt": created, "updatedAt": updated or created, "sentAt": sent, "transitionedAt": created,
        "jobberWebUri": f"https://secure.getjobber.com/quotes/{number}",
        "lastTransitioned": {"approvedAt": "2026-04-14T15:00:00Z" if status in ("approved", "converted") else None,
                             "convertedAt": "2026-04-15T12:00:00Z" if status == "converted" else None, "changesRequestedAt": None},
        "amounts": {"subtotal": total, "taxAmount": 0.0, "discountAmount": 0.0, "depositAmount": 0.0, "total": total},
        "client": {"id": client}, "property": prop(prop_id, client),
        "request": {"id": request_id} if request_id else None,
        "salesperson": {"id": salesperson} if salesperson else None,
        "jobs": {"nodes": [{"id": j} for j in jobs]},
        "lineItems": {"nodes": [{"id": f"{qid}-li1", "name": "Inducer motor", "description": "Replace failed inducer motor on furnace",
                                 "quantity": 1.0, "unitPrice": total, "totalPrice": total, "optional": False, "textOnly": False, "sortOrder": 1}]},
    }


def visit_node(vid, job_id, start="2026-04-10T14:00:00Z", completed="2026-04-10T16:00:00Z", users=(WESLEY,), client=CLIENT, prop_id=PROPERTY):
    return {
        "id": vid, "title": "Visit", "visitStatus": "COMPLETED" if completed else "UPCOMING",
        "startAt": start, "endAt": start, "completedAt": completed, "allDay": False, "isComplete": bool(completed),
        "createdAt": start, "job": {"id": job_id}, "client": {"id": client}, "property": {"id": prop_id},
        "invoice": None, "assignedUsers": {"nodes": [{"id": u} for u in users]},
    }


def regression_nodes():
    jobs = [
        job_node(JOB_2718, 2718, "REACT_SCHEDULE_DAY_INLINE", completed="2026-04-10T16:05:00Z"),
        job_node(JOB_2729, 2729, "QUOTE_CONVERT", quote_id=QUOTE_105733, created="2026-04-15T12:00:00Z", total=2450.0),
    ]
    quotes = [quote_node(QUOTE_105733, 105733, jobs=[JOB_2729])]
    visits = [visit_node("visit-2718-1", JOB_2718), visit_node("visit-2729-1", JOB_2729, start="2026-04-20T13:00:00Z", completed=None)]
    return jobs, quotes, visits
