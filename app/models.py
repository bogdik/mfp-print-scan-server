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


class SupplyLevelOut(BaseModel):
    name: str
    percent: int | None
    kind: str


class PrinterStatusOut(BaseModel):
    state: str  # "idle" | "printing" | "stopped" | "offline" | "unknown"
    state_label: str
    reasons: list[str]  # raw keywords, e.g. "media-empty"
    reason_labels: list[str]  # translated, same order
    accepting_jobs: bool
    supplies: list[SupplyLevelOut] = []


class UsageTotalsOut(BaseModel):
    jobs: int
    sent: int
    failed: int
    sheets: int  # sum of pages * copies, counting only jobs where pages is known


class UsageByKeyOut(BaseModel):
    key: str  # a user name, or a printer name; "unknown" when not recorded
    jobs: int
    sheets: int


class UsageOut(BaseModel):
    totals: UsageTotalsOut
    by_user: list[UsageByKeyOut]
    by_printer: list[UsageByKeyOut]


class TokenOut(BaseModel):
    id: str
    name: str
    created_at: float
    last_used_at: float | None = None


class TokenCreateIn(BaseModel):
    name: str = ""


class TokenCreateOut(BaseModel):
    id: str
    name: str
    token: str  # only ever shown once, right after creation


class QuotaOut(BaseModel):
    user: str
    limit: int | None  # sheets/month; None = unlimited
    used: int  # sheets sent so far this calendar month


class QuotaSetIn(BaseModel):
    limit: int | None  # None = remove the quota (unlimited)


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
    user: str | None = None  # who sent it, when known (auth=yes, or an IPP client's own user name)
    pages: int | None = None  # best-effort sheet count (see printing/convert.py's page_count()); None = not counted
