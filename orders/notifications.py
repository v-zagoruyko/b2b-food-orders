import datetime
import logging
from zoneinfo import ZoneInfo

from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from producers.models import Producer

from .exports import build_summary_workbook, workbook_to_bytes
from .models import SummaryDelivery, SummaryTrigger
from .summary import DeliverySummary, build_summary

logger = logging.getLogger(__name__)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def summary_filename(summary: DeliverySummary) -> str:
    return f"summary-{summary.delivery_date:%Y-%m-%d}.xlsx"


def _body(summary: DeliverySummary) -> str:
    lines = [
        f"Сводка заказов на {summary.delivery_date:%d.%m.%Y}.",
        f"Заказов: {summary.orders_count}, сумма {summary.total:,.2f} ₽.".replace(
            ",", " "
        ),
    ]
    if summary.pending_count:
        lines.append(
            f"Внимание: {summary.pending_count} заказов ждут подтверждения "
            "и не вошли в сводку."
        )
    if summary.items:
        lines += ["", "По позициям:"]
        lines += [
            f"  {item.name} — {item.quantity.normalize():f} {item.unit}"
            for item in summary.items
        ]
    lines += ["", "Полная сводка по позициям, точкам и раскладке — во вложении."]
    return "\n".join(lines)


def send_summary(
    producer: Producer,
    delivery_date: datetime.date,
    trigger: str,
    recipients: list[str] | None = None,
    sent_by=None,
) -> SummaryDelivery:
    """Отправляет сводку на email и записывает результат в журнал."""
    recipients = recipients or list(producer.summary_emails)
    summary = build_summary(producer, delivery_date)
    record = SummaryDelivery(
        producer=producer,
        delivery_date=delivery_date,
        trigger=trigger,
        recipients=recipients,
        orders_count=summary.orders_count,
        pending_count=summary.pending_count,
        total=summary.total,
        sent_by=sent_by,
    )
    message = EmailMessage(
        subject=f"Сводка на {delivery_date:%d.%m.%Y} — {producer.name}",
        body=_body(summary),
        to=recipients,
    )
    message.attach(
        summary_filename(summary),
        workbook_to_bytes(build_summary_workbook(summary)),
        XLSX_CONTENT_TYPE,
    )
    try:
        message.send()
    except Exception as exc:  # noqa: BLE001 — любая ошибка почты пишется в журнал
        logger.exception("Не удалось отправить сводку производства %s", producer.pk)
        record.error = str(exc)
    record.save()
    return record


def scheduled_summary_date(producer: Producer, now: datetime.datetime):
    """Дата сводки, которую пора отправить сейчас, или None."""
    if not (producer.is_active and producer.summary_enabled):
        return None
    local = now.astimezone(ZoneInfo(producer.timezone))
    if local.time() < producer.summary_send_time:
        return None
    day = local.date() + datetime.timedelta(days=producer.summary_days_ahead)
    if day.weekday() not in producer.delivery_weekdays:
        return None
    return day


# Неудачная автоотправка повторяется не чаще раза в 10 минут и не более 5 раз.
RETRY_INTERVAL = datetime.timedelta(minutes=10)
MAX_ATTEMPTS = 5


def _should_send_auto(producer, day, now) -> bool:
    attempts = SummaryDelivery.objects.filter(
        producer=producer, delivery_date=day, trigger=SummaryTrigger.AUTO
    ).order_by("-sent_at")
    if attempts.filter(error="").exists():
        return False
    failed = list(attempts[:MAX_ATTEMPTS])
    if len(failed) >= MAX_ATTEMPTS:
        return False
    return not failed or now - failed[0].sent_at >= RETRY_INTERVAL


def send_due_summaries(now: datetime.datetime | None = None) -> list[SummaryDelivery]:
    """Отправляет автоматические сводки, время которых наступило.

    Безопасно запускать часто и параллельно: производство блокируется на время
    отправки, уже отправленная на дату сводка повторно не уходит, ошибка одного
    производства не мешает остальным.
    """
    now = now or timezone.now()
    sent = []
    producer_ids = (
        Producer.objects.filter(is_active=True)
        .exclude(summary_send_time=None)
        .values_list("pk", flat=True)
    )
    for pk in producer_ids:
        try:
            with transaction.atomic():
                producer = (
                    Producer.objects.select_for_update(skip_locked=True)
                    .filter(pk=pk)
                    .first()
                )
                if producer is None:  # им уже занимается другой запуск
                    continue
                day = scheduled_summary_date(producer, now)
                if day is None or not _should_send_auto(producer, day, now):
                    continue
                summary = build_summary(producer, day)
                if summary.orders_count == 0 and summary.pending_count == 0:
                    continue
                sent.append(send_summary(producer, day, SummaryTrigger.AUTO))
        except Exception:
            logger.exception("Сбой автосводки производства %s", pk)
    return sent
