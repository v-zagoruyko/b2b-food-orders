import datetime
from decimal import Decimal

import factory
from django.contrib.auth.models import Group
from factory.django import DjangoModelFactory

from catalog.models import Category, Product
from customers.models import Customer, Point
from orders.models import Order, OrderLine
from payments.models import Payment
from producers.models import Producer
from users.models import User


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    name = factory.Sequence(lambda n: f"Менеджер {n}")
    telegram_id = factory.Sequence(lambda n: 900000000 + n)


class ProducerFactory(DjangoModelFactory):
    class Meta:
        model = Producer

    name = factory.Sequence(lambda n: f"Производство {n}")


class CustomerFactory(DjangoModelFactory):
    class Meta:
        model = Customer

    producer = factory.SubFactory(ProducerFactory)
    name = factory.Sequence(lambda n: f"Заказчик {n}")
    payment_delay_days = 14


class PointFactory(DjangoModelFactory):
    class Meta:
        model = Point

    customer = factory.SubFactory(CustomerFactory)
    name = factory.Sequence(lambda n: f"Точка {n}")
    address = "ул. Ленина, 1"


class CategoryFactory(DjangoModelFactory):
    class Meta:
        model = Category

    producer = factory.SubFactory(ProducerFactory)
    name = factory.Sequence(lambda n: f"Категория {n}")


class ProductFactory(DjangoModelFactory):
    class Meta:
        model = Product

    producer = factory.SelfAttribute("category.producer")
    category = factory.SubFactory(CategoryFactory)
    name = factory.Sequence(lambda n: f"Позиция {n}")
    price = Decimal("100.00")


class OrderFactory(DjangoModelFactory):
    class Meta:
        model = Order

    point = factory.SubFactory(PointFactory)
    delivery_date = factory.LazyFunction(datetime.date.today)


class OrderLineFactory(DjangoModelFactory):
    class Meta:
        model = OrderLine

    order = factory.SubFactory(OrderFactory)
    product = factory.SubFactory(ProductFactory)
    quantity = Decimal("1")


class PaymentFactory(DjangoModelFactory):
    class Meta:
        model = Payment

    customer = factory.SubFactory(CustomerFactory)
    amount = Decimal("1000.00")


def make_order(customer, total, delivery_date, status="CONFIRMED"):
    """Заказ на точке заказчика с одной строкой на сумму total."""
    point = customer.points.first() or PointFactory(customer=customer)
    order = OrderFactory(point=point, delivery_date=delivery_date, status=status)
    product = ProductFactory(category__producer=customer.producer)
    OrderLineFactory(
        order=order, product=product, quantity=Decimal("1"), unit_price=total
    )
    order.refresh_from_db()
    return order


def make_staff(producer, group_name, email=None):
    user = User.objects.create_staff(
        email or f"{group_name}-{producer.pk}@example.com",
        group_name,
        "pass12345",
        producer=producer,
    )
    user.groups.add(Group.objects.get(name=group_name))
    return user
