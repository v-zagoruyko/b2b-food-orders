from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords
from viewflow.fsm import State

from catalog.models import Unit


class OrderStatus(models.TextChoices):
    # NEW → CONFIRMED: производство берёт заказ в работу, в сводку попадают
    # только CONFIRMED. NEEDS_REVIEW — порог долга превышен, решает производство.
    # DELIVERED, REJECTED, CANCELED — конечные; правки только вручную из админки.
    NEW = "NEW", "Новый"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Требует решения"
    CONFIRMED = "CONFIRMED", "Подтверждён"
    DELIVERED = "DELIVERED", "Доставлен"
    REJECTED = "REJECTED", "Отклонён"
    CANCELED = "CANCELED", "Отменён"


# Статусы, по которым заказчик считается должным: заказ принят производством.
DEBT_STATUSES = (OrderStatus.CONFIRMED, OrderStatus.DELIVERED)


class ReviewReason(models.TextChoices):
    DEBT_AMOUNT = "DEBT_AMOUNT", "Превышена сумма долга"
    OVERDUE = "OVERDUE", "Превышен срок просрочки"


class Order(models.Model):
    point = models.ForeignKey(
        "customers.Point",
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="Точка",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="Кто оформил",
    )
    delivery_date = models.DateField(db_index=True, verbose_name="Дата доставки")
    status = models.CharField(
        max_length=20,
        choices=OrderStatus,
        default=OrderStatus.NEW,
        db_index=True,
        verbose_name="Статус",
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name="Сумма, ₽",
        help_text="Считается автоматически из строк заказа.",
    )
    comment = models.TextField(
        blank=True,
        verbose_name="Комментарий точки",
        help_text="Пожелания к заказу: время, подъезд, замены.",
    )

    review_reason = models.CharField(
        max_length=20,
        choices=ReviewReason,
        blank=True,
        verbose_name="Причина проверки",
    )
    debt_at_order = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Долг на момент заказа, ₽",
    )
    overdue_days_at_order = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Просрочка на момент заказа, дней"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="decided_orders",
        verbose_name="Кто принял решение",
    )
    decided_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Когда принято решение"
    )
    cancel_reason = models.TextField(blank=True, verbose_name="Причина отмены")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменён")

    history = HistoricalRecords()

    class Meta:
        ordering = ["-delivery_date", "-id"]
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        permissions = [
            ("decide_order", "Может подтверждать и отклонять заказы"),
            ("cancel_order", "Может отменять заказы"),
            ("deliver_order", "Может отмечать заказы доставленными"),
        ]

    def __str__(self):
        return f"Заказ №{self.pk}"

    _state_fsm = State(OrderStatus, default=OrderStatus.NEW)

    @_state_fsm.getter()
    def _get_fsm_state(self):
        return self.status

    @_state_fsm.setter()
    def _set_fsm_state(self, value):
        self.status = value

    @_state_fsm.transition(
        source=[OrderStatus.NEW, OrderStatus.NEEDS_REVIEW],
        target=OrderStatus.CONFIRMED,
    )
    def confirm(self):
        pass

    @_state_fsm.transition(source=OrderStatus.NEEDS_REVIEW, target=OrderStatus.REJECTED)
    def reject(self):
        pass

    @_state_fsm.transition(source=OrderStatus.CONFIRMED, target=OrderStatus.DELIVERED)
    def deliver(self):
        pass

    @_state_fsm.transition(
        source=[OrderStatus.NEW, OrderStatus.NEEDS_REVIEW, OrderStatus.CONFIRMED],
        target=OrderStatus.CANCELED,
    )
    def cancel(self):
        pass

    def recalculate_total(self):
        total = sum((line.line_total for line in self.lines.all()), Decimal("0"))
        if total != self.total:
            self.total = total
            self.save(update_fields=["total", "updated_at"])


class SummaryTrigger(models.TextChoices):
    AUTO = "AUTO", "Автоматически"
    MANUAL = "MANUAL", "Вручную"


class SummaryDelivery(models.Model):
    """Журнал отправок сводки на email."""

    producer = models.ForeignKey(
        "producers.Producer",
        on_delete=models.CASCADE,
        related_name="summary_deliveries",
        verbose_name="Производство",
    )
    delivery_date = models.DateField(db_index=True, verbose_name="Сводка на дату")
    trigger = models.CharField(
        max_length=10, choices=SummaryTrigger, verbose_name="Как отправлена"
    )
    recipients = ArrayField(models.EmailField(), verbose_name="Получатели")
    orders_count = models.PositiveIntegerField(verbose_name="Заказов в сводке")
    pending_count = models.PositiveIntegerField(
        verbose_name="Заказов ждут подтверждения",
        help_text="Не вошли в сводку: статусы «Новый» и «Требует решения».",
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="Сумма, ₽"
    )
    error = models.TextField(blank=True, verbose_name="Ошибка")
    sent_at = models.DateTimeField(auto_now_add=True, verbose_name="Когда")
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Кто отправил",
    )

    class Meta:
        ordering = ["-sent_at"]
        verbose_name = "Отправка сводки"
        verbose_name_plural = "Отправки сводок"

    def __str__(self):
        return f"Сводка на {self.delivery_date:%d.%m.%Y} ({self.producer})"


class OrderLine(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="Заказ",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.PROTECT,
        related_name="order_lines",
        verbose_name="Позиция",
    )
    # Снапшоты: прайс и название меняются, старые заказы и долги не должны «плыть».
    product_name = models.CharField(max_length=255, verbose_name="Название")
    unit = models.CharField(max_length=10, choices=Unit, verbose_name="Единица")
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Цена за единицу, ₽",
        help_text="Фиксируется в момент оформления заказа.",
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name="Количество",
    )
    line_total = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="Сумма, ₽"
    )

    class Meta:
        ordering = ["id"]
        verbose_name = "Строка заказа"
        verbose_name_plural = "Строки заказа"
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product"], name="unique_product_per_order"
            ),
        ]

    def __str__(self):
        return f"{self.product_name} × {self.quantity}"

    def save(self, *args, **kwargs):
        # Смена позиции у существующей строки обновляет снапшоты: название,
        # единицу и цену берём у новой позиции.
        product_changed = False
        if self.pk:
            previous = (
                OrderLine.objects.filter(pk=self.pk)
                .values_list("product_id", flat=True)
                .first()
            )
            product_changed = previous != self.product_id
        if product_changed or not self.product_name:
            self.product_name = self.product.name
        if product_changed or not self.unit:
            self.unit = self.product.unit
        if product_changed or self.unit_price is None:
            self.unit_price = self.product.price
        self.line_total = self.calculate_line_total(self.unit_price, self.quantity)
        super().save(*args, **kwargs)
        self.order.recalculate_total()

    def delete(self, *args, **kwargs):
        order = self.order
        result = super().delete(*args, **kwargs)
        order.recalculate_total()
        return result

    @staticmethod
    def calculate_line_total(unit_price, quantity):
        return (unit_price * quantity).quantize(Decimal("0.01"))
