import pytest
from django.contrib.auth.models import Group


@pytest.mark.django_db
def test_groups_are_synced_after_migrate():
    names = set(Group.objects.values_list("name", flat=True))
    assert {"Владелец", "Менеджер заказов", "Бухгалтер", "Технолог"} <= names


@pytest.mark.django_db
def test_accountant_cannot_decide_orders_but_manager_can():
    accountant = Group.objects.get(name="Бухгалтер").permissions
    manager = Group.objects.get(name="Менеджер заказов").permissions

    assert not accountant.filter(codename="decide_order").exists()
    assert accountant.filter(codename="add_payment").exists()
    assert manager.filter(codename="decide_order").exists()
    assert not manager.filter(codename="add_payment").exists()
