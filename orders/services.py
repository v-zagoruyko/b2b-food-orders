import datetime
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from catalog.models import Product, Unit
from customers.models import Customer, Point
from customers.services import get_balance, get_review_reason

from .exceptions import (
    BelowMinimumOrder,
    DeliveryDateUnavailable,
    EmptyOrder,
    InvalidQuantity,
    OrderingUnavailable,
    PointAccessDenied,
    ProductUnavailable,
)
from .models import Order, OrderLine, OrderStatus

# Как далеко вперёд точка может заказывать.
MAX_HORIZON_DAYS = 30

WHOLE_UNITS = (Unit.PIECE, Unit.PACK)

# Границы под размер полей в БД: количество 10 знаков, сумма 12.
MAX_QUANTITY = Decimal("100000")
MAX_AMOUNT = Decimal("9999999999.99")


def _format_date(day: datetime.date) -> str:
    return f"{day:%d.%m}"


def _format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def available_delivery_dates(producer, now=None, limit=14) -> list[datetime.date]:
    """Ближайшие даты доставки, на которые ещё принимаются заказы.

    Дата D доступна, если это день доставки производства и заказ оформлен не
    позже `Приём заказов до` за `Дней до доставки` до D, по часовому поясу
    производства.
    """
    now = now or timezone.now()
    tz = ZoneInfo(producer.timezone)
    today = now.astimezone(tz).date()
    lead = datetime.timedelta(days=producer.lead_days)
    weekdays = set(producer.delivery_weekdays)

    dates = []
    for offset in range(producer.lead_days, MAX_HORIZON_DAYS + 1):
        day = today + datetime.timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        deadline = datetime.datetime.combine(
            day - lead, producer.order_cutoff_time, tzinfo=tz
        )
        if now <= deadline:
            dates.append(day)
            if len(dates) == limit:
                break
    return dates


def _check_quantity(product: Product, quantity: Decimal) -> None:
    name = product.name
    if not quantity.is_finite():
        raise InvalidQuantity(f"«{name}»: некорректное количество.")
    if quantity <= 0:
        raise InvalidQuantity(f"«{name}»: количество должно быть больше нуля.")
    if quantity > MAX_QUANTITY:
        raise InvalidQuantity(f"«{name}»: слишком большое количество.")
    if quantity != quantity.quantize(Decimal("0.001")):
        raise InvalidQuantity(f"«{name}»: слишком много знаков после запятой.")
    step = product.order_step
    if step:
        if quantity % step != 0:
            raise InvalidQuantity(
                f"«{name}»: количество должно быть кратно {step.normalize():f}."
            )
    elif product.unit in WHOLE_UNITS and quantity != quantity.to_integral_value():
        raise InvalidQuantity(f"«{name}»: количество должно быть целым.")


def _load_products(producer, lines) -> list[tuple[Product, Decimal]]:
    """Склеивает повторы и проверяет, что позиции можно заказать."""
    quantities: dict[int, Decimal] = {}
    products: dict[int, Product] = {}
    ids = [item.pk if isinstance(item, Product) else item for item, _ in lines]
    found = {p.pk: p for p in Product.objects.filter(pk__in=ids)}
    for item, quantity in lines:
        pk = item.pk if isinstance(item, Product) else item
        product = found.get(pk)
        if product is None or product.producer_id != producer.pk:
            raise ProductUnavailable("Одна из позиций не найдена.")
        if not product.is_active:
            raise ProductUnavailable(f"«{product.name}» сейчас недоступна для заказа.")
        try:
            quantity = Decimal(quantity)
        except (InvalidOperation, TypeError, ValueError):
            raise InvalidQuantity(
                f"«{product.name}»: некорректное количество."
            ) from None
        quantities[pk] = quantities.get(pk, Decimal("0")) + quantity
        products[pk] = product
    return [(products[pk], quantities[pk]) for pk in quantities]


@transaction.atomic
def place_order(
    *,
    point: Point,
    user,
    lines: Iterable[tuple[Product | int, Decimal]],
    delivery_date: datetime.date | None = None,
    comment: str = "",
    now: datetime.datetime | None = None,
) -> Order:
    """Оформляет заказ точки.

    Заказ принимается всегда, если соблюдены правила производства (срок,
    минимум, кратность). Превышение порога долга заказ не блокирует: он
    получает статус «Требует решения».
    """
    lines = list(lines)
    # Блокировка заказчика: два одновременных заказа не должны обойти порог.
    customer = (
        Customer.objects.select_for_update()
        .select_related("producer")
        .get(pk=point.customer_id)
    )
    producer = customer.producer
    now = now or timezone.now()

    if not (producer.is_active and customer.is_active and point.is_active):
        raise OrderingUnavailable(
            "Заказы сейчас не принимаются. Свяжитесь с производством."
        )
    if not user.points.filter(pk=point.pk).exists():
        raise PointAccessDenied("Нет доступа к этой точке.")
    if not lines:
        raise EmptyOrder("Добавьте в заказ хотя бы одну позицию.")

    dates = available_delivery_dates(producer, now)
    if not dates:
        raise DeliveryDateUnavailable(
            "Нет доступных дат доставки. Свяжитесь с производством."
        )
    if delivery_date is None:
        delivery_date = dates[0]
    elif delivery_date not in available_delivery_dates(
        producer, now, limit=MAX_HORIZON_DAYS
    ):
        nearest = dates[0]
        raise DeliveryDateUnavailable(
            f"На {_format_date(delivery_date)} заказы не принимаются. "
            f"Ближайшая дата — {_format_date(nearest)}.",
            nearest=nearest,
        )

    items = _load_products(producer, lines)
    prepared = []
    total = Decimal("0")
    for product, quantity in items:
        _check_quantity(product, quantity)
        line_total = OrderLine.calculate_line_total(product.price, quantity)
        total += line_total
        if line_total > MAX_AMOUNT or total > MAX_AMOUNT:
            raise InvalidQuantity("Слишком большая сумма заказа.")
        prepared.append((product, quantity, line_total))

    minimum = (
        point.min_order_amount
        if point.min_order_amount is not None
        else producer.min_order_amount
    )
    if total < minimum:
        raise BelowMinimumOrder(
            f"Минимальная сумма заказа — {_format_money(minimum)}.", minimum=minimum
        )

    balance = get_balance(customer, now.astimezone(ZoneInfo(producer.timezone)).date())
    reason = get_review_reason(customer, balance)
    order = Order.objects.create(
        point=point,
        created_by=user,
        delivery_date=delivery_date,
        status=OrderStatus.NEEDS_REVIEW if reason else OrderStatus.NEW,
        total=total,
        comment=comment,
        review_reason=reason or "",
        debt_at_order=balance.debt,
        overdue_days_at_order=balance.overdue_days,
    )
    OrderLine.objects.bulk_create(
        OrderLine(
            order=order,
            product=product,
            product_name=product.name,
            unit=product.unit,
            unit_price=product.price,
            quantity=quantity,
            line_total=line_total,
        )
        for product, quantity, line_total in prepared
    )
    return order
