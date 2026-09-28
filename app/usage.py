"""Usage aggregation shared by the /api/usage report and quota enforcement,
so both agree on what a "sheet" is instead of each computing it separately."""

from datetime import datetime

from .i18n import t
from .models import JobOut, JobStatus, UsageByKeyOut, UsageOut, UsageTotalsOut
from .storage import job_store


def sheets(job: JobOut) -> int:
    """Paper actually used, in sheets; 0 (not None) when pages is unknown, so
    it can be summed without special-casing — a report that undercounts a
    few unknown jobs is more useful than one that crashes or lies with a
    fake page count."""
    return (job.pages or 0) * job.copies


def _by(jobs: list[JobOut], key) -> list[UsageByKeyOut]:
    totals: dict[str, list[int]] = {}
    for job in jobs:
        bucket = totals.setdefault(key(job) or t("usage.unknown"), [0, 0])
        bucket[0] += 1
        bucket[1] += sheets(job)
    return [
        UsageByKeyOut(key=k, jobs=v[0], sheets=v[1])
        for k, v in sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)
    ]


def report() -> UsageOut:
    jobs = job_store.list(limit=None)
    totals = UsageTotalsOut(
        jobs=len(jobs),
        sent=sum(1 for j in jobs if j.status == JobStatus.SENT),
        failed=sum(1 for j in jobs if j.status == JobStatus.FAILED),
        sheets=sum(sheets(j) for j in jobs),
    )
    return UsageOut(totals=totals, by_user=_by(jobs, lambda j: j.user), by_printer=_by(jobs, lambda j: j.printer))


def sheets_this_month(user: str) -> int:
    """Sheets `user` has sent so far in the current calendar month — the
    window print quotas are checked against. Derived from job history rather
    than a separate counter, so it can never drift from what actually
    happened (and a deleted job history entry naturally frees its quota).
    Unlike the report, a job with an unknown page count counts as one page
    per copy here, the same as quotas.enforce() assumes when admitting it."""
    now = datetime.now()
    return sum(
        (j.pages or 1) * j.copies for j in job_store.list(limit=None)
        if j.user == user and j.created_at.year == now.year and j.created_at.month == now.month
    )
