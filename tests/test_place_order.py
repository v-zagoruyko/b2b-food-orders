import datetime
from decimal import Decimal

import pytest
from freezegun import freeze_time

from orders.exceptions import (
    BelowMinimumOrder,
    DeliveryDateUnavailable,
    EmptyOrder,
    InvalidQuantity,
    OrderingUnavailable,
    PointAccessDenied,
    ProductUnavailable,
)
from orders.models import Order, OrderStatus, ReviewReason
from orders.services import available_delivery_dates, place_order

from .factories import (
    CustomerFactory,
    PaymentFactory,
    PointFactory,
    ProducerFactory,
    ProductFactory,
    UserFactory,
    make_order,
)

# 2026-10-19 — понедельник. Настройки по умолчанию: приём до 14:00 МСК,
# доставка на следующий день, пн-пт.
MON_MORNING = "2026-10-19 09:00:00+03:00"
MON_EVENING = "2026-10-19 18:00:00+03:00"
FRI_EVENING = "2026-10-23 18:00:00+03:00"
D = datetime.date


@pytest.fixture
def producer(db):
    return ProducerFactory(min_order_amount=Decimal("0"))


@pytest.fixture
def point(producer):
    return PointFactory(customer=CustomerFactory(producer=producer))


@pytest.fixture
def manager(point):
    user = UserFactory()
    user.points.add(point)
    return user


def product(producer, **kwargs):
    kwargs.setdefault("price", Decimal("100.00"))
    return ProductFactory(producer=producer, category__producer=producer, **kwargs)


class TestDeliveryDates:
    @freeze_time(MON_MORNING)
    def test_before_cutoff_delivers_next_day(self, producer):
        assert available_delivery_dates(producer)[0] == D(2026, 10, 20)

    @freeze_time(MON_EVENING)
    def test_after_cutoff_moves_to_day_after(self, producer):
        assert available_delivery_dates(producer)[0] == D(2026, 10, 21)

    @freeze_time(FRI_EVENING)
    def test_skips_non_delivery_weekdays(self, producer):
        # сб и вс не возят; заказ в пятницу вечером уходит на понедельник
        assert available_delivery_dates(producer)[0] == D(2026, 10, 26)

    @freeze_time("2026-10-24 10:00:00+03:00")
    def test_saturday_morning_gets_monday(self, producer):
        # дедлайн понедельника — воскресенье 14:00, он ещё не прошёл
        assert available_delivery_dates(producer)[0] == D(2026, 10, 26)

    @freeze_time("2026-10-19 13:30:00+00:00")
    def test_cutoff_is_in_producer_timezone(self, db):
        yekb = ProducerFactory(timezone="Asia/Yekaterinburg")  # 18:30 местных
        assert available_delivery_dates(yekb)[0] == D(2026, 10, 21)

    @freeze_time(MON_MORNING)
    def test_same_day_delivery_with_zero_lead(self, db):
        same_day = ProducerFactory(lead_days=0)
        assert available_delivery_dates(same_day)[0] == D(2026, 10, 19)


class TestPlaceOrder:
    @freeze_time(MON_MORNING)
    def test_creates_new_order_with_price_snapshot(self, producer, point, manager):
        item = product(producer, price=Decimal("120.50"))

        order = place_order(
            point=point, user=manager, lines=[(item, Decimal("3"))], comment="до 8:00"
        )

        item.price = Decimal("999")
        item.save()
        order.refresh_from_db()
        line = order.lines.get()
        assert order.status == OrderStatus.NEW
        assert order.delivery_date == D(2026, 10, 20)
        assert order.created_by == manager
        assert order.comment == "до 8:00"
        assert order.total == Decimal("361.50")
        assert (line.product_name, line.unit_price) == (item.name, Decimal("120.50"))

    @freeze_time(MON_MORNING)
    def test_accepts_product_ids_and_merges_duplicates(self, producer, point, manager):
        item = product(producer)

        order = place_order(
            point=point,
            user=manager,
            lines=[(item.pk, Decimal("1")), (item.pk, Decimal("2"))],
        )

        assert order.lines.get().quantity == Decimal("3")
        assert order.total == Decimal("300.00")

    @freeze_time(MON_EVENING)
    def test_late_explicit_date_is_rejected_with_nearest(
        self, producer, point, manager
    ):
        item = product(producer)

        with pytest.raises(DeliveryDateUnavailable) as error:
            place_order(
                point=point,
                user=manager,
                lines=[(item, Decimal("1"))],
                delivery_date=D(2026, 10, 20),
            )

        assert error.value.nearest == D(2026, 10, 21)
        assert not Order.objects.exists()

    @freeze_time(MON_MORNING)
    def test_non_delivery_weekday_is_rejected(self, producer, point, manager):
        item = product(producer)

        with pytest.raises(DeliveryDateUnavailable):
            place_order(
                point=point,
                user=manager,
                lines=[(item, Decimal("1"))],
                delivery_date=D(2026, 10, 24),  # суббота
            )

    @freeze_time(MON_MORNING)
    def test_explicit_future_date_is_accepted(self, producer, point, manager):
        item = product(producer)

        order = place_order(
            point=point,
            user=manager,
            lines=[(item, Decimal("1"))],
            delivery_date=D(2026, 10, 22),
        )

        assert order.delivery_date == D(2026, 10, 22)

    @freeze_time(MON_MORNING)
    def test_below_producer_minimum(self, producer, point, manager):
        producer.min_order_amount = Decimal("500")
        producer.save()
        item = product(producer)

        with pytest.raises(BelowMinimumOrder) as error:
            place_order(point=point, user=manager, lines=[(item, Decimal("4"))])

        assert error.value.minimum == Decimal("500")

    @freeze_time(MON_MORNING)
    def test_point_minimum_overrides_producer(self, producer, point, manager):
        producer.min_order_amount = Decimal("500")
        producer.save()
        point.min_order_amount = Decimal("100")
        point.save()
        item = product(producer)

        order = place_order(point=point, user=manager, lines=[(item, Decimal("1"))])

        assert order.total == Decimal("100.00")

    @freeze_time(MON_MORNING)
    def test_debt_over_limit_goes_to_review_but_is_accepted(
        self, producer, point, manager
    ):
        producer.debt_limit_amount = Decimal("1000")
        producer.save()
        make_order(point.customer, Decimal("5000"), D(2026, 10, 15))
        item = product(producer)

        order = place_order(point=point, user=manager, lines=[(item, Decimal("1"))])

        assert order.status == OrderStatus.NEEDS_REVIEW
        assert order.review_reason == ReviewReason.DEBT_AMOUNT
        assert order.debt_at_order == Decimal("5000")

    @freeze_time(MON_MORNING)
    def test_payment_brings_customer_under_limit(self, producer, point, manager):
        producer.debt_limit_amount = Decimal("1000")
        producer.save()
        make_order(point.customer, Decimal("5000"), D(2026, 10, 15))
        PaymentFactory(customer=point.customer, amount=Decimal("4500"))
        item = product(producer)

        order = place_order(point=point, user=manager, lines=[(item, Decimal("1"))])

        assert order.status == OrderStatus.NEW
        assert order.review_reason == ""

    @freeze_time(MON_MORNING)
    def test_new_customer_without_debt_is_not_flagged(self, producer, point, manager):
        item = product(producer)

        order = place_order(point=point, user=manager, lines=[(item, Decimal("1"))])

        assert order.status == OrderStatus.NEW
        assert order.debt_at_order == Decimal("0")

    @freeze_time(MON_MORNING)
    @pytest.mark.parametrize(
        ("kwargs", "quantity"),
        [
            ({"order_step": Decimal("0.5")}, Decimal("1.2")),
            ({"order_step": Decimal("10")}, Decimal("15")),
            ({"unit": "PIECE"}, Decimal("1.5")),
            ({}, Decimal("0")),
            ({}, Decimal("-1")),
        ],
    )
    def test_invalid_quantity(self, producer, point, manager, kwargs, quantity):
        item = product(producer, **kwargs)

        with pytest.raises(InvalidQuantity):
            place_order(point=point, user=manager, lines=[(item, quantity)])

    @freeze_time(MON_MORNING)
    def test_fractional_quantity_for_weighed_product(self, producer, point, manager):
        item = product(producer, unit="KG", order_step=Decimal("0.5"))

        order = place_order(point=point, user=manager, lines=[(item, Decimal("1.5"))])

        assert order.total == Decimal("150.00")

    @freeze_time(MON_MORNING)
    def test_inactive_or_foreign_product(self, producer, point, manager):
        inactive = product(producer, is_active=False)
        foreign = ProductFactory()

        for item in (inactive, foreign):
            with pytest.raises(ProductUnavailable):
                place_order(point=point, user=manager, lines=[(item, Decimal("1"))])

    @freeze_time(MON_MORNING)
    def test_empty_order(self, point, manager):
        with pytest.raises(EmptyOrder):
            place_order(point=point, user=manager, lines=[])

    @freeze_time(MON_MORNING)
    def test_user_must_manage_the_point(self, producer, point):
        stranger = UserFactory()
        item = product(producer)

        with pytest.raises(PointAccessDenied):
            place_order(point=point, user=stranger, lines=[(item, Decimal("1"))])

    @freeze_time(MON_MORNING)
    @pytest.mark.parametrize("target", ["point", "customer", "producer"])
    def test_inactive_party_cannot_order(self, producer, point, manager, target):
        entity = {
            "point": point,
            "customer": point.customer,
            "producer": producer,
        }[target]
        entity.is_active = False
        entity.save()
        item = product(producer)

        with pytest.raises(OrderingUnavailable):
            place_order(point=point, user=manager, lines=[(item, Decimal("1"))])
