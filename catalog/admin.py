from django.contrib import admin
from django.db.models import Count

from core.admin_mixins import ProducerScopedAdmin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(ProducerScopedAdmin):
    list_display = ("name", "sort_order", "products_count", "is_active")
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    fieldsets = ((None, {"fields": ("producer", "name", "sort_order", "is_active")}),)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_products=Count("products"))

    @admin.display(description="Позиций", ordering="_products")
    def products_count(self, obj):
        return obj._products


@admin.register(Product)
class ProductAdmin(ProducerScopedAdmin):
    list_display = ("name", "category", "unit", "price", "sort_order", "is_active")
    list_editable = ("price", "sort_order", "is_active")
    list_filter = (
        ("category", admin.RelatedOnlyFieldListFilter),
        "unit",
        "is_active",
    )
    list_select_related = ("category",)
    list_per_page = 100
    search_fields = ("name", "description")
    autocomplete_fields = ("category",)
    fieldsets = (
        (
            None,
            {"fields": ("producer", "category", "name", "description", "is_active")},
        ),
        ("Единицы и цена", {"fields": ("unit", "order_step", "price")}),
        ("Порядок в каталоге", {"fields": ("sort_order",)}),
    )
