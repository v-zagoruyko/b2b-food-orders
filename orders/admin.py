import datetime

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.forms import BaseInlineFormSet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from viewflow.fsm import TransitionNotAllowed

from core.admin_mixins import ProducerScopedAdmin
from core.tenancy import scope_to_producer
from customers.admin import format_money
from customers.services import get_balance
from producers.models import Producer

from .exceptions import OrderError
from .exports import build_summary_workbook, workbook_to_bytes
from .models import (
    Order,
    OrderLine,
    OrderStatus,
    SummaryDelivery,
    SummaryTrigger,
)
from .notifications import XLSX_CONTENT_TYPE, send_summary, summary_filename
from .services import prepare_order
from .summary import build_summary, default_summary_date

# Цвета бейджей статусов: «требует решения» — оранжевый (ждёт производство),
# в работе — зелёный, конечные — серый, отклонён и отменён — красный.
STATUS_COLORS = {
    OrderStatus.NEW: "#0b5ed7",
    OrderStatus.NEEDS_REVIEW: "#c76a00",
    OrderStatus.CONFIRMED: "#1b7a3d",
    OrderStatus.DELIVERED: "#6c757d",
    OrderStatus.REJECTED: "#ba2121",
    OrderStatus.CANCELED: "#ba2121",
}


class OrderAddForm(forms.ModelForm):
    """Новый заказ, внесённый сотрудником (например, по телефону)."""

    class Meta:
        model = Order
        fields = ("point", "delivery_date", "comment")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["delivery_date"].required = False
        self.fields["delivery_date"].help_text = (
            "Оставьте пустым: подставится ближайшая дата доставки. Сотрудник может "
            "выбрать любую дату от сегодняшней, срок приёма не проверяется."
        )


class OrderLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        order = self.instance
        if any(self.errors) or not order._state.adding or order.point_id is None:
            return
        lines = [
            (form.cleaned_data["product"], form.cleaned_data["quantity"])
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("product")
        ]
        try:
            # Те же правила, что при оформлении точкой: минимум, кратность, порог.
            order._prepared = prepare_order(
                point=order.point,
                user=None,
                lines=lines,
                delivery_date=order.delivery_date,
                staff=True,
            )
        except OrderError as error:
            raise ValidationError(error.message) from error


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    formset = OrderLineFormSet
    extra = 0
    fields = ("product", "quantity", "unit", "unit_price", "line_total")
    readonly_fields = ("unit", "unit_price", "line_total")
    autocomplete_fields = ("product",)

    def get_extra(self, request, obj=None, **kwargs):
        return 3 if obj is None else 0

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "product":
            from core.tenancy import scope_to_producer

            kwargs["queryset"] = scope_to_producer(
                db_field.related_model._default_manager.all(), request.user
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Order)
class OrderAdmin(ProducerScopedAdmin):
    list_display = (
        "id",
        "delivery_date",
        "point",
        "customer",
        "status_badge",
        "total",
        "debt_badge",
        "created_at",
    )
    list_filter = (
        "status",
        "delivery_date",
        ("point", admin.RelatedOnlyFieldListFilter),
    )
    list_select_related = ("point", "point__customer")
    date_hierarchy = "delivery_date"
    search_fields = ("id", "point__name", "point__customer__name", "comment")
    autocomplete_fields = ("point",)
    inlines = (OrderLineInline,)
    actions = ("confirm_orders", "reject_orders", "deliver_orders", "cancel_orders")
    readonly_fields = (
        "status_badge",
        "total",
        "created_by",
        "created_at",
        "review_reason",
        "debt_at_order",
        "overdue_days_at_order",
        "decided_by",
        "decided_at",
        "debt_badge",
    )
    fieldsets = (
        (None, {"fields": ("point", "delivery_date", "status_badge", "total")}),
        ("Комментарий точки", {"fields": ("comment",)}),
        (
            "Долг заказчика",
            {
                "fields": (
                    "debt_badge",
                    "review_reason",
                    "debt_at_order",
                    "overdue_days_at_order",
                    "decided_by",
                    "decided_at",
                ),
                "description": (
                    "Долг сейчас — по данным производства. Остальное зафиксировано "
                    "на момент оформления заказа."
                ),
            },
        ),
        (
            "Отмена",
            {
                "fields": ("cancel_reason",),
                "classes": ("collapse",),
                "description": (
                    "Заполните причину, затем выберите в списке действие «Отменить»."
                ),
            },
        ),
        ("Служебное", {"fields": ("created_by", "created_at")}),
    )

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = OrderAddForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return [(None, {"fields": ("point", "delivery_date", "comment")})]
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            if obj.created_by_id is None:
                obj.created_by = request.user
            prepared = getattr(obj, "_prepared", None)
            if prepared is not None:
                obj.delivery_date = prepared.delivery_date
                obj.status = prepared.status
                obj.total = prepared.total
                obj.review_reason = prepared.review_reason
                obj.debt_at_order = prepared.debt
                obj.overdue_days_at_order = prepared.overdue_days
                if prepared.status == OrderStatus.NEEDS_REVIEW:
                    self.message_user(
                        request,
                        "У заказчика превышен порог долга, заказ получил статус "
                        "«Требует решения». Подтвердите или отклоните его.",
                        messages.WARNING,
                    )
        super().save_model(request, obj, form, change)

    @admin.display(description="Заказчик", ordering="point__customer__name")
    def customer(self, obj):
        return obj.point.customer

    @admin.display(description="Статус", ordering="status")
    def status_badge(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLORS[obj.status],
            obj.get_status_display(),
        )

    @admin.display(description="Долг заказчика")
    def debt_badge(self, obj):
        if not obj.pk:
            return "—"
        balance = get_balance(obj.point.customer)
        if balance.debt <= 0:
            return "нет"
        text = format_money(balance.debt)
        if balance.overdue_days:
            text += f", просрочка {balance.overdue_days} дн."
        return format_html('<b style="color:#ba2121">{}</b>', text)

    def _transition(self, request, queryset, method_name, done_text, decided=False):
        pks = list(queryset.values_list("pk", flat=True))
        done = 0
        with transaction.atomic():
            # Блокировка строк: параллельная правка не затрёт статус и сумму.
            locked = Order.objects.select_for_update(of=("self",)).filter(pk__in=pks)
            for order in locked.select_related("point__customer"):
                try:
                    getattr(order, method_name)()
                except TransitionNotAllowed:
                    self.message_user(
                        request,
                        f"{order}: из статуса «{order.get_status_display()}» "
                        "это действие недоступно.",
                        messages.WARNING,
                    )
                    continue
                fields = ["status", "updated_at"]
                if decided:
                    order.decided_by = request.user
                    order.decided_at = timezone.now()
                    fields += ["decided_by", "decided_at"]
                order.save(update_fields=fields)
                done += 1
        if done:
            self.message_user(request, f"{done_text}: {done}.", messages.SUCCESS)

    @admin.action(description="Подтвердить", permissions=["decide"])
    def confirm_orders(self, request, queryset):
        self._transition(request, queryset, "confirm", "Подтверждено", decided=True)

    @admin.action(
        description="Отклонить (только «Требует решения»)", permissions=["decide"]
    )
    def reject_orders(self, request, queryset):
        self._transition(request, queryset, "reject", "Отклонено", decided=True)

    @admin.action(description="Отметить доставленными", permissions=["deliver"])
    def deliver_orders(self, request, queryset):
        self._transition(request, queryset, "deliver", "Отмечено доставленными")

    @admin.action(
        description="Отменить (нужна причина в карточке)", permissions=["cancel"]
    )
    def cancel_orders(self, request, queryset):
        without_reason = queryset.filter(cancel_reason="")
        if without_reason.exists():
            ids = ", ".join(
                f"№{pk}" for pk in without_reason.values_list("pk", flat=True)
            )
            self.message_user(
                request,
                f"Не отменено: укажите причину отмены в карточке заказов {ids}.",
                messages.ERROR,
            )
            queryset = queryset.exclude(pk__in=without_reason)
        self._transition(request, queryset, "cancel", "Отменено")

    def has_decide_permission(self, request):
        return request.user.has_perm("orders.decide_order")

    def has_deliver_permission(self, request):
        return request.user.has_perm("orders.deliver_order")

    def has_cancel_permission(self, request):
        return request.user.has_perm("orders.cancel_order")

    def get_urls(self):
        custom = [
            path(
                "summary/",
                self.admin_site.admin_view(self.summary_view),
                name="orders_order_summary",
            ),
            path(
                "summary/xlsx/",
                self.admin_site.admin_view(self.summary_xlsx_view),
                name="orders_order_summary_xlsx",
            ),
            path(
                "summary/send/",
                self.admin_site.admin_view(self.summary_send_view),
                name="orders_order_summary_send",
            ),
        ]
        return custom + super().get_urls()

    def _summary_params(self, request):
        """Производство и дата сводки из запроса; superuser выбирает производство."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        data = request.POST or request.GET
        user = request.user
        if user.is_superuser:
            producers = Producer.objects.filter(is_active=True)
            pk = data.get("producer")
            producer = get_object_or_404(producers, pk=pk) if pk else producers.first()
            if producer is None:
                raise PermissionDenied
        elif user.producer_id is None:
            raise PermissionDenied
        else:
            producer = user.producer
        try:
            day = datetime.date.fromisoformat(data.get("date", ""))
        except ValueError:
            day = default_summary_date(producer)
        return producer, day

    def summary_view(self, request):
        producer, day = self._summary_params(request)
        summary = build_summary(producer, day)
        context = {
            **self.admin_site.each_context(request),
            "title": "Сводка на дату доставки",
            "opts": self.model._meta,
            "summary": summary,
            "prev_date": day - datetime.timedelta(days=1),
            "next_date": day + datetime.timedelta(days=1),
            "producers": (
                Producer.objects.filter(is_active=True)
                if request.user.is_superuser
                else None
            ),
            "producer_query": (
                f"&producer={producer.pk}" if request.user.is_superuser else ""
            ),
            "can_send": self.has_change_permission(request),
        }
        return TemplateResponse(request, "admin/orders/order/summary.html", context)

    def summary_xlsx_view(self, request):
        producer, day = self._summary_params(request)
        summary = build_summary(producer, day)
        response = HttpResponse(
            workbook_to_bytes(build_summary_workbook(summary)),
            content_type=XLSX_CONTENT_TYPE,
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{summary_filename(summary)}"'
        )
        return response

    def summary_send_view(self, request):
        producer, day = self._summary_params(request)
        if request.method != "POST" or not self.has_change_permission(request):
            raise PermissionDenied
        back = reverse("admin:orders_order_summary")
        query = f"?date={day:%Y-%m-%d}"
        if request.user.is_superuser:
            query += f"&producer={producer.pk}"
        if not producer.summary_emails:
            self.message_user(
                request,
                "Укажите email для сводки в настройках производства.",
                messages.ERROR,
            )
            return redirect(back + query)
        record = send_summary(
            producer, day, SummaryTrigger.MANUAL, sent_by=request.user
        )
        if record.error:
            self.message_user(
                request, f"Не удалось отправить письмо: {record.error}", messages.ERROR
            )
        else:
            self.message_user(
                request,
                f"Сводка на {day:%d.%m.%Y} отправлена: {', '.join(record.recipients)}.",
                messages.SUCCESS,
            )
        return redirect(back + query)


@admin.register(SummaryDelivery)
class SummaryDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "sent_at",
        "producer",
        "delivery_date",
        "trigger",
        "orders_count",
        "pending_count",
        "total",
        "status",
    )
    list_filter = (
        "trigger",
        "delivery_date",
        ("producer", admin.RelatedOnlyFieldListFilter),
    )
    list_select_related = ("producer",)
    date_hierarchy = "delivery_date"

    def get_queryset(self, request):
        return scope_to_producer(super().get_queryset(request), request.user)

    @admin.display(description="Результат")
    def status(self, obj):
        if obj.error:
            return format_html('<b style="color:#ba2121">Ошибка: {}</b>', obj.error)
        return "Отправлена"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
