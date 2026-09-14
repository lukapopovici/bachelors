from prometheus_client import Counter, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "msvmed_http_requests_total",
    "Total HTTP requests handled by the API",
    ("method", "route", "status"),
)
REQUEST_LATENCY = Histogram(
    "msvmed_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("method", "route"),
)


def metrics_payload() -> bytes:
    return generate_latest()