"""Document-type classification: upload PDFs, get one type each with evidence. Stateless —
nothing is stored except the model call's metering row."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..classify import DEFAULT_TYPES, classify_pdf, is_custom, load_types, reset_types, save_types
from ..llm.client import LLMError

router = APIRouter(prefix="/api", tags=["classify"])
MAX_BYTES = 60 * 1024 * 1024


class DocTypeIn(BaseModel):
    code: str
    label: str = ""
    definition: str = ""


class TypesIn(BaseModel):
    types: list[DocTypeIn]


@router.get("/classify/types")
def get_types() -> dict[str, Any]:
    return {"types": load_types(), "custom": is_custom(), "default_count": len(DEFAULT_TYPES)}


@router.put("/classify/types")
def put_types(body: TypesIn) -> dict[str, Any]:
    try:
        types = save_types([t.model_dump() for t in body.types])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"types": types, "custom": True, "default_count": len(DEFAULT_TYPES)}


@router.delete("/classify/types")
def delete_types() -> dict[str, Any]:
    reset_types()
    return {"types": load_types(), "custom": False, "default_count": len(DEFAULT_TYPES)}


@router.post("/classify")
async def classify(files: list[UploadFile] = File(...), head: int = Form(3), tail: int = Form(2)) -> dict[str, Any]:
    if not (0 <= head <= 10 and 0 <= tail <= 10 and head + tail >= 1):
        raise HTTPException(400, "head and tail must be 0–10 pages, at least one page in total")

    async def one(f: UploadFile) -> dict[str, Any]:
        name = f.filename or "document.pdf"
        data = await f.read()
        if len(data) > MAX_BYTES:
            return {"filename": name, "error": "file larger than 60 MB"}
        if b"%PDF-" not in data[:1024]:
            return {"filename": name, "error": "not a PDF"}
        try:
            return await classify_pdf(data, name, head=head, tail=tail)
        except (ValueError, LLMError) as exc:
            return {"filename": name, "error": str(exc)}

    return {"results": list(await asyncio.gather(*(one(f) for f in files)))}
