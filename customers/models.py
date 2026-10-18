import datetime
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class LegalForm(models.TextChoices):
    SOLE_PROPRIETOR = "IP", "ИП"
    COMPANY = "LEGAL", "Юридическое лицо"


class Customer(models.Model):
    """Заказчик — сторона расчётов. Баланс, долг и порог считаются на нём."""

    producer = models.ForeignKey(
        "producers.Producer",
        on_delete=models.PROTECT,
        related_name="customers",
        verbose_name="Производство",
    )
    name = models.CharField(
        max_length=200,
        verbose_name="Название",
        help_text="Как заказчик значится в счетах, например «ИП Иванов А. А.».",
    )
    legal_form = models.CharField(
        max_length=10,
        choices=LegalForm,
        default=LegalForm.SOLE_PROPRIETOR,
        verbose_name="Форма",
    )
    inn = models.CharField(
        max_length=12,
        blank=True,
        validators=[RegexValidator(r"^(\d{10}|\d{12})$", "ИНН — 10 или 12 цифр.")],
        verbose_name="ИНН",
    )
    payment_delay_days = models.PositiveSmallIntegerField(
        default=14,
        verbose_name="Отсрочка оплаты, дней",
        help_text="Сколько дней после доставки у заказчика есть на оплату счёта.",
    )
    debt_limit_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Свой порог долга, ₽",
        help_text="Оставьте пустым, чтобы применялся порог производства.",
    )
    overdue_days_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Свой порог просрочки, дней",
        help_text="Оставьте пустым, чтобы применялся порог производства.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен",
        help_text="Заказы от неактивного заказчика не принимаются.",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        verbose_name = "Заказчик"
        verbose_name_plural = "Заказчики"
        constraints = [
            models.UniqueConstraint(
                fields=["producer", "name"], name="unique_customer_name_per_producer"
            ),
            models.UniqueConstraint(
                fields=["producer", "inn"],
                condition=~models.Q(inn=""),
                name="unique_customer_inn_per_producer",
            ),
        ]

    def __str__(self):
        return self.name


class Point(models.Model):
    """Точка — адрес доставки заказчика."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="points",
        verbose_name="Заказчик",
    )
    name = models.CharField(
        max_length=200,
        verbose_name="Название",
        help_text="Как точку называют в работе, например «Кофейня на Ленина».",
    )
    address = models.CharField(max_length=500, verbose_name="Адрес доставки")
    contact_name = models.CharField(
        max_length=200, blank=True, verbose_name="Контактное лицо"
    )
    contact_phone = models.CharField(max_length=32, blank=True, verbose_name="Телефон")
    min_order_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Свой минимальный заказ, ₽",
        help_text=(
            "Проверяется по сумме одного заказа. "
            "Оставьте пустым, чтобы применялся минимум производства."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
        help_text="Неактивная точка не может оформлять заказы.",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")

    history = HistoricalRecords()

    class Meta:
        ordering = ["customer__name", "name"]
        verbose_name = "Точка"
        verbose_name_plural = "Точки"
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "name"], name="unique_point_name_per_customer"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.customer})"


def default_invitation_expiry():
    return timezone.now() + datetime.timedelta(days=7)


class Invitation(models.Model):
    """Ссылка-приглашение менеджера на точку: по ней он входит через Telegram."""

    point = models.ForeignKey(
        Point,
        on_delete=models.CASCADE,
        related_name="invitations",
        verbose_name="Точка",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        default=secrets.token_urlsafe,
        editable=False,
        verbose_name="Токен",
    )
    expires_at = models.DateTimeField(
        default=default_invitation_expiry,
        verbose_name="Действует до",
        help_text="После этого срока ссылка перестаёт работать.",
    )
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="used_invitations",
        verbose_name="Кто принял",
    )
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда принято")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_invitations",
        verbose_name="Кто создал",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Приглашение"
        verbose_name_plural = "Приглашения"

    def __str__(self):
        return f"Приглашение на {self.point}"

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()

    @property
    def link(self):
        bot = settings.TELEGRAM_BOT_USERNAME
        if not bot:
            return ""
        return f"https://t.me/{bot}?startapp={self.token}"
