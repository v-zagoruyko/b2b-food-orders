import datetime
import random
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Category, Product, Unit
from customers.models import Customer, Invitation, LegalForm, Point
from customers.services import get_balance, get_review_reason
from orders.models import Order, OrderLine, OrderStatus
from payments.models import Payment
from producers.models import Producer
from users.models import User

PASSWORD = "demo12345"

# категория -> [(название, единица, шаг, цена)]
CATALOG = {
    "Выпечка": [
        ("Круассан классический", Unit.PIECE, None, "85"),
        ("Круассан с миндалём", Unit.PIECE, None, "110"),
        ("Булочка с корицей", Unit.PIECE, None, "70"),
        ("Слойка с вишней", Unit.PIECE, None, "65"),
        ("Пирожок с картофелем", Unit.PIECE, "10", "45"),
        ("Пирожок с мясом", Unit.PIECE, "10", "60"),
        ("Ватрушка с творогом", Unit.PIECE, None, "65"),
        ("Эклер ванильный", Unit.PIECE, None, "95"),
    ],
    "Салаты": [
        ("Оливье", Unit.KG, "0.5", "520"),
        ("Цезарь с курицей", Unit.KG, "0.5", "640"),
        ("Винегрет", Unit.KG, "0.5", "380"),
        ("Греческий", Unit.KG, "0.5", "590"),
        ("Салат с крабом", Unit.KG, "0.5", "560"),
    ],
    "Горячее": [
        ("Борщ", Unit.LITER, "1", "260"),
        ("Куриный суп", Unit.LITER, "1", "240"),
        ("Котлета куриная", Unit.PIECE, None, "95"),
        ("Котлета по-киевски", Unit.PIECE, None, "140"),
        ("Гуляш говяжий", Unit.KG, "0.5", "890"),
        ("Пюре картофельное", Unit.KG, "0.5", "280"),
        ("Гречка отварная", Unit.KG, "0.5", "220"),
        ("Плов", Unit.KG, "0.5", "560"),
        ("Лазанья", Unit.PIECE, None, "210"),
    ],
    "Десерты": [
        ("Чизкейк Нью-Йорк", Unit.PIECE, None, "190"),
        ("Тирамису", Unit.PIECE, None, "210"),
        ("Медовик", Unit.PIECE, None, "150"),
        ("Панна котта", Unit.PIECE, None, "130"),
        ("Наполеон", Unit.PIECE, None, "140"),
    ],
    "Напитки": [
        ("Морс клюквенный", Unit.LITER, "1", "190"),
        ("Компот из сухофруктов", Unit.LITER, "1", "120"),
        ("Лимонад домашний", Unit.LITER, "1", "240"),
        ("Холодный чай", Unit.LITER, "1", "210"),
        ("Сок яблочный", Unit.LITER, "1", "180"),
    ],
    "Сэндвичи": [
        ("Сэндвич с курицей", Unit.PIECE, None, "160"),
        ("Сэндвич с ветчиной и сыром", Unit.PIECE, None, "150"),
        ("Ролл с лососем", Unit.PIECE, None, "240"),
        ("Бургер с говядиной", Unit.PIECE, None, "280"),
    ],
}

# (заказчик, форма, ИНН, отсрочка, свой порог, [(точка, адрес, свой минимум)],
#  доля оплаченного, масштаб заказов)
CUSTOMERS = [
    (
        "ИП Соколова А. В.",
        LegalForm.SOLE_PROPRIETOR,
        "770112345678",
        14,
        None,
        [
            ("Кофейня на Ленина", "ул. Ленина, 12", None),
            ("Кофейня на Мира", "пр. Мира, 5", None),
        ],
        "0.93",
        1,
    ),
    (
        "ООО «Кофе-Поинт»",
        LegalForm.COMPANY,
        "7701234567",
        14,
        None,
        [
            ("Кофе-Поинт Центр", "ул. Советская, 3", None),
            ("Кофе-Поинт Вокзал", "Привокзальная пл., 1", None),
            ("Кофе-Поинт Универ", "ул. Студенческая, 8", None),
        ],
        "0.55",
        2,
    ),
    (
        "ИП Кузнецов Д. С.",
        LegalForm.SOLE_PROPRIETOR,
        "770298765432",
        14,
        None,
        [("Магазин «У дома»", "ул. Гагарина, 27", None)],
        "0.85",
        1,
    ),
    (
        "ООО «Продмаг»",
        LegalForm.COMPANY,
        "7702345678",
        21,
        "300000",
        [
            ("Продмаг №1", "ул. Победы, 40", "3000"),
            ("Продмаг №2", "ул. Заводская, 11", "3000"),
        ],
        "0.9",
        1,
    ),
    (
        "ИП Лебедева Е. Н.",
        LegalForm.SOLE_PROPRIETOR,
        "770356789012",
        14,
        None,
        [("Кафе «Лебедь»", "наб. Речная, 2", None)],
        "0",
        1,
    ),
]

STAFF = [
    ("owner@vkusny.example", "Ольга Владелец", "Владелец"),
    ("orders@vkusny.example", "Игорь Менеджер", "Менеджер заказов"),
    ("accountant@vkusny.example", "Марина Бухгалтер", "Бухгалтер"),
    ("tech@vkusny.example", "Сергей Технолог", "Технолог"),
]


class Command(BaseCommand):
    help = "Заполняет базу демо-данными: два производства, заказчики, заказы, оплаты."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Удалить все данные перед заполнением"
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Команда доступна только при DEBUG=True.")
        if options["reset"]:
            self._reset()
        elif Producer.objects.exists():
            raise CommandError("В базе уже есть данные. Запустите с --reset.")

        self.rng = random.Random(42)
        self.today = datetime.date.today()
        self.telegram_seq = 100000000

        User.objects.create_superuser("admin@example.com", "Платформа", PASSWORD)
        main = self._producer_main()
        self._producer_second()

        self.stdout.write(self.style.SUCCESS("Демо-данные созданы."))
        self.stdout.write(f"Пароль у всех: {PASSWORD}")
        self.stdout.write("  admin@example.com — все производства")
        for email, _name, group in STAFF:
            self.stdout.write(f"  {email} — {group}")
        self.stdout.write("  owner@sibiryachka.example — Владелец второго производства")
        self.stdout.write(f"Производство: {main}")

    def _reset(self):
        for model in (
            OrderLine,
            Order,
            Payment,
            User,
            Point,
            Customer,
            Product,
            Category,
            Producer,
        ):
            model.objects.all().delete()

    def _telegram_id(self):
        self.telegram_seq += 1
        return self.telegram_seq

    def _create_staff(self, producer, email, name, group):
        user = User.objects.create_staff(
            email, name, PASSWORD, producer=producer, phone="+7 900 000-00-00"
        )
        user.groups.add(Group.objects.get(name=group))
        return user

    def _producer_main(self):
        producer = Producer.objects.create(
            name="Кухня «Вкусный день»",
            order_cutoff_time=datetime.time(14, 0),
            lead_days=1,
            delivery_weekdays=[0, 1, 2, 3, 4, 5],
            min_order_amount=Decimal("2000"),
            debt_limit_amount=Decimal("100000"),
            overdue_days_limit=14,
            onboarding_done=True,
            summary_emails=["kitchen@vkusny.example"],
            summary_send_time=datetime.time(14, 5),
        )
        for email, name, group in STAFF:
            self._create_staff(producer, email, name, group)

        products = []
        for sort, (category_name, items) in enumerate(CATALOG.items()):
            category = Category.objects.create(
                producer=producer, name=category_name, sort_order=sort
            )
            for order, (name, unit, step, price) in enumerate(items):
                products.append(
                    Product.objects.create(
                        producer=producer,
                        category=category,
                        name=name,
                        unit=unit,
                        order_step=Decimal(step) if step else None,
                        price=Decimal(price),
                        sort_order=order,
                    )
                )

        for spec in CUSTOMERS:
            self._customer(producer, products, *spec)
        self._review_order(producer, products)
        Invitation.objects.create(
            point=Point.objects.get(customer__producer=producer, name="Продмаг №2")
        )
        return producer

    def _customer(
        self,
        producer,
        products,
        name,
        form,
        inn,
        delay,
        limit,
        points,
        paid_share,
        scale,
    ):
        customer = Customer.objects.create(
            producer=producer,
            name=name,
            legal_form=form,
            inn=inn,
            payment_delay_days=delay,
            debt_limit_amount=Decimal(limit) if limit else None,
        )
        orders = []
        for point_name, address, min_order in points:
            point = Point.objects.create(
                customer=customer,
                name=point_name,
                address=address,
                contact_name="Менеджер точки",
                contact_phone="+7 900 111-22-33",
                min_order_amount=Decimal(min_order) if min_order else None,
            )
            manager = User.objects.create_user(
                f"Менеджер: {point_name}",
                telegram_id=self._telegram_id(),
                phone="+7 900 111-22-33",
            )
            manager.points.add(point)
            if name != "ИП Лебедева Е. Н.":
                orders += self._history(point, manager, products, scale)

        paid_total = sum(o.total for o in orders) * Decimal(paid_share)
        self._payments(customer, paid_total, orders)

    def _lines(self, order, products, scale):
        count = self.rng.randint(4, 9)
        for product in self.rng.sample(products, count):
            if product.order_step:
                quantity = product.order_step * self.rng.randint(2, 6)
            else:
                quantity = Decimal(self.rng.randint(5, 20))
            quantity *= scale
            OrderLine.objects.create(order=order, product=product, quantity=quantity)

    def _history(self, point, manager, products, scale):
        orders = []
        for days_ago in range(42, 0, -1):
            day = self.today - datetime.timedelta(days=days_ago)
            if day.weekday() == 6 or self.rng.random() > 0.45:
                continue
            status = OrderStatus.DELIVERED
            roll = self.rng.random()
            if roll < 0.03:
                status = OrderStatus.CANCELED
            order = Order.objects.create(
                point=point,
                created_by=manager,
                delivery_date=day,
                status=status,
                cancel_reason="Точка закрыта на выходной"
                if status == OrderStatus.CANCELED
                else "",
            )
            self._lines(order, products, scale)
            order.refresh_from_db()
            if status == OrderStatus.DELIVERED:
                orders.append(order)

        for status, delta in ((OrderStatus.CONFIRMED, 1), (OrderStatus.NEW, 2)):
            day = self.today + datetime.timedelta(days=delta)
            if day.weekday() == 6:
                day += datetime.timedelta(days=1)
            order = Order.objects.create(
                point=point,
                created_by=manager,
                delivery_date=day,
                status=status,
                comment="Привезти до 8:00" if status == OrderStatus.NEW else "",
            )
            self._lines(order, products, scale)
        return orders

    def _payments(self, customer, total, orders):
        if total <= 0:
            return
        orders = sorted(orders, key=lambda o: o.delivery_date)
        chunks = [Decimal("0.4"), Decimal("0.35"), Decimal("0.25")]
        for index, share in enumerate(chunks):
            idx = min(len(orders) - 1, int(len(orders) * (index + 1) / 3) - 1)
            paid_at = orders[idx].delivery_date + datetime.timedelta(days=5)
            Payment.objects.create(
                customer=customer,
                amount=(total * share).quantize(Decimal("0.01")),
                paid_at=min(paid_at, self.today),
                document_number=f"п/п {self.rng.randint(100, 999)}",
                comment="Оплата по счёту",
            )

    def _review_order(self, producer, products):
        """Заказ Кофе-Поинта, у которого долг выше порога, ждёт решения."""
        customer = Customer.objects.get(producer=producer, name="ООО «Кофе-Поинт»")
        point = customer.points.first()
        manager = point.managers.first()
        balance = get_balance(customer)
        reason = get_review_reason(customer, balance)
        order = Order.objects.create(
            point=point,
            created_by=manager,
            delivery_date=self.today + datetime.timedelta(days=1),
            status=OrderStatus.NEEDS_REVIEW if reason else OrderStatus.NEW,
            review_reason=reason or "",
            debt_at_order=balance.debt,
            overdue_days_at_order=balance.overdue_days,
        )
        self._lines(order, products, 2)

    def _producer_second(self):
        producer = Producer.objects.create(
            name="Пельменная «Сибирячка»", min_order_amount=Decimal("1500")
        )
        self._create_staff(
            producer, "owner@sibiryachka.example", "Пётр Сибиряков", "Владелец"
        )
        category = Category.objects.create(producer=producer, name="Пельмени")
        products = [
            Product.objects.create(
                producer=producer,
                category=category,
                name=name,
                unit=Unit.KG,
                order_step=Decimal("0.5"),
                price=Decimal(price),
                sort_order=i,
            )
            for i, (name, price) in enumerate(
                [("Классические", "480"), ("С говядиной", "560"), ("С курицей", "430")]
            )
        ]
        customer = Customer.objects.create(
            producer=producer, name="ИП Орлов К. Л.", inn="770411223344"
        )
        point = Point.objects.create(
            customer=customer, name="Магазин «Орлов»", address="ул. Лесная, 9"
        )
        manager = User.objects.create_user(
            "Менеджер: Магазин «Орлов»",
            telegram_id=self._telegram_id(),
        )
        manager.points.add(point)
        order = Order.objects.create(
            point=point,
            created_by=manager,
            delivery_date=self.today + datetime.timedelta(days=1),
            status=OrderStatus.CONFIRMED,
        )
        for product in products:
            OrderLine.objects.create(
                order=order, product=product, quantity=Decimal("3")
            )
