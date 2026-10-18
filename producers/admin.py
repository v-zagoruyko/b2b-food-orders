from django import forms
from django.contrib import admin

from core.admin_mixins import ScopedHistoryAdmin
from core.tenancy import scope_to_producer

from .models import Producer, Weekday


class ProducerAdminForm(forms.ModelForm):
    delivery_weekdays = forms.TypedMultipleChoiceField(
        choices=Weekday.choices,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
        label="Дни доставки",
        help_text="В какие дни недели производство возит заказы.",
    )

    class Meta:
        model = Producer
        fields = (
            "name",
            "is_active",
            "timezone",
            "order_cutoff_time",
            "lead_days",
            "delivery_weekdays",
            "min_order_amount",
            "summary_emails",
            "summary_send_time",
            "summary_days_ahead",
            "debt_control_enabled",
            "debt_limit_amount",
            "overdue_days_limit",
            "onboarding_done",
        )


@admin.register(Producer)
class ProducerAdmin(ScopedHistoryAdmin):
    form = ProducerAdminForm
    list_display = (
        "name",
        "order_cutoff_time",
        "min_order_amount",
        "debt_limit_amount",
        "onboarding_done",
        "is_active",
    )
    list_filter = ("is_active", "onboarding_done")
    search_fields = ("name",)
    fieldsets = (
        (None, {"fields": ("name", "is_active", "timezone")}),
        (
            "Приём заказов",
            {
                "fields": (
                    "order_cutoff_time",
                    "lead_days",
                    "delivery_weekdays",
                    "min_order_amount",
                ),
                "description": (
                    "Пример: заказы до 14:00, доставка на следующий день. "
                    "Поздние заказы система не принимает и предлагает ближайшую дату."
                ),
            },
        ),
        (
            "Сводка на почту",
            {
                "fields": (
                    "summary_emails",
                    "summary_send_time",
                    "summary_days_ahead",
                ),
                "description": (
                    "В сводку попадают подтверждённые заказы. Заказы в статусах "
                    "«Новый» и «Требует решения» в неё не входят, о них письмо "
                    "предупредит отдельно. Пустое время отправки отключает рассылку."
                ),
            },
        ),
        (
            "Контроль долга",
            {
                "fields": (
                    "debt_control_enabled",
                    "debt_limit_amount",
                    "overdue_days_limit",
                    "onboarding_done",
                ),
                "description": (
                    "Заказ никогда не блокируется: при превышении порога он получает "
                    "статус «Требует решения», а вы подтверждаете или отклоняете его."
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return scope_to_producer(super().get_queryset(request), request.user)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
