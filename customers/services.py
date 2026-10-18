import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from orders.models import DEBT_STATUSES, Order, ReviewReason
from payments.models import Payment

ZERO = Decimal("0")


@dataclass(frozen=True)
class Balance:
    debt: Decimal  # > 0 — заказчик должен, < 0 — предоплата
    overdue_amount: Decimal
    overdue_days: int


def get_balance(customer, today: datetime.date | None = None) -> Balance:
    """Долг заказчика: принятые заказы минус оплаты.

    Просрочка считается по FIFO: оплаты закрывают самые старые заказы, срок
    оплаты заказа — дата доставки плюс отсрочка заказчика.
    """
    today = today or datetime.date.today()
    paid = Payment.objects.filter(customer=customer).aggregate(s=Sum("amount"))["s"]
    remaining = paid or ZERO
    orders = Order.objects.filter(
        point__customer=customer, status__in=DEBT_STATUSES
    ).order_by("delivery_date", "id")

    debt = ZERO
    overdue_amount = ZERO
    overdue_days = 0
    delay = datetime.timedelta(days=customer.payment_delay_days)
    for order in orders:
        covered = min(remaining, order.total)
        remaining -= covered
        unpaid = order.total - covered
        debt += unpaid
        due = order.delivery_date + delay
        if unpaid and due < today:
            overdue_amount += unpaid
            overdue_days = max(overdue_days, (today - due).days)
    return Balance(
        debt=debt - remaining,  # остаток оплат — предоплата
        overdue_amount=overdue_amount,
        overdue_days=overdue_days,
    )


def get_limits(customer) -> tuple[Decimal, int]:
    """Порог долга и просрочки: свой у заказчика или общий у производства."""
    producer = customer.producer
    amount = customer.debt_limit_amount
    days = customer.overdue_days_limit
    return (
        producer.debt_limit_amount if amount is None else amount,
        producer.overdue_days_limit if days is None else days,
    )


def get_review_reason(customer, balance: Balance | None = None) -> str | None:
    """Причина, по которой новый заказ требует решения производства, или None."""
    if not customer.producer.debt_control_enabled:
        return None
    balance = balance or get_balance(customer)
    amount_limit, days_limit = get_limits(customer)
    if balance.debt > amount_limit:
        return ReviewReason.DEBT_AMOUNT
    if balance.overdue_days > days_limit:
        return ReviewReason.OVERDUE
    return None
