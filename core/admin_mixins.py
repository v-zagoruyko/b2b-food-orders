from django import forms
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.http import Http404
from simple_history.admin import SimpleHistoryAdmin

from .tenancy import PRODUCER_LOOKUPS, scope_to_producer


class ScopedHistoryAdmin(SimpleHistoryAdmin):
    """История изменений только для объектов, доступных пользователю.

    Исходный `SimpleHistoryAdmin` достаёт объект из исторических записей
    в обход `get_queryset`, а откат версии проверяет только право просмотра.
    Здесь история доступна лишь по объектам из своей выборки, откат отключён.
    """

    def history_view(self, request, object_id, extra_context=None):
        if self.get_object(request, unquote(object_id)) is None:
            raise Http404
        return super().history_view(request, object_id, extra_context)

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        if self.get_object(request, unquote(object_id)) is None:
            raise Http404
        if request.method != "GET":
            raise PermissionDenied
        return super().history_form_view(request, object_id, version_id, extra_context)


class ProducerScopedAdmin(ScopedHistoryAdmin):
    """Админка с изоляцией по производству.

    Сотрудник видит и выбирает только данные своего производства, superuser — все.
    Поле `producer` у сотрудника скрыто и подставляется автоматически.
    """

    def get_queryset(self, request):
        return scope_to_producer(super().get_queryset(request), request.user)

    def _scope_related(self, db_field, request, kwargs):
        related = db_field.related_model._meta.label_lower
        if related in PRODUCER_LOOKUPS:
            kwargs["queryset"] = scope_to_producer(
                db_field.related_model._default_manager.all(), request.user
            )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        self._scope_related(db_field, request, kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        self._scope_related(db_field, request, kwargs)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        field = form.base_fields.get("producer")
        if field is not None and not request.user.is_superuser:
            # Поле остаётся в форме (иначе не сработают проверки уникальности),
            # но скрыто и всегда равно производству сотрудника.
            field.widget = forms.HiddenInput()
            field.disabled = True
            field.required = False
            field.initial = request.user.producer_id
        return form
