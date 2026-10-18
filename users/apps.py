from django.apps import AppConfig
from django.db.models.signals import post_migrate


class UsersConfig(AppConfig):
    name = "users"
    verbose_name = "Пользователи"

    def ready(self):
        from .groups import sync_groups

        post_migrate.connect(sync_groups, sender=self)
