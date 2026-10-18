import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from customers.models import Invitation
from users.models import User

from .factories import PointFactory, ProducerFactory, make_staff


@pytest.mark.django_db
def test_invitation_gets_unique_token_and_is_valid_for_a_week():
    point = PointFactory()
    first, second = (
        Invitation.objects.create(point=point),
        Invitation.objects.create(point=point),
    )

    assert first.token != second.token
    assert first.is_valid
    assert first.expires_at > timezone.now() + datetime.timedelta(days=6)


@pytest.mark.django_db
def test_used_or_expired_invitation_is_not_valid():
    point = PointFactory()
    used = Invitation.objects.create(point=point, used_at=timezone.now())
    expired = Invitation.objects.create(
        point=point, expires_at=timezone.now() - datetime.timedelta(minutes=1)
    )

    assert not used.is_valid
    assert not expired.is_valid


@pytest.mark.django_db
def test_link_uses_bot_username(settings):
    invitation = Invitation.objects.create(point=PointFactory())
    assert invitation.link == ""

    settings.TELEGRAM_BOT_USERNAME = "orders_bot"

    assert invitation.link == f"https://t.me/orders_bot?startapp={invitation.token}"


def test_owner_creating_manager_leaves_producer_empty(client, db):
    producer = ProducerFactory()
    client.force_login(make_staff(producer, "Владелец"))

    response = client.post(
        reverse("admin:users_user_add"),
        {
            "name": "Менеджер",
            "email": "",
            "usable_password": "false",
            "producer": producer.pk,
        },
    )

    assert response.status_code == 302
    assert User.objects.get(name="Менеджер").producer is None
