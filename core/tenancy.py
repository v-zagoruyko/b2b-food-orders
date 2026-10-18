from django.db.models import QuerySet

# Путь от модели до производства: по нему режутся данные в админке и API.
PRODUCER_LOOKUPS = {
    "producers.producer": "pk",
    "customers.customer": "producer",
    "customers.point": "customer__producer",
    "catalog.category": "producer",
    "catalog.product": "producer",
    "orders.order": "point__customer__producer",
    "orders.summarydelivery": "producer",
    "payments.payment": "customer__producer",
}


def scope_to_producer(queryset: QuerySet, user) -> QuerySet:
    """Оставляет записи производства пользователя. Superuser видит всё."""
    if user.is_superuser:
        return queryset
    lookup = PRODUCER_LOOKUPS[queryset.model._meta.label_lower]
    if user.producer_id is None:
        return queryset.none()
    return queryset.filter(**{lookup: user.producer_id})
