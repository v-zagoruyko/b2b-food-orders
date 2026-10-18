import datetime
from collections.abc import Iterable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedLine:
    product: Product
    quantity: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class PreparedOrder:
    """Проверенный заказ: всё, что нужно записать, без обращений на запись."""

    point: Point
    delivery_date: datetime.date
    lines: list[PreparedLine]
    total: Decimal
    status: str
    review_reason: str
    debt: Decimal
    overdue_days: int


def prepare_order(
    *,
    point: Point,
    user,
    lines: Iterable[tuple[Product | int, Decimal]],
    delivery_date: datetime.date | None = None,
    now: datetime.datetime | None = None,
    staff: bool = False,
    customer: Customer | None = None,
) -> PreparedOrder:
    """Проверяет заказ по правилам производства и считает его итоги.

    `staff=True` — заказ вносит сотрудник производства за точку (например,
    по телефону): проверка «менеджер точки» не нужна, а дату можно выбрать
    любую от сегодняшней, срок приёма и дни доставки не проверяются.
    Остальные правила и порог долга действуют для всех.
    """
    lines = list(lines)
    customer = customer or Customer.objects.select_related("producer").get(
        pk=point.customer_id
    )
    producer = customer.producer
    now = now or timezone.now()

    if not (producer.is_active and customer.is_active and point.is_active):
        raise OrderingUnavailable(
            "Заказы сейчас не принимаются. Свяжитесь с производством."
        )
    if not staff and not user.points.filter(pk=point.pk).exists():
        raise PointAccessDenied("Нет доступа к этой точке.")
    if not lines:
        raise EmptyOrder("Добавьте в заказ хотя бы одну позицию.")

    delivery_date = _resolve_delivery_date(producer, delivery_date, now, staff)

    items = _load_products(producer, lines)
    prepared = []
    total = Decimal("0")
    for product, quantity in items:
        _check_quantity(product, quantity)
        line_total = OrderLine.calculate_line_total(product.price, quantity)
        total += line_total
        if line_total > MAX_AMOUNT or total > MAX_AMOUNT:
            raise InvalidQuantity("Слишком большая сумма заказа.")
        prepared.append(PreparedLine(product, quantity, line_total))

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
    return PreparedOrder(
        point=point,
        delivery_date=delivery_date,
        lines=prepared,
        total=total,
        status=OrderStatus.NEEDS_REVIEW if reason else OrderStatus.NEW,
        review_reason=reason or "",
        debt=balance.debt,
        overdue_days=balance.overdue_days,
    )


def _resolve_delivery_date(producer, delivery_date, now, staff) -> datetime.date:
    dates = available_delivery_dates(producer, now)
    if staff and delivery_date is not None:
        today = now.astimezone(ZoneInfo(producer.timezone)).date()
        if delivery_date < today:
            raise DeliveryDateUnavailable("Дата доставки не может быть в прошлом.")
        if delivery_date > today + datetime.timedelta(days=MAX_HORIZON_DAYS):
            raise DeliveryDateUnavailable(
                f"Заказ можно оформить не дальше чем на {MAX_HORIZON_DAYS} дней вперёд."
            )
        return delivery_date
    if not dates:
        raise DeliveryDateUnavailable(
            "Нет доступных дат доставки. Свяжитесь с производством."
        )
    if delivery_date is None:
        return dates[0]
    if delivery_date not in available_delivery_dates(
        producer, now, limit=MAX_HORIZON_DAYS
    ):
        raise DeliveryDateUnavailable(
            f"На {_format_date(delivery_date)} заказы не принимаются. "
            f"Ближайшая дата — {_format_date(dates[0])}.",
            nearest=dates[0],
        )
    return delivery_date


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
    # Блокировка заказчика: два одновременных заказа не должны обойти порог.
    customer = (
        Customer.objects.select_for_update()
        .select_related("producer")
        .get(pk=point.customer_id)
    )
    prepared = prepare_order(
        point=point,
        user=user,
        lines=lines,
        delivery_date=delivery_date,
        now=now,
        customer=customer,
    )
    order = Order.objects.create(
        point=point,
        created_by=user,
        delivery_date=prepared.delivery_date,
        status=prepared.status,
        total=prepared.total,
        comment=comment,
        review_reason=prepared.review_reason,
        debt_at_order=prepared.debt,
        overdue_days_at_order=prepared.overdue_days,
    )
    OrderLine.objects.bulk_create(
        OrderLine(
            order=order,
            product=line.product,
            product_name=line.product.name,
            unit=line.product.unit,
            unit_price=line.product.price,
            quantity=line.quantity,
            line_total=line.line_total,
        )
        for line in prepared.lines
    )
    return order
