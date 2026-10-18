from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import Group
from django.db.models import Q

from core.admin_mixins import ScopedHistoryAdmin
from core.tenancy import scope_to_producer
from customers.models import Point
from producers.models import Producer

from .groups import GROUPS
from .models import User


class UserCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email", "name")


class UserChangeFormWithoutUsername(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(ScopedHistoryAdmin, BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeFormWithoutUsername
    list_display = ("name", "email", "phone", "role", "producer", "is_active")
    list_filter = (
        "is_staff",
        "is_active",
        ("producer", admin.RelatedOnlyFieldListFilter),
        "groups",
    )
    search_fields = ("name", "email", "phone", "points__name")
    ordering = ("name",)
    filter_horizontal = ("groups", "user_permissions", "points")
    fieldsets = (
        (None, {"fields": ("name", "email", "password")}),
        ("Контакты", {"fields": ("phone", "telegram_id")}),
        (
            "Сотрудник производства",
            {
                "fields": ("producer", "is_staff", "groups"),
                "description": "Права в админке выдаются группами.",
            },
        ),
        (
            "Менеджер точки",
            {
                "fields": ("points",),
                "description": "Менеджер видит в приложении только эти точки.",
            },
        ),
        ("Статус", {"fields": ("is_active",)}),
        (
            "Служебное",
            {
                "fields": (
                    "is_superuser",
                    "user_permissions",
                    "last_login",
                    "date_joined",
                )
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "name",
                    "email",
                    "usable_password",
                    "password1",
                    "password2",
                ),
                "description": (
                    "Менеджерам точек пароль не нужен: выберите «Отключить "
                    "аутентификацию по паролю», они входят через Telegram."
                ),
            },
        ),
        (
            "Роль",
            {"fields": ("producer", "is_staff", "groups", "points", "phone")},
        ),
    )
    readonly_fields = ("last_login", "date_joined")

    @admin.display(description="Роль")
    def role(self, obj):
        return "Сотрудник" if obj.is_staff else "Менеджер точки"

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            # Производство указывается только сотруднику; менеджер точки
            # привязан к производствам через свои точки.
            obj.producer = request.user.producer if obj.is_staff else None
        super().save_model(request, obj, form, change)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if request.user.producer_id is None:
            return qs.none()
        mine = request.user.producer_id
        # Сотрудники производства и менеджеры его точек. Сотрудник другого
        # производства сюда не попадает, даже если он менеджер точки у нас.
        return (
            qs.filter(
                Q(producer_id=mine)
                | Q(is_staff=False, points__customer__producer_id=mine)
            )
            .exclude(is_superuser=True)
            .distinct()
        )

    def _foreign_points(self, request, obj):
        """Точки пользователя в других производствах (недоступны этому владельцу)."""
        if request.user.is_superuser or obj is None or obj.pk is None:
            return []
        return list(obj.points.exclude(customer__producer_id=request.user.producer_id))

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        # Менеджер с точками нескольких производств общий: вход и активность
        # не может менять владелец одного из них.
        if obj is not None and self._foreign_points(request, obj):
            return (*readonly, "telegram_id", "is_active")
        return readonly

    def save_related(self, request, form, formsets, change):
        foreign = self._foreign_points(request, form.instance) if change else []
        super().save_related(request, form, formsets, change)
        if foreign:
            # Форма видит только свои точки, связи с чужими сохраняем как были.
            form.instance.points.add(*foreign)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            return fieldsets
        return [fs for fs in fieldsets if fs[0] != "Служебное"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        producer = form.base_fields.get("producer")
        if producer is not None and not request.user.is_superuser:
            producer.widget = forms.HiddenInput()
            producer.disabled = True
            producer.required = False
            producer.initial = request.user.producer_id
        return form

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.related_model is Producer:
            kwargs["queryset"] = scope_to_producer(Producer.objects.all(), request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.related_model is Point:
            kwargs["queryset"] = scope_to_producer(Point.objects.all(), request.user)
        elif db_field.related_model is Group and not request.user.is_superuser:
            kwargs["queryset"] = Group.objects.filter(name__in=GROUPS)
        return super().formfield_for_manytomany(db_field, request, **kwargs)
