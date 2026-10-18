from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from core.admin_mixins import ProducerScopedAdmin
from producers.models import Producer

from .importer import (
    ImportFileError,
    apply_import,
    build_template,
    plan_import,
    read_rows,
)
from .models import Category, Product

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IMPORT_SESSION_KEY = "catalog_import"


class ImportForm(forms.Form):
    file = forms.FileField(
        label="Файл",
        help_text="Excel (.xlsx) или CSV по шаблону. До 2 МБ и 2000 строк.",
    )


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

    def get_urls(self):
        custom = [
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="catalog_product_import",
            ),
            path(
                "import/template/",
                self.admin_site.admin_view(self.import_template_view),
                name="catalog_product_import_template",
            ),
        ]
        return custom + super().get_urls()

    def _import_producer(self, request):
        if not (
            self.has_add_permission(request) and self.has_change_permission(request)
        ):
            raise PermissionDenied
        user = request.user
        if user.is_superuser:
            producers = Producer.objects.filter(is_active=True)
            pk = request.POST.get("producer") or request.GET.get("producer")
            producer = get_object_or_404(producers, pk=pk) if pk else producers.first()
            if producer is None:
                raise PermissionDenied
            return producer
        if user.producer_id is None:
            raise PermissionDenied
        return user.producer

    def import_template_view(self, request):
        self._import_producer(request)
        response = HttpResponse(build_template(), content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = 'attachment; filename="catalog-template.xlsx"'
        return response

    def import_view(self, request):
        producer = self._import_producer(request)
        context = {
            **self.admin_site.each_context(request),
            "title": "Импорт каталога из файла",
            "opts": self.model._meta,
            "producer": producer,
            "producers": (
                Producer.objects.filter(is_active=True)
                if request.user.is_superuser
                else None
            ),
            "form": ImportForm(),
        }
        if request.method == "POST" and "confirm" in request.POST:
            return self._import_confirm(request, producer)
        if request.method == "POST":
            form = ImportForm(request.POST, request.FILES)
            context["form"] = form
            if form.is_valid():
                upload = form.cleaned_data["file"]
                try:
                    rows = read_rows(upload.name, upload.read())
                except ImportFileError as error:
                    form.add_error("file", str(error))
                else:
                    plan = plan_import(producer, rows)
                    request.session[IMPORT_SESSION_KEY] = {
                        "producer": producer.pk,
                        "rows": rows,
                    }
                    context.update(plan=plan, preview=True)
        return TemplateResponse(request, "admin/catalog/product/import.html", context)

    def _import_confirm(self, request, producer):
        saved = request.session.pop(IMPORT_SESSION_KEY, None)
        changelist = reverse("admin:catalog_product_changelist")
        if not saved or saved["producer"] != producer.pk:
            self.message_user(
                request,
                "Загрузите файл заново: данные предпросмотра устарели.",
                messages.ERROR,
            )
            return redirect(reverse("admin:catalog_product_import"))
        plan = plan_import(producer, saved["rows"])
        if plan.has_errors:
            self.message_user(
                request, "В файле остались ошибки, импорт не выполнен.", messages.ERROR
            )
            return redirect(reverse("admin:catalog_product_import"))
        result = apply_import(producer, plan)
        self.message_user(
            request,
            f"Импорт выполнен: создано {result['created']}, обновлено {result['updated']}.",
            messages.SUCCESS,
        )
        return redirect(changelist)
