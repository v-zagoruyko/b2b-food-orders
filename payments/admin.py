from django.contrib import admin

from core.admin_mixins import ProducerScopedAdmin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(ProducerScopedAdmin):
    list_display = ("paid_at", "customer", "amount", "document_number", "created_by")
    list_filter = (("customer", admin.RelatedOnlyFieldListFilter), "paid_at")
    list_select_related = ("customer", "created_by")
    date_hierarchy = "paid_at"
    search_fields = ("customer__name", "document_number", "comment")
    autocomplete_fields = ("customer",)
    readonly_fields = ("created_by", "created_at")
    fieldsets = (
        (None, {"fields": ("customer", "amount", "paid_at")}),
        ("Документ", {"fields": ("document_number", "comment")}),
        ("Служебное", {"fields": ("created_by", "created_at")}),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
