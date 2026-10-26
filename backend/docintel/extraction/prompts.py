"""Prompts. Static instructions first, document last: the provider's prompt cache then
serves the shared prefix across every document (measured as cached tokens)."""
from __future__ import annotations

import hashlib
import json

from .schema import GROUP_PROPERTIES

SYSTEM = """You are a meticulous contract analyst. You extract facts from contract pages into JSON.

Rules:
1. Use only the pages provided. If something is not stated, return null for its value and quote. Never guess.
2. "quote" must be copied character-for-character from the page text — the shortest span that proves the value.
   "page" is the number in the "=== Page N ===" marker above that text.
3. Dates are ISO YYYY-MM-DD. Numeric dates follow the document's locale: Vietnamese and bilingual
   Vietnamese documents write day/month/year.
4. Amounts are plain numbers in full units: "1.500.000.000 VNĐ" -> 1500000000, "USD 1.2 million" -> 1200000.
5. Amendments override the original agreement: report the amended value and cite the amendment.
6. Deleted (struck-through) text has already been removed from the pages; ignore what is not there.
7. A liability cap, insurance amount or penalty is not the contract value. A cure period for breach is not a
   termination-for-convenience notice period. Insurance policies being "renewed" is not auto-renewal.
8. [signed] marks a drawn signature detected next to a signature line; [stamp] marks a company seal.
   A signature block with blank lines and no such marker is not signed.
9. Report what the document states; do not compute end dates or deadlines yourself."""

GROUP_INSTRUCTIONS = {
    "parties": "Extract the contracting parties and the people in the signature blocks of the main agreement.",
    "term": "Extract the effective date, how the term ends, renewal, notice to prevent renewal, and termination "
            "for convenience.",
    "commercial": "Extract the price or recurring fee, payment terms, and the rows of any price table.",
    "law": "Extract the document type, its title and the governing law.",
}


def build_messages(groups: list[str], pages_markdown: str, *, title: str | None, language: str, total_pages: int,
                   page_numbers: list[int]) -> list[dict]:
    task = " ".join(GROUP_INSTRUCTIONS[g] for g in groups)
    shown = "all pages" if len(page_numbers) == total_pages else f"pages {_ranges(page_numbers)} of {total_pages}"
    user = (f"Task: {task}\n"
            f"Document: {title or 'untitled'} | language: {language} | {total_pages} pages | provided: {shown}\n\n"
            f"{pages_markdown}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def repair_message(errors: list[str]) -> dict:
    return {"role": "user", "content": "Your JSON has problems:\n- " + "\n- ".join(errors[:12]) +
            "\nReturn the corrected JSON for the same task. Keep every other value unchanged."}


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


def prompt_version() -> str:
    blob = json.dumps({"system": SYSTEM, "groups": GROUP_INSTRUCTIONS, "schema": GROUP_PROPERTIES}, sort_keys=True,
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]
