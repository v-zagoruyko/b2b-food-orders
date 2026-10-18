from decimal import Decimal

import pytest
from viewflow.fsm import TransitionNotAllowed

from orders.models import OrderStatus

from .factories import OrderFactory, OrderLineFactory, ProductFactory


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("source", "method", "target"),
    [
        (OrderStatus.NEW, "confirm", OrderStatus.CONFIRMED),
        (OrderStatus.NEEDS_REVIEW, "confirm", OrderStatus.CONFIRMED),
        (OrderStatus.NEEDS_REVIEW, "reject", OrderStatus.REJECTED),
        (OrderStatus.CONFIRMED, "deliver", OrderStatus.DELIVERED),
        (OrderStatus.NEW, "cancel", OrderStatus.CANCELED),
        (OrderStatus.NEEDS_REVIEW, "cancel", OrderStatus.CANCELED),
        (OrderStatus.CONFIRMED, "cancel", OrderStatus.CANCELED),
    ],
)
def test_allowed_transitions(source, method, target):
    order = OrderFactory(status=source)

    getattr(order, method)()

    assert order.status == target


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("source", "method"),
    [
        (OrderStatus.NEW, "reject"),
        (OrderStatus.NEW, "deliver"),
        (OrderStatus.NEEDS_REVIEW, "deliver"),
        (OrderStatus.DELIVERED, "cancel"),
        (OrderStatus.REJECTED, "confirm"),
        (OrderStatus.CANCELED, "confirm"),
    ],
)
def test_forbidden_transitions(source, method):
    order = OrderFactory(status=source)

    with pytest.raises(TransitionNotAllowed):
        getattr(order, method)()


@pytest.mark.django_db
def test_line_fixes_price_and_total_survives_price_change():
    product = ProductFactory(price=Decimal("100.00"))
    order = OrderFactory()
    OrderLineFactory(order=order, product=product, quantity=Decimal("2.5"))

    product.price = Decimal("999.00")
    product.save()
    order.refresh_from_db()

    assert order.total == Decimal("250.00")
    assert order.lines.get().unit_price == Decimal("100.00")
