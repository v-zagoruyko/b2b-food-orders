from decimal import Decimal

import pytest
from django.urls import reverse
from freezegun import freeze_time

from orders.exceptions import InvalidQuantity
from orders.models import OrderStatus
from orders.services import place_order
from orders.summary import build_summary

from .factories import (
    CustomerFactory,
    OrderFactory,
    OrderLineFactory,
    PaymentFactory,
    PointFactory,
    ProducerFactory,
    ProductFactory,
    UserFactory,
    make_staff,
)


@pytest.fixture
def tenants(db):
    return ProducerFactory(), ProducerFactory()


class TestHistoryIsScoped:
    def test_foreign_history_is_hidden(self, client, tenants):
        mine, other = tenants
        foreign = PaymentFactory(customer=CustomerFactory(producer=other))
        client.force_login(make_staff(mine, "Бухгалтер"))

        response = client.get(
            reverse("admin:payments_payment_history", args=[foreign.pk])
        )

        assert response.status_code == 404

    def test_foreign_version_page_is_hidden(self, client, tenants):
        mine, other = tenants
        foreign = PaymentFactory(customer=CustomerFactory(producer=other))
        version = foreign.history.first().history_id
        client.force_login(make_staff(mine, "Бухгалтер"))

        response = client.get(
            reverse("admin:payments_payment_simple_history", args=[foreign.pk, version])
        )

        assert response.status_code == 404

    def test_own_history_is_visible(self, client, tenants):
        mine, _ = tenants
        own = PaymentFactory(customer=CustomerFactory(producer=mine))
        client.force_login(make_staff(mine, "Бухгалтер"))

        response = client.get(reverse("admin:payments_payment_history", args=[own.pk]))

        assert response.status_code == 200

    def test_view_only_role_cannot_revert_through_history(self, client, tenants):
        mine, _ = tenants
        order = OrderFactory(
            point=PointFactory(customer=CustomerFactory(producer=mine)), comment="было"
        )
        version = order.history.first().history_id
        client.force_login(make_staff(mine, "Бухгалтер"))

        response = client.post(
            reverse("admin:orders_order_simple_history", args=[order.pk, version]),
            {
                "delivery_date": "2030-01-01",
                "comment": "стало",
                "point": order.point_id,
            },
        )

        order.refresh_from_db()
        assert response.status_code == 403
        assert order.comment == "было"


class TestSharedManager:
    @pytest.fixture
    def shared(self, tenants):
        mine, other = tenants
        point_a = PointFactory(customer=CustomerFactory(producer=mine))
        point_b = PointFactory(customer=CustomerFactory(producer=other))
        manager = UserFactory(name="Общий")
        manager.points.add(point_a, point_b)
        return manager, point_a, point_b

    def test_owner_save_keeps_links_to_foreign_points(self, client, tenants, shared):
        mine, _ = tenants
        manager, point_a, point_b = shared
        client.force_login(make_staff(mine, "Владелец"))

        response = client.post(
            reverse("admin:users_user_change", args=[manager.pk]),
            {"name": "Новое имя", "email": "", "phone": "", "points": [point_a.pk]},
        )

        manager.refresh_from_db()
        assert response.status_code == 302
        assert manager.name == "Новое имя"
        assert set(manager.points.all()) == {point_a, point_b}

    def test_owner_cannot_change_login_data_of_shared_manager(
        self, client, tenants, shared
    ):
        mine, _ = tenants
        manager, point_a, _ = shared
        manager.telegram_id = 111
        manager.save()
        client.force_login(make_staff(mine, "Владелец"))

        client.post(
            reverse("admin:users_user_change", args=[manager.pk]),
            {
                "name": manager.name,
                "email": "",
                "phone": "",
                "telegram_id": 222,
                "points": [point_a.pk],
            },
        )

        manager.refresh_from_db()
        assert manager.telegram_id == 111
        assert manager.is_active

    def test_staff_of_other_producer_is_not_reachable(self, client, tenants):
        mine, other = tenants
        point_a = PointFactory(customer=CustomerFactory(producer=mine))
        colleague = make_staff(other, "Владелец", email="b@example.com")
        colleague.points.add(point_a)
        client.force_login(make_staff(mine, "Владелец"))

        listing = client.get(reverse("admin:users_user_changelist"))
        change = client.get(reverse("admin:users_user_change", args=[colleague.pk]))

        assert colleague.pk not in {u.pk for u in listing.context["cl"].queryset}
        assert change.status_code == 302
        colleague.refresh_from_db()
        assert colleague.producer == other


class TestOrderActions:
    @pytest.fixture
    def order(self, tenants):
        mine, _ = tenants
        return OrderFactory(
            point=PointFactory(customer=CustomerFactory(producer=mine)),
            status=OrderStatus.NEEDS_REVIEW,
        )

    def act(self, client, name, order):
        return client.post(
            reverse("admin:orders_order_changelist"),
            {"action": name, "_selected_action": [order.pk]},
            follow=True,
        )

    def test_manager_confirms_and_decision_is_recorded(self, client, tenants, order):
        mine, _ = tenants
        manager = make_staff(mine, "Менеджер заказов")
        client.force_login(manager)

        self.act(client, "confirm_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.CONFIRMED
        assert order.decided_by == manager
        assert order.decided_at is not None

    def test_reject_only_from_review(self, client, tenants, order):
        mine, _ = tenants
        order.status = OrderStatus.NEW
        order.save()
        client.force_login(make_staff(mine, "Менеджер заказов"))

        self.act(client, "reject_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.NEW

    def test_cancel_requires_reason(self, client, tenants, order):
        mine, _ = tenants
        client.force_login(make_staff(mine, "Менеджер заказов"))

        self.act(client, "cancel_orders", order)
        order.refresh_from_db()
        assert order.status == OrderStatus.NEEDS_REVIEW

        order.cancel_reason = "Точка закрыта"
        order.save()
        self.act(client, "cancel_orders", order)
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED

    def test_repeated_action_does_nothing(self, client, tenants, order):
        mine, _ = tenants
        client.force_login(make_staff(mine, "Менеджер заказов"))

        self.act(client, "confirm_orders", order)
        self.act(client, "reject_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.CONFIRMED

    def test_accountant_cannot_run_actions(self, client, tenants, order):
        mine, _ = tenants
        client.force_login(make_staff(mine, "Бухгалтер"))

        self.act(client, "confirm_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.NEEDS_REVIEW

    def test_foreign_order_is_untouched(self, client, tenants, order):
        mine, other = tenants
        foreign = OrderFactory(
            point=PointFactory(customer=CustomerFactory(producer=other)),
            status=OrderStatus.NEW,
        )
        client.force_login(make_staff(mine, "Менеджер заказов"))

        self.act(client, "confirm_orders", foreign)

        foreign.refresh_from_db()
        assert foreign.status == OrderStatus.NEW

    def test_total_survives_transition(self, client, tenants, order):
        mine, _ = tenants
        product = ProductFactory(producer=mine, category__producer=mine)
        OrderLineFactory(order=order, product=product, quantity=Decimal("3"))
        client.force_login(make_staff(mine, "Менеджер заказов"))

        self.act(client, "confirm_orders", order)

        order.refresh_from_db()
        assert order.total == Decimal("300.00")


class TestQuantityLimits:
    @freeze_time("2026-10-19 09:00:00+03:00")
    @pytest.mark.parametrize(
        "quantity",
        [Decimal("NaN"), Decimal("Infinity"), Decimal("100001"), "abc", None],
    )
    def test_invalid_quantity_is_a_domain_error(self, db, quantity):
        producer = ProducerFactory()
        point = PointFactory(customer=CustomerFactory(producer=producer))
        manager = UserFactory()
        manager.points.add(point)
        product = ProductFactory(
            producer=producer, category__producer=producer, unit="KG"
        )

        with pytest.raises(InvalidQuantity):
            place_order(point=point, user=manager, lines=[(product, quantity)])

    @freeze_time("2026-10-19 09:00:00+03:00")
    def test_amount_overflow_is_a_domain_error(self, db):
        producer = ProducerFactory()
        point = PointFactory(customer=CustomerFactory(producer=producer))
        manager = UserFactory()
        manager.points.add(point)
        product = ProductFactory(
            producer=producer,
            category__producer=producer,
            unit="KG",
            price=Decimal("99999999.99"),
        )

        with pytest.raises(InvalidQuantity):
            place_order(point=point, user=manager, lines=[(product, Decimal("99999"))])


def test_changing_product_refreshes_line_snapshot(db):
    producer = ProducerFactory()
    cheap = ProductFactory(
        producer=producer,
        category__producer=producer,
        price=Decimal("10"),
        unit="PIECE",
    )
    dear = ProductFactory(
        producer=producer, category__producer=producer, price=Decimal("500"), unit="KG"
    )
    order = OrderFactory()
    line = OrderLineFactory(order=order, product=cheap, quantity=Decimal("2"))

    line.product = dear
    line.save()
    line.refresh_from_db()
    order.refresh_from_db()

    assert (line.product_name, line.unit, line.unit_price) == (
        dear.name,
        "KG",
        Decimal("500.00"),
    )
    assert order.total == Decimal("1000.00")


def test_summary_does_not_mix_units_of_one_product(db):
    producer = ProducerFactory()
    product = ProductFactory(
        producer=producer, category__producer=producer, unit="PIECE"
    )
    point = PointFactory(customer=CustomerFactory(producer=producer))
    import datetime

    day = datetime.date(2026, 10, 20)
    first = OrderFactory(point=point, delivery_date=day, status=OrderStatus.CONFIRMED)
    OrderLineFactory(order=first, product=product, quantity=Decimal("10"))
    product.unit = "KG"
    product.save()
    second = OrderFactory(point=point, delivery_date=day, status=OrderStatus.CONFIRMED)
    OrderLineFactory(order=second, product=product, quantity=Decimal("2"))

    summary = build_summary(producer, day)

    assert sorted((i.unit, i.quantity) for i in summary.items) == [
        ("кг", Decimal("2")),
        ("шт", Decimal("10")),
    ]
