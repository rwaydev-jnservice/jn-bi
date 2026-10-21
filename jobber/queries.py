"""All Jobber GraphQL documents used by the app. READ-ONLY queries only.

Every field here was checked against the introspected 2026-05-12 schema
(jobber/schema_snapshot/). tests/test_jobber_queries_schema.py validates all
documents against that snapshot, so a wrong field name fails the test suite
instead of failing in production.
"""

PAGE_INFO = "pageInfo { hasNextPage endCursor }"

PROPERTY_FIELDS = """
fragment PropertyFields on Property {
  id
  name
  jobberWebUri
  client { id }
  address { street1 street2 city province postalCode country }
}
"""

ASSESSMENT_FIELDS = """
fragment AssessmentFields on Assessment {
  id
  title
  startAt
  endAt
  completedAt
  allDay
  isComplete
  request { id }
  client { id }
  property { id }
  assignedUsers(first: 20) { nodes { id } }
}
"""

QUOTE_FIELDS = """
fragment QuoteFields on Quote {
  id
  quoteNumber
  title
  quoteStatus
  createdAt
  updatedAt
  sentAt
  transitionedAt
  jobberWebUri
  lastTransitioned { approvedAt convertedAt changesRequestedAt }
  amounts { subtotal taxAmount discountAmount depositAmount total }
  client { id }
  property { ...PropertyFields }
  request { id }
  salesperson { id }
  jobs(first: 10) { nodes { id } }
  lineItems(first: 50) {
    nodes { id name description quantity unitPrice totalPrice optional textOnly sortOrder }
  }
}
"""

JOB_FIELDS = """
fragment JobFields on Job {
  id
  jobNumber
  title
  source
  jobStatus
  jobType
  createdAt
  updatedAt
  startAt
  endAt
  completedAt
  total
  invoicedTotal
  uninvoicedTotal
  jobberWebUri
  client { id }
  property { ...PropertyFields }
  request { id }
  quote { id }
  salesperson { id }
  invoices(first: 10) { nodes { id } }
}
"""

VISIT_FIELDS = """
fragment VisitFields on Visit {
  id
  title
  visitStatus
  startAt
  endAt
  completedAt
  allDay
  isComplete
  createdAt
  job { id }
  client { id }
  property { id }
  invoice { id }
  assignedUsers(first: 20) { nodes { id } }
}
"""

USERS = f"""
query Users($first: Int!, $after: String) {{
  users(first: $first, after: $after) {{
    nodes {{
      id
      name {{ full first last }}
      email {{ raw }}
      status
      isAccountOwner
      isAccountAdmin
    }}
    {PAGE_INFO}
  }}
}}
"""

CLIENTS = f"""
query Clients($first: Int!, $after: String, $filter: ClientFilterAttributes) {{
  clients(first: $first, after: $after, filter: $filter) {{
    nodes {{
      id
      name
      firstName
      lastName
      companyName
      isCompany
      isLead
      jobberWebUri
      createdAt
      updatedAt
      emails {{ address primary }}
      phones {{ number normalizedPhoneNumber primary }}
    }}
    {PAGE_INFO}
  }}
}}
"""

REQUESTS = f"""
query Requests($first: Int!, $after: String, $filter: RequestFilterAttributes) {{
  requests(first: $first, after: $after, filter: $filter) {{
    nodes {{
      id
      title
      requestStatus
      source
      createdAt
      updatedAt
      jobberWebUri
      client {{ id }}
      property {{ ...PropertyFields }}
      salesperson {{ id }}
      assessment {{ ...AssessmentFields }}
      quotes(first: 20) {{ nodes {{ id }} }}
      jobs(first: 20) {{ nodes {{ id }} }}
    }}
    {PAGE_INFO}
  }}
}}
{PROPERTY_FIELDS}
{ASSESSMENT_FIELDS}
"""

QUOTES = f"""
query Quotes($first: Int!, $after: String, $filter: QuoteFilterAttributes) {{
  quotes(first: $first, after: $after, filter: $filter) {{
    nodes {{ ...QuoteFields }}
    {PAGE_INFO}
  }}
}}
{QUOTE_FIELDS}
{PROPERTY_FIELDS}
"""

# Jobs have no updatedAt filter in 2026-05-12, but can be sorted by UPDATED_AT.
JOBS = f"""
query Jobs($first: Int!, $after: String, $filter: JobFilterAttributes) {{
  jobs(first: $first, after: $after, filter: $filter, sort: [{{key: UPDATED_AT, direction: DESCENDING}}]) {{
    nodes {{ ...JobFields }}
    {PAGE_INFO}
  }}
}}
{JOB_FIELDS}
{PROPERTY_FIELDS}
"""

VISITS = f"""
query Visits($first: Int!, $after: String, $filter: VisitFilterAttributes) {{
  visits(first: $first, after: $after, filter: $filter, sort: [{{key: START_AT, direction: ASCENDING}}]) {{
    nodes {{ ...VisitFields }}
    {PAGE_INFO}
  }}
}}
{VISIT_FIELDS}
"""

# There is no top-level `assessments` query; scheduledItems is the way to list
# them by date range.
ASSESSMENTS = f"""
query Assessments($first: Int!, $after: String, $filter: ScheduledItemsFilterAttributes!) {{
  scheduledItems(first: $first, after: $after, filter: $filter) {{
    nodes {{
      __typename
      ... on Assessment {{ ...AssessmentFields }}
    }}
    {PAGE_INFO}
  }}
}}
{ASSESSMENT_FIELDS}
"""

INVOICES = f"""
query Invoices($first: Int!, $after: String, $filter: InvoiceFilterAttributes) {{
  invoices(first: $first, after: $after, filter: $filter) {{
    nodes {{
      id
      invoiceNumber
      subject
      invoiceStatus
      createdAt
      updatedAt
      issuedDate
      dueDate
      receivedDate
      jobberWebUri
      amounts {{ subtotal taxAmount total paymentsTotal invoiceBalance }}
      client {{ id }}
      jobs(first: 10) {{ nodes {{ id }} }}
      visits(first: 20) {{ nodes {{ id }} }}
    }}
    {PAGE_INFO}
  }}
}}
"""

# --- Single-object lookups (validation / drilldown) ---------------------------
JOB_BY_ID = f"""
query JobById($id: EncodedId!) {{
  job(id: $id) {{
    ...JobFields
    visits(first: 50) {{ nodes {{ ...VisitFields }} }}
  }}
}}
{JOB_FIELDS}
{VISIT_FIELDS}
{PROPERTY_FIELDS}
"""

QUOTE_BY_ID = f"""
query QuoteById($id: EncodedId!) {{
  quote(id: $id) {{ ...QuoteFields }}
}}
{QUOTE_FIELDS}
{PROPERTY_FIELDS}
"""

ACCOUNT = """
query Account { account { id name } }
"""

ALL_DOCUMENTS = {
    "USERS": USERS,
    "CLIENTS": CLIENTS,
    "REQUESTS": REQUESTS,
    "QUOTES": QUOTES,
    "JOBS": JOBS,
    "VISITS": VISITS,
    "ASSESSMENTS": ASSESSMENTS,
    "INVOICES": INVOICES,
    "JOB_BY_ID": JOB_BY_ID,
    "QUOTE_BY_ID": QUOTE_BY_ID,
    "ACCOUNT": ACCOUNT,
}
