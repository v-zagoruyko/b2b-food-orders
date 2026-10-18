from django.db import connection


def test_healthz_ok(client, db):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.content == b"ok"


def test_healthz_reports_unavailable_database(client, db, monkeypatch):
    def broken_cursor(*args, **kwargs):
        raise OSError("db down")

    with monkeypatch.context() as patch:
        patch.setattr(connection, "cursor", broken_cursor)
        response = client.get("/healthz")

    assert response.status_code == 503
