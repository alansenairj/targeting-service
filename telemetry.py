"""Logs JSON (sempre) e OpenTelemetry (traces via OTLP/gRPC para o Alloy)."""
import json
import logging
import os
import time

from flask import g, request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class JsonFormatter(logging.Formatter):
    """Uma linha JSON por log. trace_id/span_id vem do LoggingInstrumentor."""

    def __init__(self, service_name):
        super().__init__()
        self.service_name = service_name

    def format(self, record):
        out = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "service": self.service_name,
            "msg": record.getMessage(),
        }
        trace_id = getattr(record, "otelTraceID", "0")
        if trace_id and trace_id != "0":
            out["trace_id"] = trace_id
            out["span_id"] = getattr(record, "otelSpanID", "")
        for key in ("method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def _setup_logging(service_name):
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter(service_name))


def _access_log(app):
    log = logging.getLogger("access")

    @app.before_request
    def _start():
        g._t0 = time.monotonic()

    @app.after_request
    def _log(response):
        if request.path != "/health":
            log.log(
                logging.ERROR if response.status_code >= 500 else logging.INFO,
                "request",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": int((time.monotonic() - g._t0) * 1000),
                },
            )
        return response


def setup(app, service_name):
    """Liga logs JSON, log de acesso e (se houver coletor) traces. Chamar antes do pool/clientes.

    Sem OTEL_EXPORTER_OTLP_ENDPOINT nao instrumenta traces (dev local e testes);
    os logs continuam em JSON, sem trace_id.
    """
    _setup_logging(service_name)
    _access_log(app)
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FlaskInstrumentor().instrument_app(app, excluded_urls="health")
    RequestsInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()
    # Logs seguem por stdout (Alloy coleta dos pods); sem handler OTLP duplicado.
    LoggingInstrumentor().instrument(
        inject_trace_context=True, enable_log_auto_instrumentation=False
    )
    _setup_logging(service_name)  # reaplica: o instrumentor nao troca formatters
