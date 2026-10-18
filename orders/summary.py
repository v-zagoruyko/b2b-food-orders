import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import DEBT_STATUSES, Order, OrderLine, OrderStatus

# В сводку попадают принятые производством заказы; «Новый» и «Требует решения»
# ждут решения и показываются отдельным предупреждением.
SUMMARY_STATUSES = DEBT_STATUSES
PENDING_STATUSES = (OrderStatus.NEW, OrderStatus.NEEDS_REVIEW)

ZERO = Decimal("0")


@dataclass
class SummaryItem:
    product_id: int
    category: str
    name: str
    unit: str
    quantity: Decimal = ZERO
    amount: Decimal = ZERO


@dataclass
class SummaryPointLine:
    product_id: int
    name: str
    unit: str
    quantity: Decimal
    amount: Decimal


@dataclass
class SummaryPoint:
    point_id: int
    name: str
    customer: str
    address: str
    order_ids: list[int] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    lines: list[SummaryPointLine] = field(default_factory=list)
    total: Decimal = ZERO


@dataclass
class DeliverySummary:
    producer: object
    delivery_date: datetime.date
    items: list[SummaryItem]
    points: list[SummaryPoint]
    orders_count: int
    total: Decimal
    pending_count: int

    @property
    def is_empty(self):
        return self.orders_count == 0


def build_summary(producer, delivery_date: datetime.date) -> DeliverySummary:
    """Сводка заказов производства на дату доставки: по позициям и по точкам."""
    orders = Order.objects.filter(
        point__customer__producer=producer,
        delivery_date=delivery_date,
        status__in=SUMMARY_STATUSES,
    ).select_related("point__customer")
    order_by_id = {order.pk: order for order in orders}
    lines = (
        OrderLine.objects.filter(order_id__in=order_by_id)
        .select_related("product__category")
        .order_by(
            "product__category__sort_order",
            "product__category__name",
            "product__sort_order",
            "product_name",
            "order_id",
        )
    )

    items: dict[tuple[int, str], SummaryItem] = {}
    points: dict[int, SummaryPoint] = {}
    for order in sorted(
        orders, key=lambda o: (o.point.customer.name, o.point.name, o.pk)
    ):
        point = order.point
        row = points.setdefault(
            point.pk,
            SummaryPoint(
                point_id=point.pk,
                name=point.name,
                customer=point.customer.name,
                address=point.address,
            ),
        )
        row.order_ids.append(order.pk)
        if order.comment:
            row.comments.append(order.comment)
        row.total += order.total

    point_lines: dict[tuple[int, int, str], SummaryPointLine] = {}
    for line in lines:
        product = line.product
        # Единица в ключе: после смены единицы у позиции строки в «шт» и «кг»
        # нельзя складывать.
        item = items.setdefault(
            (product.pk, line.unit),
            SummaryItem(
                product_id=product.pk,
                category=product.category.name,
                name=line.product_name,
                unit=line.get_unit_display(),
            ),
        )
        item.quantity += line.quantity
        item.amount += line.line_total

        point_id = order_by_id[line.order_id].point_id
        key = (point_id, product.pk, line.unit)
        entry = point_lines.get(key)
        if entry is None:
            entry = point_lines[key] = SummaryPointLine(
                product_id=product.pk,
                name=line.product_name,
                unit=line.get_unit_display(),
                quantity=ZERO,
                amount=ZERO,
            )
            points[point_id].lines.append(entry)
        entry.quantity += line.quantity
        entry.amount += line.line_total

    pending_count = Order.objects.filter(
        point__customer__producer=producer,
        delivery_date=delivery_date,
        status__in=PENDING_STATUSES,
    ).count()
    return DeliverySummary(
        producer=producer,
        delivery_date=delivery_date,
        items=list(items.values()),
        points=list(points.values()),
        orders_count=len(order_by_id),
        total=sum((order.total for order in order_by_id.values()), ZERO),
        pending_count=pending_count,
    )


def default_summary_date(producer, now=None) -> datetime.date:
    """Дата, на которую обычно смотрят сводку: сегодня плюс `Сводка на дату`."""
    now = now or timezone.now()
    today = now.astimezone(ZoneInfo(producer.timezone)).date()
    return today + datetime.timedelta(days=producer.summary_days_ahead)
