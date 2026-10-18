from django.db import connection
from django.http import HttpResponse


def healthz(request):
    """Проверка для Docker и мониторинга: приложение живо, база отвечает."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("db unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")
