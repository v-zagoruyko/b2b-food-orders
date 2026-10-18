import datetime
from decimal import Decimal

import pytest

from customers.services import get_balance, get_review_reason
from orders.models import OrderStatus, ReviewReason

from .factories import CustomerFactory, PaymentFactory, make_order

TODAY = datetime.date(2026, 10, 18)


def days_ago(n):
    return TODAY - datetime.timedelta(days=n)


@pytest.mark.django_db
def test_debt_counts_only_confirmed_and_delivered():
    customer = CustomerFactory()
    make_order(customer, Decimal("1000"), days_ago(1), OrderStatus.CONFIRMED)
    make_order(customer, Decimal("2000"), days_ago(1), OrderStatus.DELIVERED)
    for status in (OrderStatus.NEW, OrderStatus.NEEDS_REVIEW, OrderStatus.CANCELED):
        make_order(customer, Decimal("5000"), days_ago(1), status)

    assert get_balance(customer, TODAY).debt == Decimal("3000")


@pytest.mark.django_db
def test_payments_close_oldest_orders_first():
    customer = CustomerFactory(payment_delay_days=14)
    make_order(customer, Decimal("1000"), days_ago(30))  # срок вышел 16 дней назад
    make_order(customer, Decimal("1000"), days_ago(20))  # срок вышел 6 дней назад
    make_order(customer, Decimal("1000"), days_ago(2))  # срок не вышел
    PaymentFactory(customer=customer, amount=Decimal("1500"))

    balance = get_balance(customer, TODAY)

    assert balance.debt == Decimal("1500")
    assert balance.overdue_amount == Decimal("500")
    assert balance.overdue_days == 6


@pytest.mark.django_db
def test_overpayment_is_negative_debt():
    customer = CustomerFactory()
    make_order(customer, Decimal("1000"), days_ago(1))
    PaymentFactory(customer=customer, amount=Decimal("1500"))

    assert get_balance(customer, TODAY).debt == Decimal("-500")


@pytest.mark.django_db
def test_new_customer_without_debt_passes():
    assert get_review_reason(CustomerFactory()) is None


@pytest.mark.django_db
def test_review_reason_by_amount_and_own_limit_overrides_producer():
    customer = CustomerFactory(producer__debt_limit_amount=Decimal("1000"))
    make_order(customer, Decimal("1500"), days_ago(1))
    assert get_review_reason(customer) == ReviewReason.DEBT_AMOUNT

    customer.debt_limit_amount = Decimal("2000")
    assert get_review_reason(customer) is None


@pytest.mark.django_db
def test_review_reason_by_overdue():
    customer = CustomerFactory(
        producer__debt_limit_amount=Decimal("1000000"),
        producer__overdue_days_limit=10,
        payment_delay_days=14,
    )
    make_order(customer, Decimal("100"), datetime.date.today() - datetime.timedelta(40))

    assert get_review_reason(customer) == ReviewReason.OVERDUE


@pytest.mark.django_db
def test_debt_control_can_be_disabled():
    customer = CustomerFactory(
        producer__debt_control_enabled=False,
        producer__debt_limit_amount=Decimal("1"),
    )
    make_order(customer, Decimal("1500"), days_ago(1))

    assert get_review_reason(customer) is None
