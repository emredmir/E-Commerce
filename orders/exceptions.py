class OrderDomainError(Exception):
    """Order domain'indeki tüm business exception'ların tabanı."""
    pass


# ============================================================================
# VALIDATION
# ============================================================================

class OrderValidationError(OrderDomainError):
    """Checkout/order validation hatalarının tabanı."""
    pass


class CartAccessError(OrderValidationError):
    """Sepet kullanıcı/session'a ait değil."""
    pass


class EmptyOrderError(OrderValidationError):
    """Sipariş oluşturulurken seçili ürün bulunamadı."""
    pass


class CartItemSelectionError(OrderValidationError):
    """Checkout item'ları geçerli selected cart item'larla eşleşmiyor."""
    pass


class InvalidAddressError(OrderValidationError):
    """Adres geçersiz veya kullanıcıya ait değil."""
    pass


class InvalidCurrencyError(OrderValidationError):
    """Currency geçersiz veya sistem tarafından desteklenmiyor."""
    pass


class ProductUnavailableError(OrderValidationError):
    """Ürün artık satın alınabilir değil."""
    pass


# ============================================================================
# ORDER
# ============================================================================

class OrderNotFoundError(OrderDomainError):
    """Sipariş bulunamadı."""
    pass


class InvalidOrderStateError(OrderDomainError):
    """Sipariş mevcut state'i nedeniyle bu işleme uygun değil."""
    pass


class OrderCreationError(OrderDomainError):
    """Sipariş oluşturulması sırasında domain seviyesinde hata oluştu."""
    pass


# ============================================================================
# STOCK
# ============================================================================

class StockError(OrderDomainError):
    """Stok işlemleriyle ilgili domain hatalarının tabanı."""
    pass


class InsufficientStockError(StockError):
    """Sipariş veya reservation için yeterli stok bulunamadı."""
    pass


# ============================================================================
# RESERVATION
# ============================================================================

class ReservationError(StockError):
    """Stok rezervasyonu ile ilgili hataların tabanı."""
    pass


class ReservationExpiredError(ReservationError):
    """Stok rezervasyonunun süresi doldu."""
    pass


# ============================================================================
# PAYMENT
# ============================================================================

class PaymentError(OrderDomainError):
    """Ödeme işlemleriyle ilgili hataların tabanı."""
    pass


class PaymentGatewayError(PaymentError):
    """Ödeme sağlayıcısı ile iletişim veya işlem hatası."""
    pass


class PaymentAlreadyProcessedError(PaymentError):
    """Ödeme daha önce başarıyla işlenmiş."""
    pass


class PaymentVerificationError(PaymentError):
    """Ödeme veya callback doğrulaması başarısız."""
    pass


# ============================================================================
# REFUND
# ============================================================================

class RefundError(OrderDomainError):
    """İade işlemleriyle ilgili hataların tabanı."""
    pass


class InvalidRefundAmountError(RefundError):
    """İade tutarı geçersiz."""
    pass