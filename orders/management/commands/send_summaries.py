from django.core.management.base import BaseCommand

from orders.notifications import send_due_summaries


class Command(BaseCommand):
    help = (
        "Отправляет на email сводки, время которых наступило. "
        "Запускать по расписанию, например раз в 5 минут."
    )

    def handle(self, *args, **options):
        for record in send_due_summaries():
            status = f"ошибка: {record.error}" if record.error else "отправлена"
            self.stdout.write(
                f"{record.producer}: сводка на {record.delivery_date:%d.%m.%Y} — {status}"
            )
