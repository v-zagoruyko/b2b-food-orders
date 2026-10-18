import pytest
from django.urls import reverse

from .factories import (
    CustomerFactory,
    OrderFactory,
    PaymentFactory,
    PointFactory,
    ProducerFactory,
    ProductFactory,
    make_staff,
)


@pytest.fixture
def two_tenants(db):
    mine, other = ProducerFactory(), ProducerFactory()
    return mine, other


@pytest.mark.parametrize(
    ("changelist", "factory"),
    [
        ("customers_customer_changelist", lambda p: CustomerFactory(producer=p)),
        (
            "customers_point_changelist",
            lambda p: PointFactory(customer=CustomerFactory(producer=p)),
        ),
        (
            "catalog_product_changelist",
            lambda p: ProductFactory(producer=p, category__producer=p),
        ),
        (
            "orders_order_changelist",
            lambda p: OrderFactory(
                point=PointFactory(customer=CustomerFactory(producer=p))
            ),
        ),
        (
            "payments_payment_changelist",
            lambda p: PaymentFactory(customer=CustomerFactory(producer=p)),
        ),
    ],
)
def test_staff_sees_only_own_producer(client, two_tenants, changelist, factory):
    mine, other = two_tenants
    own_obj, foreign_obj = factory(mine), factory(other)
    client.force_login(make_staff(mine, "Владелец"))

    response = client.get(reverse(f"admin:{changelist}"))

    pks = {obj.pk for obj in response.context["cl"].queryset}
    assert own_obj.pk in pks
    assert foreign_obj.pk not in pks


def test_staff_cannot_open_foreign_object(client, two_tenants):
    mine, other = two_tenants
    foreign = CustomerFactory(producer=other)
    client.force_login(make_staff(mine, "Владелец"))

    response = client.get(reverse("admin:customers_customer_change", args=[foreign.pk]))

    assert response.status_code == 302  # админка редиректит на список


def test_new_object_gets_producer_of_staff(client, two_tenants):
    mine, other = two_tenants
    client.force_login(make_staff(mine, "Технолог"))

    response = client.post(
        reverse("admin:catalog_category_add"),
        {"producer": other.pk, "name": "Салаты", "sort_order": 0, "is_active": "on"},
    )

    assert response.status_code == 302
    from catalog.models import Category

    assert Category.objects.get(name="Салаты").producer == mine


def test_technologist_has_no_access_to_orders(client, two_tenants):
    mine, _ = two_tenants
    client.force_login(make_staff(mine, "Технолог"))

    assert client.get(reverse("admin:orders_order_changelist")).status_code == 403


def test_staff_without_producer_sees_nothing(client, db):
    from users.models import User

    user = User.objects.create_staff("x@example.com", "X", "pass12345")
    CustomerFactory()
    client.force_login(user)

    response = client.get(reverse("admin:customers_customer_changelist"))

    assert response.status_code == 403  # нет прав
