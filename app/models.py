from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


class PrinterOut(BaseModel):
    name: str
    is_default: bool


class OptionChoiceOut(BaseModel):
    value: str
    label: str


class PrinterOptionOut(BaseModel):
    key: str
    label: str
    choices: list[OptionChoiceOut]
    default: str | None
    limits: dict[str, dict[str, list[str]]] | None = None
    fallbacks: dict[str, dict[str, dict[str, str]]] | None = None


class PreviewOut(BaseModel):
    available: bool
    reason: str | None = None  # why there's no preview, when available=False
    pages: list[str] = []  # PNG data URLs, one per sheet
    total_pages: int = 0
    paper_mm: tuple[float, float] | None = None
    exact_layout: bool = False  # False = printer geometry unknown, A4 assumed


class JobOut(BaseModel):
    id: str
    filename: str
    printer: str | None
    copies: int
    options: dict[str, str] | None = None
    status: JobStatus
    error: str | None
    created_at: datetime
    note: str | None = None  # e.g. settings the server had to adjust
