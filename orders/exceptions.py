import datetime
from decimal import Decimal


class OrderError(Exception):
    """Заказ не может быть оформлен. Текст понятен менеджеру точки."""

    code = "order_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OrderingUnavailable(OrderError):
    code = "ordering_unavailable"


class PointAccessDenied(OrderError):
    code = "point_access_denied"


class EmptyOrder(OrderError):
    code = "empty_order"


class ProductUnavailable(OrderError):
    code = "product_unavailable"


class InvalidQuantity(OrderError):
    code = "invalid_quantity"


class DeliveryDateUnavailable(OrderError):
    code = "delivery_date_unavailable"

    def __init__(self, message: str, nearest: datetime.date | None = None):
        super().__init__(message)
        self.nearest = nearest


class BelowMinimumOrder(OrderError):
    code = "below_minimum_order"

    def __init__(self, message: str, minimum: Decimal):
        super().__init__(message)
        self.minimum = minimum
