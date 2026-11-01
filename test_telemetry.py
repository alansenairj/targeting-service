"""Regressao da telemetria: logs sempre JSON; sem coletor nao instrumenta traces;
com coletor o log recebe trace_id."""
import importlib
import json
import logging

from flask import Flask

SERVICE = "targeting-service"


def _app():
    app = Flask("t")

    @app.route("/x")
    def x():
        logging.getLogger("t").info("dentro")
        return "ok"

    @app.route("/health")
    def health():
        return "ok"

    return app


def _linhas(capsys):
    return [json.loads(l) for l in capsys.readouterr().err.splitlines() if l.startswith("{")]


def test_logs_json_sem_coletor(monkeypatch, capsys):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    import telemetry

    importlib.reload(telemetry)
    logging.getLogger().handlers.clear()
    app = _app()
    telemetry.setup(app, SERVICE)
    client = app.test_client()
    assert client.get("/x").status_code == 200
    assert client.get("/health").status_code == 200
    linhas = _linhas(capsys)
    assert any(l["msg"] == "dentro" and l["service"] == SERVICE for l in linhas)
    acessos = [l for l in linhas if l["msg"] == "request"]
    assert [a["path"] for a in acessos] == ["/x"], "/health nao deve gerar log de acesso"
    assert acessos[0]["status"] == 200 and "trace_id" not in acessos[0]


def test_com_coletor_log_tem_trace_id(monkeypatch, capsys):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    import telemetry

    importlib.reload(telemetry)
    logging.getLogger().handlers.clear()
    app = _app()
    telemetry.setup(app, SERVICE)
    assert app.test_client().get("/x").status_code == 200
    dentro = [l for l in _linhas(capsys) if l["msg"] == "dentro"]
    assert dentro and len(dentro[0]["trace_id"]) == 32
