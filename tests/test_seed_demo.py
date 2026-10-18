import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from producers.models import Producer
from users.models import User

PASSWORD = "stand-password-1"


@pytest.fixture
def stand(settings):
    settings.DEBUG = False
    settings.ALLOW_DEMO_SEED = True
    settings.DEMO_PASSWORD = PASSWORD


def test_refuses_without_flag(db, settings):
    settings.DEBUG = False
    settings.ALLOW_DEMO_SEED = False

    with pytest.raises(CommandError):
        call_command("seed_demo")


def test_requires_password_outside_debug(db, settings):
    settings.DEBUG = False
    settings.ALLOW_DEMO_SEED = True
    settings.DEMO_PASSWORD = ""

    with pytest.raises(CommandError):
        call_command("seed_demo")


def test_stand_seed_uses_env_password_and_is_idempotent_with_if_empty(db, stand):
    call_command("seed_demo")

    owner = User.objects.get(email="owner@vkusny.example")
    assert owner.check_password(PASSWORD)
    assert not User.objects.filter(is_superuser=True).exists()
    assert Producer.objects.count() == 2

    call_command("seed_demo", "--if-empty")

    assert Producer.objects.count() == 2


def test_reset_keeps_real_superuser(db, stand):
    real = User.objects.create_superuser("me@example.com", "Я", "real-pass-123")
    call_command("seed_demo")

    call_command("seed_demo", "--reset")

    assert User.objects.filter(pk=real.pk).exists()
    assert Producer.objects.count() == 2
