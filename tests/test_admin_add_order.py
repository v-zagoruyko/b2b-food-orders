import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from freezegun import freeze_time

from orders.models import Order, OrderStatus, ReviewReason
from orders.services import prepare_order

from .factories import (
    CustomerFactory,
    PointFactory,
    ProducerFactory,
    ProductFactory,
    UserFactory,
    make_order,
    make_staff,
)

# Понедельник утром, приём до 14:00: ближайшая дата доставки — вторник.
MON_MORNING = "2026-10-19 09:00:00+03:00"
MON_EVENING = "2026-10-19 18:00:00+03:00"
TUESDAY = datetime.date(2026, 10, 20)


@pytest.fixture
def producer(db):
    return ProducerFactory(min_order_amount=Decimal("500"))


@pytest.fixture
def point(producer):
    return PointFactory(customer=CustomerFactory(producer=producer))


@pytest.fixture
def product(producer):
    return ProductFactory(
        producer=producer, category__producer=producer, price=Decimal("100.00")
    )


@pytest.fixture
def staff(producer):
    return make_staff(producer, "Менеджер заказов")


def post_order(client, point, rows, delivery_date="", comment=""):
    data = {
        "point": point.pk,
        "delivery_date": delivery_date,
        "comment": comment,
        "lines-TOTAL_FORMS": len(rows),
        "lines-INITIAL_FORMS": 0,
        "lines-MIN_NUM_FORMS": 0,
        "lines-MAX_NUM_FORMS": 1000,
    }
    for index, (product, quantity) in enumerate(rows):
        data[f"lines-{index}-product"] = product.pk
        data[f"lines-{index}-quantity"] = quantity
    return client.post(reverse("admin:orders_order_add"), data)


def errors(response):
    return " ".join(
        str(e)
        for formset in response.context["inline_admin_formsets"]
        for e in formset.formset.non_form_errors()
    )


@freeze_time(MON_MORNING)
def test_creates_order_with_nearest_date_and_snapshots(client, staff, point, product):
    client.force_login(staff)

    response = post_order(client, point, [(product, "6")], comment="по телефону")

    order = Order.objects.get()
    line = order.lines.get()
    assert response.status_code == 302
    assert (order.status, order.delivery_date) == (OrderStatus.NEW, TUESDAY)
    assert order.created_by == staff
    assert order.total == Decimal("600.00")
    assert (line.product_name, line.unit_price) == (product.name, Decimal("100.00"))


@freeze_time(MON_EVENING)
def test_staff_can_accept_late_order_for_tomorrow(client, staff, point, product):
    client.force_login(staff)

    response = post_order(client, point, [(product, "6")], delivery_date="2026-10-20")

    assert response.status_code == 302
    assert Order.objects.get().delivery_date == TUESDAY


@freeze_time(MON_MORNING)
def test_past_date_is_rejected(client, staff, point, product):
    client.force_login(staff)

    response = post_order(client, point, [(product, "6")], delivery_date="2026-10-18")

    assert response.status_code == 200
    assert "в прошлом" in errors(response)
    assert not Order.objects.exists()


@freeze_time(MON_MORNING)
def test_below_minimum_is_rejected(client, staff, point, product):
    client.force_login(staff)

    response = post_order(client, point, [(product, "2")], comment="x")

    assert response.status_code == 200
    assert "Минимальная сумма заказа" in errors(response)
    assert not Order.objects.exists()


@freeze_time(MON_MORNING)
def test_quantity_step_is_checked(client, staff, point, producer):
    item = ProductFactory(
        producer=producer,
        category__producer=producer,
        unit="KG",
        order_step=Decimal("0.5"),
        price=Decimal("1000"),
    )
    client.force_login(staff)

    response = post_order(client, point, [(item, "1.2")])

    assert response.status_code == 200
    assert "кратно" in errors(response)
    assert not Order.objects.exists()


@freeze_time(MON_MORNING)
def test_empty_order_is_rejected(client, staff, point):
    client.force_login(staff)

    response = post_order(client, point, [])

    assert response.status_code == 200
    assert "хотя бы одну позицию" in errors(response)


@freeze_time(MON_MORNING)
def test_debt_over_limit_marks_order_for_review(client, staff, point, product):
    point.customer.producer.debt_limit_amount = Decimal("1000")
    point.customer.producer.save()
    make_order(point.customer, Decimal("5000"), datetime.date(2026, 10, 15))
    client.force_login(staff)

    response = post_order(client, point, [(product, "6")])

    order = Order.objects.get(status=OrderStatus.NEEDS_REVIEW)
    assert response.status_code == 302
    assert order.review_reason == ReviewReason.DEBT_AMOUNT
    assert order.debt_at_order == Decimal("5000")


@freeze_time(MON_MORNING)
def test_cannot_add_order_for_foreign_point(client, staff, product):
    foreign_point = PointFactory()
    client.force_login(staff)

    response = post_order(client, foreign_point, [(product, "6")])

    assert response.status_code == 200
    assert not Order.objects.exists()


@freeze_time(MON_MORNING)
def test_inactive_point_is_rejected(client, staff, point, product):
    point.is_active = False
    point.save()
    client.force_login(staff)

    response = post_order(client, point, [(product, "6")])

    assert response.status_code == 200
    assert not Order.objects.exists()


@freeze_time(MON_MORNING)
def test_prepare_order_writes_nothing(point, product):
    prepared = prepare_order(
        point=point, user=None, lines=[(product, Decimal("6"))], staff=True
    )

    assert prepared.total == Decimal("600.00")
    assert not Order.objects.exists()


@freeze_time(MON_MORNING)
def test_staff_flag_skips_point_manager_check_only_when_set(point, product):
    from orders.exceptions import PointAccessDenied

    stranger = UserFactory()

    with pytest.raises(PointAccessDenied):
        prepare_order(point=point, user=stranger, lines=[(product, Decimal("6"))])
    assert prepare_order(
        point=point, user=stranger, lines=[(product, Decimal("6"))], staff=True
    )
