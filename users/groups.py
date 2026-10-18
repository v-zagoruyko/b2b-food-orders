from django.apps import apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.management import create_contenttypes

CRUD = ("view", "add", "change", "delete")
VIEW = ("view",)

# Группы сотрудников производства: {группа: {app.model: действия}}.
# Менеджеры точек (Telegram) в группы не входят, в админку они не попадают.
GROUPS = {
    "Владелец": {
        "producers.producer": ("view", "change"),
        "customers.customer": CRUD,
        "customers.point": CRUD,
        "customers.invitation": CRUD,
        "users.user": ("view", "add", "change"),
        "catalog.category": CRUD,
        "catalog.product": CRUD,
        "orders.order": (
            "view",
            "add",
            "change",
            "decide",
            "cancel",
            "deliver",
        ),
        "orders.orderline": CRUD,
        "orders.summarydelivery": VIEW,
        "payments.payment": CRUD,
    },
    "Менеджер заказов": {
        "customers.customer": ("view", "add", "change"),
        "customers.point": ("view", "add", "change"),
        "customers.invitation": ("view", "add", "change"),
        "catalog.category": VIEW,
        "catalog.product": VIEW,
        "orders.order": (
            "view",
            "add",
            "change",
            "decide",
            "cancel",
            "deliver",
        ),
        "orders.orderline": ("view", "add", "change", "delete"),
        "orders.summarydelivery": VIEW,
    },
    "Бухгалтер": {
        "customers.customer": VIEW,
        "customers.point": VIEW,
        "orders.order": VIEW,
        "orders.orderline": VIEW,
        "payments.payment": CRUD,
    },
    "Технолог": {
        "catalog.category": CRUD,
        "catalog.product": CRUD,
    },
}


def sync_groups(**kwargs):
    """Приводит группы и их права к описанию выше (вызывается после migrate)."""
    for app_config in apps.get_app_configs():
        create_contenttypes(app_config, verbosity=0)
        create_permissions(app_config, verbosity=0)
    for name, models_perms in GROUPS.items():
        codenames = {
            (app_label, f"{action}_{model}")
            for label, actions in models_perms.items()
            for app_label, model in [label.split(".")]
            for action in actions
        }
        permissions = [
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in sorted(codenames)
        ]
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(permissions)
