"""Business segments, driven by the Pipedrive deal "Category" field.

Same mapping the company's existing Pipedrive reports use. Never inferred
from value, title or owner.
"""

RESIDENTIAL = "RESIDENTIAL"
PROPERTY_MANAGEMENT = "PROPERTY_MANAGEMENT"
GC_PROJECT = "GC_PROJECT"
UNCATEGORIZED = "UNCATEGORIZED"  # deal found, Category empty/unknown
NO_DEAL_MATCH = "NO_DEAL_MATCH"  # quote not linked to a deal (yet)

CATEGORY_TO_SEGMENT = {
    "reactivation": RESIDENTIAL,
    "small jobs": RESIDENTIAL,
    "big jobs - homeowners": RESIDENTIAL,
    "big contracts - maintenance": PROPERTY_MANAGEMENT,
    "big jobs - gcs": GC_PROJECT,
}

LABELS = {
    RESIDENTIAL: "Residential / Homeowner",
    PROPERTY_MANAGEMENT: "Property Management / Maintenance",
    GC_PROJECT: "GC / Project Work",
    UNCATEGORIZED: "Uncategorized (deal has no Category)",
    NO_DEAL_MATCH: "No Pipedrive deal matched",
}
ORDER = [RESIDENTIAL, PROPERTY_MANAGEMENT, GC_PROJECT, UNCATEGORIZED, NO_DEAL_MATCH]


def segment_for_category(category: str | None) -> str:
    if not category:
        return UNCATEGORIZED
    return CATEGORY_TO_SEGMENT.get(category.strip().lower(), UNCATEGORIZED)
