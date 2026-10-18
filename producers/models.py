import datetime
from decimal import Decimal
from zoneinfo import available_timezones

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords


class Weekday(models.IntegerChoices):
    MON = 0, "Понедельник"
    TUE = 1, "Вторник"
    WED = 2, "Среда"
    THU = 3, "Четверг"
    FRI = 4, "Пятница"
    SAT = 5, "Суббота"
    SUN = 6, "Воскресенье"


def default_delivery_weekdays():
    return [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]


class Producer(models.Model):
    """Производство — тенант: все остальные данные принадлежат ему."""

    name = models.CharField(max_length=200, unique=True, verbose_name="Название")
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активно",
        help_text="Неактивное производство не принимает заказы.",
    )
    timezone = models.CharField(
        max_length=64,
        default="Europe/Moscow",
        verbose_name="Часовой пояс",
        help_text="Например, Europe/Moscow. По нему считается срок приёма заказов.",
    )

    order_cutoff_time = models.TimeField(
        default=datetime.time(14, 0),
        verbose_name="Приём заказов до",
        help_text="Заказы, оформленные позже, уходят на ближайшую следующую дату.",
    )
    lead_days = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Дней до доставки",
        help_text="Через сколько дней после заказа возможна доставка. 1 — на следующий день.",
    )
    delivery_weekdays = ArrayField(
        models.PositiveSmallIntegerField(choices=Weekday),
        default=default_delivery_weekdays,
        verbose_name="Дни доставки",
        help_text="В какие дни недели производство возит заказы.",
    )
    min_order_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Минимальный заказ, ₽",
        help_text="Для точек, у которых свой минимум не задан. 0 — без ограничения.",
    )

    debt_control_enabled = models.BooleanField(
        default=True,
        verbose_name="Контроль долга",
        help_text=(
            "Если долг заказчика выше порога, заказ не отклоняется, а получает "
            "статус «Требует решения»."
        ),
    )
    debt_limit_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("50000"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Порог долга, ₽",
        help_text="Для заказчиков, у которых свой порог не задан.",
    )
    overdue_days_limit = models.PositiveSmallIntegerField(
        default=14,
        verbose_name="Порог просрочки, дней",
        help_text="Для заказчиков, у которых свой порог не задан.",
    )
    onboarding_done = models.BooleanField(
        default=False,
        verbose_name="Настройка завершена",
        help_text="Отмечается после того, как производство подтвердило пороги долга.",
    )

    summary_emails = ArrayField(
        models.EmailField(),
        default=list,
        blank=True,
        verbose_name="Email для сводки",
        help_text="Адреса через запятую. Сводка с Excel придёт на них автоматически.",
    )
    summary_send_time = models.TimeField(
        null=True,
        blank=True,
        verbose_name="Отправлять сводку в",
        help_text="Время по часовому поясу производства. Пусто — не отправлять.",
    )
    summary_days_ahead = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Сводка на дату",
        help_text=(
            "0 — на сегодня, 1 — на завтра и так далее. Не может быть больше, чем "
            "«Дней до доставки»."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        verbose_name = "Производство"
        verbose_name_plural = "Производства"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.timezone and self.timezone not in available_timezones():
            raise ValidationError({"timezone": "Неизвестный часовой пояс."})
        weekdays = self.delivery_weekdays or []
        if not weekdays:
            raise ValidationError({"delivery_weekdays": "Выберите хотя бы один день."})
        self._clean_summary()

    def _clean_summary(self):
        if not (self.summary_emails or self.summary_send_time):
            return
        if not self.summary_emails:
            raise ValidationError({"summary_emails": "Укажите хотя бы один email."})
        if not self.summary_send_time:
            raise ValidationError({"summary_send_time": "Укажите время отправки."})
        if self.summary_days_ahead > self.lead_days:
            raise ValidationError(
                {
                    "summary_days_ahead": (
                        "Приём заказов на эту дату ещё не закрыт, сводка будет неполной."
                    )
                }
            )
        if (
            self.summary_days_ahead == self.lead_days
            and self.summary_send_time < self.order_cutoff_time
        ):
            raise ValidationError(
                {
                    "summary_send_time": (
                        "Отправка раньше окончания приёма заказов даст неполную сводку."
                    )
                }
            )

    @property
    def summary_enabled(self):
        return bool(self.summary_emails and self.summary_send_time)
