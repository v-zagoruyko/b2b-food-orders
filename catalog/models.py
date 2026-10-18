from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords


class Unit(models.TextChoices):
    PIECE = "PIECE", "шт"
    KG = "KG", "кг"
    LITER = "LITER", "л"
    PACK = "PACK", "уп"


class Category(models.Model):
    producer = models.ForeignKey(
        "producers.Producer",
        on_delete=models.PROTECT,
        related_name="categories",
        verbose_name="Производство",
    )
    name = models.CharField(max_length=200, verbose_name="Название")
    sort_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        verbose_name="Порядок сортировки",
        help_text="Чем меньше число, тем выше категория в каталоге.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
        help_text="Скрытые категории не отображаются в каталоге точек.",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        constraints = [
            models.UniqueConstraint(
                fields=["producer", "name"], name="unique_category_name_per_producer"
            ),
        ]

    def __str__(self):
        return self.name


class Product(models.Model):
    producer = models.ForeignKey(
        "producers.Producer",
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Производство",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Категория",
    )
    name = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(
        blank=True,
        verbose_name="Описание",
        help_text="Состав, вес упаковки — всё, что нужно знать при заказе.",
    )
    unit = models.CharField(
        max_length=10,
        choices=Unit,
        default=Unit.PIECE,
        verbose_name="Единица измерения",
        help_text="В этих единицах точка указывает количество и видит цену.",
    )
    order_step = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name="Шаг заказа",
        help_text=(
            "Количество должно быть кратно шагу, например 0.5 кг или 10 шт. "
            "Оставьте пустым, если шаг не нужен."
        ),
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Цена за единицу, ₽",
        help_text="Изменение цены не затрагивает уже оформленные заказы.",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        verbose_name="Порядок сортировки",
        help_text="Порядок внутри категории: чем меньше число, тем выше позиция.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
        help_text="Скрытые позиции не отображаются в каталоге, но остаются в старых заказах.",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["category__sort_order", "sort_order", "name"]
        verbose_name = "Позиция"
        verbose_name_plural = "Позиции"
        constraints = [
            models.UniqueConstraint(
                fields=["producer", "name"], name="unique_product_name_per_producer"
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.category_id and self.producer_id:
            if self.category.producer_id != self.producer_id:
                raise ValidationError(
                    {"category": "Категория принадлежит другому производству."}
                )
