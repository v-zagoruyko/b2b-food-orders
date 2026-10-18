from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Сотрудник производства (email + пароль) или менеджер точки (Telegram)."""

    email = models.EmailField(
        unique=True,
        null=True,
        blank=True,
        verbose_name="Email",
        help_text="Нужен только сотрудникам производства: по нему входят в админку.",
    )
    telegram_id = models.BigIntegerField(
        unique=True,
        null=True,
        blank=True,
        verbose_name="Telegram ID",
        help_text="Заполняется автоматически, когда менеджер откроет ссылку-приглашение.",
    )
    name = models.CharField(max_length=200, verbose_name="Имя")
    phone = models.CharField(max_length=32, blank=True, verbose_name="Телефон")
    producer = models.ForeignKey(
        "producers.Producer",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staff",
        verbose_name="Производство",
        help_text=(
            "Только для сотрудников производства: определяет, чьи данные они видят. "
            "Менеджер точки производство не указывает, оно определяется по его точкам."
        ),
    )
    points = models.ManyToManyField(
        "customers.Point",
        blank=True,
        related_name="managers",
        verbose_name="Точки",
        help_text="Для менеджеров точек: только эти точки они видят в приложении.",
    )
    is_staff = models.BooleanField(
        default=False,
        verbose_name="Сотрудник производства",
        help_text="Разрешает вход в админку. Права задаются группами.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен",
        help_text="Снимите галочку вместо удаления: история заказов сохранится.",
    )
    date_joined = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    objects = UserManager()
    history = HistoricalRecords(excluded_fields=["password", "last_login"])

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["name"]
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.is_staff and not self.email:
            raise ValidationError({"email": "Сотруднику нужен email для входа."})
        if self.is_staff and not self.is_superuser and self.producer_id is None:
            raise ValidationError(
                {"producer": "Укажите производство, в котором работает сотрудник."}
            )
