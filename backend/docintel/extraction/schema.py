"""The extraction schema (contract-v1): what the model returns, field by field.

Design rules, each visible below:

* **Every value carries evidence** — a verbatim ``quote`` and its ``page``. That is
  what makes a value checkable (grounding), reviewable (the UI highlights it) and
  auditable.
* **Null is an answer.** "The document does not say" must be expressible and is
  graded like any other value; guessing is worse than null.
* **The model reads, code derives.** The model reports what is written (initial
  term of 24 months, 60 days' notice); code computes what follows (term end,
  cancel-by date). Arithmetic on dates is exactly where models are least reliable
  and code is exact.
* **Strict JSON schema** (constrained decoding): structure errors become rare, so
  validation focuses on meaning (impossible dates, pages out of range).
* **Field groups** let long documents send each group only the pages it needs.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

DATE = {"type": ["string", "null"], "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
        "description": "ISO 8601 date YYYY-MM-DD"}
QUOTE = {"type": ["string", "null"],
         "description": "Shortest verbatim span copied character-for-character from the page that proves the value "
                        "(max ~300 characters). Null when the value is null."}
PAGE = {"type": ["integer", "null"], "description": "Page number from the '=== Page N ===' marker of the quote"}


def cited(value: dict[str, Any], description: str, **extra: dict[str, Any]) -> dict[str, Any]:
    props = {"value": value, **extra, "quote": QUOTE, "page": PAGE}
    return {"type": "object", "additionalProperties": False, "required": list(props), "description": description,
            "properties": props}


def nullable_object(properties: dict[str, Any], description: str = "") -> dict[str, Any]:
    obj = {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}
    return {"anyOf": [obj, {"type": "null"}], **({"description": description} if description else {})}


DOC_TYPES = ["services_agreement", "nda", "lease", "purchase_agreement", "sow", "employment", "license",
             "other"]
PARTY_ROLES = ["customer", "vendor", "tenant", "landlord", "buyer", "seller", "discloser", "recipient",
               "employer", "employee", "licensor", "licensee", "other"]

GROUP_PROPERTIES: dict[str, dict[str, Any]] = {
    "parties": {
        "parties": {
            "type": "array", "description": "The contracting legal entities or persons (not their signatories).",
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["name", "role", "quote", "page"],
                      "properties": {"name": {"type": "string", "description": "Legal name exactly as written"},
                                     "role": {"type": "string", "enum": PARTY_ROLES},
                                     "quote": QUOTE, "page": PAGE}}},
        "signatories": {
            "type": "array",
            "description": "People named in the signature blocks of the main agreement (not of amendments).",
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["name", "title", "party", "signed_date", "is_signed", "quote", "page"],
                      "properties": {
                          "name": {"type": "string"},
                          "title": {"type": ["string", "null"]},
                          "party": {"type": ["string", "null"], "description": "Party this person signs for"},
                          "signed_date": DATE,
                          "is_signed": {"type": "boolean", "description":
                                        "True only if the block shows a signature: a [signed] or [stamp] marker, "
                                        "'/s/', or a filled signature date. Blank lines mean not signed."},
                          "quote": QUOTE, "page": PAGE}}},
    },
    "term": {
        "effective_date": cited(DATE, "Date the agreement takes effect.",
                                basis={"type": ["string", "null"], "enum": ["stated_date", "last_signature", "other", None],
                                       "description": "stated_date: written in the text; last_signature: defined as "
                                                      "the date of the last signature (then value = latest signature date)"}),
        "expiration_type": cited({"type": "string", "enum": ["fixed_date", "duration", "evergreen", "on_completion",
                                                             "not_stated"]},
                                 "How the initial term ends: an explicit date, a duration from the effective date, "
                                 "until terminated (evergreen), on completion of obligations, or not stated."),
        "expiration_date": cited(DATE, "End date of the initial term ONLY if written as a calendar date."),
        "initial_term_months": cited({"type": ["integer", "null"], "minimum": 0},
                                     "Initial term as a duration in months (years x 12) if stated as a duration."),
        "auto_renewal": cited({"type": ["boolean", "null"]},
                              "True if the agreement renews automatically unless a party gives notice; false if it "
                              "expressly does not renew automatically; null if the document is silent."),
        "renewal_term_months": cited({"type": ["integer", "null"], "minimum": 0},
                                     "Length of each automatic renewal term in months."),
        "non_renewal_notice": cited(nullable_object({
            "amount": {"type": "integer", "minimum": 0},
            "unit": {"type": "string", "enum": ["days", "weeks", "months"]}}),
            "Notice required to PREVENT automatic renewal (not the termination-for-breach cure period)."),
        "termination_for_convenience": cited({"type": ["boolean", "null"]},
                                             "Can a party terminate without cause (for convenience)? "
                                             "Termination for breach does not count."),
        "termination_notice_days": cited({"type": ["integer", "null"], "minimum": 0},
                                         "Notice period in days for termination for convenience."),
    },
    "commercial": {
        "contract_value": cited(nullable_object({
            "amount": {"type": "number", "minimum": 0, "description": "Plain number, no separators, in full units"},
            "currency": {"type": "string", "pattern": "^[A-Z]{3}$", "description": "ISO 4217, e.g. USD, VND"}}),
            "The price or recurring fee the agreement commits to — not a liability cap or insurance amount.",
            period={"type": ["string", "null"], "enum": ["one_time", "monthly", "quarterly", "annual", "total", None]}),
        "payment_terms_days": cited({"type": ["integer", "null"], "minimum": 0},
                                    "Days within which invoices must be paid (e.g. net 30)."),
        "line_items": {
            "type": "array", "description": "Rows of the price / line-item table, excluding total rows.",
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["description", "quantity", "unit_price", "amount", "page"],
                      "properties": {"description": {"type": "string"},
                                     "quantity": {"type": ["number", "null"]},
                                     "unit_price": {"type": ["number", "null"]},
                                     "amount": {"type": ["number", "null"]},
                                     "page": PAGE}}},
    },
    "law": {
        "document_type": {"type": "string", "enum": DOC_TYPES},
        "title": {"type": ["string", "null"], "description": "Document title as written"},
        "governing_law": cited({"type": ["string", "null"]},
                               "Jurisdiction whose law governs, name only (e.g. 'New York', 'England and Wales', "
                               "'Vietnam')."),
    },
}
GROUPS = list(GROUP_PROPERTIES)

FIELD_GROUP = {field: group for group, props in GROUP_PROPERTIES.items() for field in props}
LIST_FIELDS = {"parties", "signatories", "line_items"}
UNCITED_FIELDS = {"document_type", "title"}
CRITICAL_FIELDS = ["parties", "signatories", "effective_date", "expiration_type", "expiration_date",
                   "initial_term_months", "auto_renewal", "non_renewal_notice", "contract_value"]
REVIEW_FIELDS = [f for g in GROUPS for f in GROUP_PROPERTIES[g]]


def group_schema(groups: list[str]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for g in groups:
        props.update(deepcopy(GROUP_PROPERTIES[g]))
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}
