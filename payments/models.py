import datetime
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords


class Payment(models.Model):
    """Оплата заказчика. Вносится вручную: данные могут отставать от банка."""

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Заказчик",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Сумма, ₽",
    )
    paid_at = models.DateField(
        default=datetime.date.today,
        db_index=True,
        verbose_name="Дата оплаты",
        help_text="Дата поступления денег по выписке, а не дата ввода.",
    )
    document_number = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Номер платёжки или счёта",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Кто внёс",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Внесено")

    history = HistoricalRecords()

    class Meta:
        ordering = ["-paid_at", "-id"]
        verbose_name = "Оплата"
        verbose_name_plural = "Оплаты"

    def __str__(self):
        return f"{self.customer}: {self.amount} ₽ от {self.paid_at:%d.%m.%Y}"
