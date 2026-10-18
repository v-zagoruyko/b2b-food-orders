from django.contrib import admin
from django.utils.html import format_html

from core.admin_mixins import ProducerScopedAdmin

from .models import Customer, Invitation, Point
from .services import get_balance


def format_money(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


class PointInline(admin.StackedInline):
    model = Point
    extra = 0
    show_change_link = True
    fields = (
        ("name", "is_active"),
        "address",
        ("contact_name", "contact_phone"),
        "min_order_amount",
    )


@admin.register(Customer)
class CustomerAdmin(ProducerScopedAdmin):
    list_display = (
        "name",
        "legal_form",
        "inn",
        "debt_badge",
        "overdue_badge",
        "points_count",
        "is_active",
    )
    list_filter = ("legal_form", "is_active")
    search_fields = ("name", "inn", "points__name", "points__address")
    inlines = (PointInline,)
    readonly_fields = ("debt_badge", "overdue_badge")
    fieldsets = (
        (None, {"fields": ("producer", "name", "legal_form", "inn", "is_active")}),
        (
            "Расчёты",
            {
                "fields": (
                    "payment_delay_days",
                    "debt_limit_amount",
                    "overdue_days_limit",
                ),
                "description": "Свои пороги перекрывают общие пороги производства.",
            },
        ),
        (
            "Баланс по данным производства",
            {
                "fields": ("debt_badge", "overdue_badge"),
                "description": (
                    "Принятые заказы минус внесённые оплаты. Оплаты вносятся "
                    "вручную, поэтому данные могут отставать."
                ),
            },
        ),
        ("Комментарий", {"fields": ("comment",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("points")

    def _balance(self, obj):
        if not obj.pk:
            return None
        return get_balance(obj)

    @admin.display(description="Долг")
    def debt_badge(self, obj):
        balance = self._balance(obj)
        if balance is None:
            return "—"
        if balance.debt > 0:
            return format_html(
                '<b style="color:#ba2121">{}</b>', format_money(balance.debt)
            )
        if balance.debt < 0:
            return format_html("предоплата {}", format_money(-balance.debt))
        return "нет"

    @admin.display(description="Просрочка")
    def overdue_badge(self, obj):
        balance = self._balance(obj)
        if balance is None or not balance.overdue_days:
            return "нет" if balance is not None else "—"
        return format_html(
            '<b style="color:#ba2121">{} дн., {}</b>',
            balance.overdue_days,
            format_money(balance.overdue_amount),
        )

    @admin.display(description="Точек")
    def points_count(self, obj):
        return len(obj.points.all())


class InvitationInline(admin.TabularInline):
    model = Invitation
    extra = 0
    fields = ("expires_at", "link_display", "state", "used_by")
    readonly_fields = ("link_display", "state", "used_by")

    @admin.display(description="Ссылка для менеджера")
    def link_display(self, obj):
        if not obj.pk:
            return "Появится после сохранения"
        return obj.link or f"Токен: {obj.token} (не задан TELEGRAM_BOT_USERNAME)"

    @admin.display(description="Состояние")
    def state(self, obj):
        if not obj.pk:
            return "—"
        if obj.used_at:
            return "Принято"
        return "Действует" if obj.is_valid else "Истекло"


@admin.register(Point)
class PointAdmin(ProducerScopedAdmin):
    list_display = ("name", "customer", "address", "min_order_amount", "is_active")
    list_filter = (("customer", admin.RelatedOnlyFieldListFilter), "is_active")
    list_select_related = ("customer",)
    search_fields = (
        "name",
        "address",
        "customer__name",
        "contact_name",
        "contact_phone",
    )
    autocomplete_fields = ("customer",)
    inlines = (InvitationInline,)
    fieldsets = (
        (None, {"fields": ("customer", "name", "address", "is_active")}),
        ("Контакты", {"fields": ("contact_name", "contact_phone")}),
        ("Условия", {"fields": ("min_order_amount",)}),
    )

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, Invitation) and instance.created_by_id is None:
                instance.created_by = request.user
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()
