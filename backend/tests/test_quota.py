from google.genai import errors

from app.agents.common import is_long_quota, quota_retry_delay


def _err(code: int, quota_id: str, delay: str | None):
    details = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        },
    ]
    if delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    return errors.ClientError(
        code,
        {
            "error": {
                "code": code,
                "message": f"quota {quota_id}",
                "status": "X",
                "details": details,
            }
        },
    )


def test_daily_quota_is_long():
    e = _err(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "46495s")
    assert quota_retry_delay(e) == 46495.0 and is_long_quota(e)


def test_per_minute_quota_is_retryable():
    e = _err(429, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "17s")
    assert quota_retry_delay(e) == 17.0 and not is_long_quota(e)


def test_other_errors_are_not_quota():
    assert not is_long_quota(_err(400, "x", None))
    assert quota_retry_delay(_err(503, "x", "5s")) is None
