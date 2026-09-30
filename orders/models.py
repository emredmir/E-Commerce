import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


from products.models import StoreProduct
from store.models import Store


# ==============================================================================
# HELPERS
# ==============================================================================


def generate_order_number():
    """
    Public ana sipariş numarası üretir.

    Örnek:
        TX-A1B2C3D4E5F678901234
    """
    return f"TX-{uuid.uuid4().hex[:20].upper()}"


def generate_suborder_number():
    """
    Mağaza bazlı alt sipariş numarası üretir.

    Örnek:
        SUB-A1B2C3D4E5F678901234
    """
    return f"SUB-{uuid.uuid4().hex[:20].upper()}"


def generate_refund_reference():
    """
    Sistem içi refund referansı üretir.

    Örnek:
        REF-A1B2C3D4E5F67890
    """
    return f"REF-{uuid.uuid4().hex[:16].upper()}"


# ==============================================================================
# ENUMS / STATE MACHINES
# ==============================================================================


class OrderStatus(models.TextChoices):
    """
    Ana siparişin genel yaşam döngüsü.

    Ödeme denemelerinin başarısızlığı PaymentTransaction'a aittir.
    Stok rezervasyonunun yaşam döngüsü StockReservation'a aittir.
    """

    PENDING_PAYMENT = "pending_payment", "Ödeme Bekliyor"
    PAID = "paid", "Ödendi"
    PREPARING = "preparing", "Hazırlanıyor"
    PARTIALLY_SHIPPED = "partially_shipped", "Kısmen Kargolandı"
    SHIPPED = "shipped", "Kargolandı"
    DELIVERED = "delivered", "Teslim Edildi"
    CANCELLED = "cancelled", "İptal Edildi"
    EXPIRED = "expired", "Süresi Doldu"
    COMPLETED = "completed", "Tamamlandı"


class SubOrderStatus(models.TextChoices):
    """
    Mağaza bazlı sipariş yaşam döngüsü.
    """

    PENDING = "pending", "Bekliyor"
    PREPARING = "preparing", "Hazırlanıyor"
    SHIPPED = "shipped", "Kargolandı"
    DELIVERED = "delivered", "Teslim Edildi"
    CANCELLED = "cancelled", "İptal Edildi"


class PaymentStatus(models.TextChoices):
    """
    Tek bir ödeme denemesinin durumu.

    Aynı Order altında birden fazla PaymentTransaction olabilir.
    """

    INITIATED = "initiated", "Başlatıldı"
    PENDING = "pending", "Bekliyor"
    SUCCESS = "success", "Başarılı"
    FAILED = "failed", "Başarısız"
    REFUNDED = "refunded", "İade Edildi"
    PARTIALLY_REFUNDED = "partially_refunded", "Kısmen İade Edildi"


class RefundStatus(models.TextChoices):
    """
    Refund işleminin durumu.
    """

    PENDING = "pending", "Bekliyor"
    SUCCESS = "success", "Başarılı"
    FAILED = "failed", "Başarısız"


class ReservationStatus(models.TextChoices):
    """
    Stok rezervasyonunun yaşam döngüsü.
    """

    ACTIVE = "active", "Aktif (Kilitli)"
    CONSUMED = "consumed", "Tüketildi (Satın Alındı)"
    RELEASED = "released", "Serbest Bırakıldı (İptal)"
    EXPIRED = "expired", "Süresi Doldu"


# ==============================================================================
# 1. ORDER
# ==============================================================================


class Order(models.Model):
    """
    Müşterinin tek checkout / tek ödeme sürecindeki ana siparişi.

    Multi-vendor yapı:

        Order
            ├── SubOrder (Store A)
            │      ├── OrderItem
            │      └── OrderItem
            │
            ├── SubOrder (Store B)
            │      └── OrderItem
            │
            └── PaymentTransaction

    Customer:
        Order'ın tamamını görebilir.

    Seller:
        Yalnızca kendi Store'una ait SubOrder'ı görebilir.
    """

    # ==========================================================================
    # IDENTITY
    # ==========================================================================

    order_number = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        default=generate_order_number,
        verbose_name="Sipariş Numarası",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Müşteri",
    )

    buyer_identity_number = models.CharField(
        max_length=50,
        blank=True,
    )

    # Sipariş anındaki müşteri iletişim bilgileri.
    # Kullanıcı daha sonra profilini değiştirse bile sipariş değişmez.

    customer_email = models.EmailField(
        null=True,
        blank=True,
        verbose_name="Müşteri E-posta",
    )

    customer_phone = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        verbose_name="Müşteri Telefonu",
    )

    # ==========================================================================
    # FINANCIAL SNAPSHOT
    # ==========================================================================

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Ara Toplam",
    )

    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="İndirim Tutarı",
    )

    shipping_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Kargo Tutarı",
    )

    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Vergi Tutarı",
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Genel Toplam",
    )

    currency = models.CharField(
        max_length=3,
        default="TRY",
        verbose_name="Para Birimi",
    )

    # ==========================================================================
    # STATUS
    # ==========================================================================

    status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING_PAYMENT,
        db_index=True,
        verbose_name="Sipariş Durumu",
    )

    # ==========================================================================
    # SHIPPING ADDRESS SNAPSHOT
    # ==========================================================================

    shipping_full_name = models.CharField(
        max_length=255,
        verbose_name="Teslimat Alıcısı",
    )

    shipping_phone = models.CharField(
        max_length=20,
        verbose_name="Teslimat Telefonu",
    )

    shipping_address_line1 = models.CharField(
        max_length=255,
        verbose_name="Teslimat Adresi",
    )

    shipping_address_line2 = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Teslimat Adresi 2",
    )

    shipping_city = models.CharField(
        max_length=100,
        verbose_name="Teslimat Şehri",
    )

    shipping_state = models.CharField(
        max_length=100,
        verbose_name="Teslimat İlçesi",
    )

    shipping_postal_code = models.CharField(
        max_length=20,
        verbose_name="Teslimat Posta Kodu",
    )

    # ==========================================================================
    # BILLING ADDRESS SNAPSHOT
    # ==========================================================================

    billing_full_name = models.CharField(
        max_length=255,
        verbose_name="Fatura Adı",
    )

    billing_phone = models.CharField(
        max_length=20,
        verbose_name="Fatura Telefonu",
    )

    billing_address_line1 = models.CharField(
        max_length=255,
        verbose_name="Fatura Adresi",
    )

    billing_address_line2 = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Fatura Adresi 2",
    )

    billing_city = models.CharField(
        max_length=100,
        verbose_name="Fatura Şehri",
    )

    billing_state = models.CharField(
        max_length=100,
        verbose_name="Fatura İlçesi",
    )

    billing_postal_code = models.CharField(
        max_length=20,
        verbose_name="Fatura Posta Kodu",
    )

    # ==========================================================================
    # TIMESTAMPS
    # ==========================================================================

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Ödeme Tarihi",
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Tamamlanma Tarihi",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="İptal Tarihi",
    )

    class Meta:
        verbose_name = "Sipariş"
        verbose_name_plural = "Siparişler"

        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "-created_at",
                ],
                name="order_user_created_idx",
            ),
            models.Index(
                fields=[
                    "status",
                    "-created_at",
                ],
                name="order_status_created_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0),
                name="order_subtotal_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name="order_discount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_amount__gte=0),
                name="order_shipping_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_amount__gte=0),
                name="order_tax_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name="order_total_gte_0",
            ),
        ]

    def __str__(self):
        return self.order_number


# ==============================================================================
# 2. SUB ORDER
# ==============================================================================


class SubOrder(models.Model):
    """
    Bir Order içerisindeki tek bir mağazaya ait sipariş.

    Aynı Order içerisinde aynı Store için yalnızca bir SubOrder olabilir.

    Customer:
        Ana Order üzerinden tüm SubOrder'ları görebilir.

    Seller:
        Yalnızca kendisine ait Store'un SubOrder'ını görebilir.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="sub_orders",
        verbose_name="Ana Sipariş",
    )

    store = models.ForeignKey(
        Store,
        on_delete=models.PROTECT,
        related_name="received_orders",
        verbose_name="Mağaza",
    )

    suborder_number = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        default=generate_suborder_number,
        verbose_name="Alt Sipariş Numarası",
    )

    # ==========================================================================
    # STORE SNAPSHOT
    # ==========================================================================

    # Mağaza daha sonra adını değiştirse bile eski siparişte
    # sipariş oluşturulduğu andaki mağaza adı korunur.
    store_name_snapshot = models.CharField(
        max_length=255,
        verbose_name="Sipariş Anındaki Mağaza Adı",
    )

    # ==========================================================================
    # FINANCIAL SNAPSHOT
    # ==========================================================================

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Ara Toplam",
    )

    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="İndirim Tutarı",
    )

    shipping_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Kargo Tutarı",
    )

    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Vergi Tutarı",
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Genel Toplam",
    )

    # ==========================================================================
    # STATUS
    # ==========================================================================

    status = models.CharField(
        max_length=30,
        choices=SubOrderStatus.choices,
        default=SubOrderStatus.PENDING,
        db_index=True,
        verbose_name="Mağaza Sipariş Durumu",
    )

    # ==========================================================================
    # SHIPPING / FULFILLMENT
    # ==========================================================================

    cargo_company = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Kargo Firması",
    )

    cargo_tracking_number = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        verbose_name="Kargo Takip Numarası",
    )

    shipped_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Kargoya Verilme Tarihi",
    )

    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Teslim Tarihi",
    )

    # ==========================================================================
    # TIMESTAMPS
    # ==========================================================================

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    class Meta:
        verbose_name = "Mağaza Siparişi"
        verbose_name_plural = "Mağaza Siparişleri"

        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "store",
                    "-created_at",
                ],
                name="suborder_store_created_idx",
            ),
            models.Index(
                fields=[
                    "status",
                    "-created_at",
                ],
                name="suborder_status_created_idx",
            ),
        ]

        constraints = [
            # Aynı ana siparişte aynı mağaza yalnızca bir kez bulunabilir.
            models.UniqueConstraint(
                fields=[
                    "order",
                    "store",
                ],
                name="unique_store_per_order",
            ),

            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0),
                name="suborder_subtotal_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name="suborder_discount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_amount__gte=0),
                name="suborder_shipping_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_amount__gte=0),
                name="suborder_tax_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name="suborder_total_gte_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.suborder_number} - "
            f"{self.store_name_snapshot}"
        )


# ==============================================================================
# 3. ORDER ITEM
# ==============================================================================


class OrderItem(models.Model):
    """
    Gerçekte satın alınan ürünün sipariş snapshot kaydı.

    StoreProduct canlı bir referans olarak tutulur ancak
    geçmiş sipariş için gerekli kritik bilgiler ayrıca snapshot'lanır.

    StoreProduct:
        - fiyat değiştirebilir
        - SKU değiştirebilir
        - arşivlenebilir

    OrderItem:
        sipariş anındaki bilgileri korur.
    """

    sub_order = models.ForeignKey(
        SubOrder,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Mağaza Siparişi",
    )

    store_product = models.ForeignKey(
        StoreProduct,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Mağaza Ürünü",
    )

    # ==========================================================================
    # PRODUCT SNAPSHOT
    # ==========================================================================

    product_name_snapshot = models.CharField(
        max_length=255,
        verbose_name="Ürün Adı",
    )

    variant_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Varyant Bilgisi",
        help_text=(
            "Sipariş anındaki varyant özelliklerinin snapshot'ı. "
            "Örn: {'Renk': 'Siyah', 'Beden': 'XL'}"
        ),
    )

    variant_display = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Varyant Görünümü",
        help_text=(
            "UI için hazırlanmış varyant metni. "
            "Örn: Renk: Siyah, Beden: XL"
        ),
    )

    sku_snapshot = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="SKU",
    )

    barcode_snapshot = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Barkod",
    )

    # ==========================================================================
    # QUANTITY / FINANCIAL SNAPSHOT
    # ==========================================================================

    quantity = models.PositiveIntegerField(
        default=1,
        validators=[
            MinValueValidator(1),
        ],
        verbose_name="Adet",
    )

    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Birim Fiyat",
    )

    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="İndirim",
    )

    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Vergi",
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Satır Toplamı",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    class Meta:
        verbose_name = "Sipariş Kalemi"
        verbose_name_plural = "Sipariş Kalemleri"

        ordering = [
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "sub_order",
                    "id",
                ],
                name="orderitem_suborder_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="orderitem_quantity_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name="orderitem_unit_price_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name="orderitem_discount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_amount__gte=0),
                name="orderitem_tax_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name="orderitem_total_gte_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.quantity}x "
            f"{self.product_name_snapshot}"
        )


# ==============================================================================
# 4. STOCK RESERVATION
# ==============================================================================


class StockReservation(models.Model):
    """
    Checkout / ödeme sürecinde OrderItem için stoğu geçici olarak rezerve eder.

    İlişki:

        Order
          ↓
        SubOrder
          ↓
        OrderItem
          ↓
        StockReservation

    store_product ayrıca tutulmaz.

    Çünkü OrderItem zaten StoreProduct'a bağlıdır.
    Aynı StoreProduct bilgisini iki farklı yerde tutmak
    veri tutarsızlığı oluşturabilir.

    Lifecycle:

        ACTIVE
           ├──> CONSUMED
           ├──> RELEASED
           └──> EXPIRED
    """

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="reservations",
        verbose_name="Sipariş Kalemi",
    )

    # ==========================================================================
    # RESERVATION
    # ==========================================================================

    quantity = models.PositiveIntegerField(
        validators=[
            MinValueValidator(1),
        ],
        verbose_name="Rezerve Adet",
    )

    status = models.CharField(
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.ACTIVE,
        db_index=True,
        verbose_name="Rezervasyon Durumu",
    )

    expires_at = models.DateTimeField(
        db_index=True,
        verbose_name="Rezervasyon Bitiş Tarihi",
    )

    # ==========================================================================
    # TIMESTAMPS
    # ==========================================================================

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    class Meta:
        verbose_name = "Stok Rezervasyonu"
        verbose_name_plural = "Stok Rezervasyonları"

        ordering = [
            "-created_at",
        ]

        indexes = [
            # Expiry worker için:
            # ACTIVE + expires_at kombinasyonu önemlidir.
            models.Index(
                fields=[
                    "status",
                    "expires_at",
                ],
                name="reservation_status_exp_idx",
            ),

            # OrderItem bazlı reservation sorguları.
            models.Index(
                fields=[
                    "order_item",
                    "status",
                ],
                name="reservation_item_status_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="reservation_quantity_gte_1",
            ),

            # Aynı OrderItem için aynı anda yalnızca bir ACTIVE reservation.
            models.UniqueConstraint(
                fields=[
                    "order_item",
                ],
                condition=models.Q(
                    status=ReservationStatus.ACTIVE,
                ),
                name="unique_active_reservation_per_item",
            ),
        ]

    def __str__(self):
        return (
            f"{self.order_item.product_name_snapshot} - "
            f"{self.quantity} adet - "
            f"{self.get_status_display()}"
        )


# ==============================================================================
# 5. PAYMENT TRANSACTION
# ==============================================================================


class PaymentTransaction(models.Model):
    """
    Bir Order için yapılan tek bir ödeme denemesi.

    Aynı Order altında birden fazla PaymentTransaction olabilir.

    Örnek:

        Order
            ├── Payment #1 -> FAILED
            ├── Payment #2 -> FAILED
            └── Payment #3 -> SUCCESS

    PaymentTransaction:
        ödeme attempt'ini temsil eder.

    Order:
        siparişin genel durumunu temsil eder.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payment_transactions",
        verbose_name="Sipariş",
    )

    # ==========================================================================
    # PROVIDER
    # ==========================================================================

    provider = models.CharField(
        max_length=50,
        default="iyzico",
        db_index=True,
        verbose_name="Ödeme Sağlayıcı",
    )

    payment_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Provider Payment ID",
    )

    conversation_id = models.CharField(
        max_length=100,
        verbose_name="Conversation ID",
    )

    basket_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Basket ID",
    )

    # ==========================================================================
    # STATUS
    # ==========================================================================

    status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.INITIATED,
        db_index=True,
        verbose_name="Ödeme Durumu",
    )

    fraud_status = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Fraud Status",
    )

    # ==========================================================================
    # MONEY
    # ==========================================================================

    paid_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.00")),
        ],
        verbose_name="Ödenen Tutar",
    )

    currency = models.CharField(
        max_length=3,
        default="TRY",
        verbose_name="Para Birimi",
    )

    # ==========================================================================
    # CARD SNAPSHOT
    # ==========================================================================

    last_four_digits = models.CharField(
        max_length=4,
        blank=True,
        null=True,
        verbose_name="Kart Son 4 Hane",
    )

    card_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Kart Tipi",
    )

    card_association = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Kart Markası",
    )

    # ==========================================================================
    # TIMESTAMPS
    # ==========================================================================

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    succeeded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Başarılı Olma Tarihi",
    )

    class Meta:
        verbose_name = "Ödeme İşlemi"
        verbose_name_plural = "Ödeme İşlemleri"

        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "order",
                    "-created_at",
                ],
                name="payment_order_created_idx",
            ),
            models.Index(
                fields=[
                    "status",
                    "-created_at",
                ],
                name="payment_status_created_idx",
            ),
        ]

        constraints = [
            # Aynı provider altında aynı payment ID tekrar kullanılamaz.
            models.UniqueConstraint(
                fields=[
                    "provider",
                    "payment_id",
                ],
                condition=models.Q(
                    payment_id__isnull=False,
                ),
                name="unique_provider_payment",
            ),

            # Conversation ID provider bazında unique olmalıdır.
            models.UniqueConstraint(
                fields=[
                    "provider",
                    "conversation_id",
                ],
                name="unique_provider_conversation",
            ),

            models.CheckConstraint(
                condition=(
                    models.Q(paid_price__gte=0)
                    | models.Q(paid_price__isnull=True)
                ),
                name="payment_paid_price_gte_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.order.order_number} - "
            f"{self.get_status_display()}"
        )

# ==============================================================================
# 6. PAYMENT CUSTOMER
# ==============================================================================


class PaymentCustomer(models.Model):
    """
    Kullanıcının ödeme sağlayıcısındaki müşteri kimliğini temsil eder.

    iyzico Card Storage tarafında:
        provider_customer_key = cardUserKey

    Bir kullanıcı birden fazla kayıtlı karta sahip olabilir.
    Tüm kartlar aynı cardUserKey altında tutulabilir.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payment_customers",
        verbose_name="Kullanıcı",
    )

    provider = models.CharField(
        max_length=50,
        default="iyzico",
        db_index=True,
        verbose_name="Ödeme Sağlayıcı",
    )

    provider_customer_key = models.CharField(
        max_length=255,
        verbose_name="Provider Customer Key",
    )

    provider_external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Provider External ID",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    class Meta:
        verbose_name = "Ödeme Müşterisi"
        verbose_name_plural = "Ödeme Müşterileri"

        ordering = [
            "-created_at",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "provider",
                ],
                name="unique_payment_customer_provider",
            ),

            models.UniqueConstraint(
                fields=[
                    "provider",
                    "provider_customer_key",
                ],
                name="unique_provider_customer_key",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "provider",
                ],
                name="paycust_user_prov_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.user} - "
            f"{self.provider}"
        )


# ==============================================================================
# 7. STORED CARD
# ==============================================================================
class StoredCardOperationType(models.TextChoices):
    CREATE = "CREATE", "Create"
    DELETE = "DELETE", "Delete"

class StoredCardOperationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROVIDER_SUCCEEDED = "PROVIDER_SUCCEEDED", "Provider Succeeded"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    RECONCILIATION_REQUIRED = (
        "RECONCILIATION_REQUIRED",
        "Reconciliation Required",
    )

class StoredCard(models.Model):
    """
    iyzico Card Storage tarafından tokenize edilmiş kayıtlı kart.

    ÖNEMLİ:

        Burada:
            - PAN / card number
            - CVC

        tutulmaz.

        Yalnızca provider'ın verdiği token ve kart metadata'sı tutulur.
    """

    payment_customer = models.ForeignKey(
        PaymentCustomer,
        on_delete=models.CASCADE,
        related_name="stored_cards",
        verbose_name="Ödeme Müşterisi",
    )

    # ==========================================================================
    # PROVIDER TOKEN
    # ==========================================================================

    provider_card_token = models.CharField(
        max_length=255,
        verbose_name="Provider Card Token",
    )

    # ==========================================================================
    # CARD METADATA
    # ==========================================================================

    card_alias = models.CharField(
        max_length=293,
        blank=True,
        default="",
        verbose_name="Kart Takma Adı",
    )

    bin_number = models.CharField(
        max_length=8,
        blank=True,
        default="",
        verbose_name="BIN",
    )

    last_four_digits = models.CharField(
        max_length=4,
        verbose_name="Son 4 Hane",
    )

    card_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Kart Tipi",
    )

    card_association = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Kart Markası",
    )

    card_family = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Kart Ailesi",
    )

    card_bank_code = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Banka Kodu",
    )

    card_bank_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="Banka Adı",
    )

    expire_month = models.CharField(
        max_length=2,
        blank=True,
        default="",
        verbose_name="Son Kullanma Ayı",
    )

    expire_year = models.CharField(
        max_length=4,
        blank=True,
        default="",
        verbose_name="Son Kullanma Yılı",
    )

    # ==========================================================================
    # LOCAL STATE
    # ==========================================================================

    is_default = models.BooleanField(
        default=False,
        verbose_name="Varsayılan Kart",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Aktif",
    )

    # ==========================================================================
    # TIMESTAMPS
    # ==========================================================================

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    class Meta:
        verbose_name = "Kayıtlı Kart"
        verbose_name_plural = "Kayıtlı Kartlar"

        ordering = [
            "-is_default",
            "-created_at",
            "-pk",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "payment_customer",
                    "provider_card_token",
                ],
                name="unique_stcard_token",
            ),
            models.UniqueConstraint(
                fields=["payment_customer"],
                condition=Q(
                    is_active=True,
                    is_default=True,
                ),
                name="unique_act_def_stcard",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "payment_customer",
                    "is_active",
                ],
                name="stcard_cust_active_idx",
            ),

            models.Index(
                fields=[
                    "payment_customer",
                    "is_default",
                ],
                name="stcard_cust_default_idx",
            ),
        ]

    def __str__(self):
        alias = self.card_alias.strip()

        if alias:
            return alias

        return (
            f"**** {self.last_four_digits}"
        )


class StoredCardOperation(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stored_card_operations",
    )

    provider = models.CharField(
        max_length=30,
        default="iyzico",
    )

    operation_type = models.CharField(
        max_length=20,
        choices=StoredCardOperationType.choices,
    )

    status = models.CharField(
        max_length=32,
        choices=StoredCardOperationStatus.choices,
        default=StoredCardOperationStatus.PENDING,
    )

    idempotency_key = models.CharField(
        max_length=128,
    )

    # HMAC-SHA256 hex digest. PAN/CVC are never persisted.
    request_fingerprint = models.CharField(
        max_length=64,
    )

    stored_card = models.ForeignKey(
        StoredCard,
        on_delete=models.PROTECT,
        related_name="operations",
        blank=True,
        null=True,
    )

    make_default = models.BooleanField(
        default=False,
    )

    provider_card_token = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    provider_customer_key = models.CharField(
        max_length=64,
        blank=True,
        null=True,
    )

    # Stored only when iyzico actually returns an externalId.
    provider_external_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    error_code = models.CharField(
        max_length=100,
        blank=True,
    )

    error_message = models.CharField(
        max_length=500,
        blank=True,
    )

    completed_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "provider",
                    "operation_type",
                    "idempotency_key",
                ],
                name="stcard_idmptncy_key",
            ),
            models.UniqueConstraint(
                fields=[
                    "user",
                    "provider",
                    "operation_type",
                ],
                condition=Q(
                    operation_type=StoredCardOperationType.CREATE,
                    status__in=[
                        StoredCardOperationStatus.PENDING,
                        StoredCardOperationStatus.PROVIDER_SUCCEEDED,
                        StoredCardOperationStatus.RECONCILIATION_REQUIRED,
                    ],
                ),
                name="active_stcard_create",
            ),
            models.UniqueConstraint(
                fields=[
                    "stored_card",
                    "provider",
                    "operation_type",
                ],
                condition=Q(
                    operation_type=StoredCardOperationType.DELETE,
                    status__in=[
                        StoredCardOperationStatus.PENDING,
                        StoredCardOperationStatus.PROVIDER_SUCCEEDED,
                        StoredCardOperationStatus.RECONCILIATION_REQUIRED,
                    ],
                ),
                name="active_stcard_delete",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        operation_type=StoredCardOperationType.CREATE,
                    )
                    | Q(
                        operation_type=StoredCardOperationType.DELETE,
                        stored_card__isnull=False,
                    )
                ),
                name="stcard_target_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "user",
                    "operation_type",
                    "status",
                    "created_at",
                ],
                name="op_user_state_idx",
            ),
            models.Index(
                fields=[
                    "stored_card",
                    "operation_type",
                    "status",
                ],
                name="op_card_state_idx",
            ),
        ]
        ordering = [
            "-created_at",
            "-pk",
        ]

    def __str__(self):
        return (
            f"{self.provider}:{self.operation_type}:"
            f"{self.idempotency_key}:{self.status}"
        )
# ==============================================================================
# 8. PAYMENT REFUND
# ==============================================================================


class PaymentRefund(models.Model):
    """
    PaymentTransaction üzerinden gerçekleştirilen refund kaydı.

    Bir payment birden fazla refund içerebilir.

    Örnek:

        Payment = 1.000 TL

        Refund #1 = 200 TL
        Refund #2 = 300 TL

        Toplam refund = 500 TL
    """

    payment_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name="Ödeme İşlemi",
    )

    # ==========================================================================
    # PROVIDER
    # ==========================================================================

    provider_refund_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Provider Refund ID",
    )

    refund_reference = models.CharField(
        max_length=100,
        unique=True,
        default=generate_refund_reference,
        editable=False,
        verbose_name="Refund Referansı",
    )

    # ==========================================================================
    # MONEY
    # ==========================================================================

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01")),
        ],
        verbose_name="İade Tutarı",
    )

    currency = models.CharField(
        max_length=3,
        default="TRY",
        verbose_name="Para Birimi",
    )

    # ==========================================================================
    # STATUS
    # ==========================================================================

    status = models.CharField(
        max_length=20,
        choices=RefundStatus.choices,
        default=RefundStatus.PENDING,
        db_index=True,
        verbose_name="İade Durumu",
    )

    reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="İade Nedeni",
    )

    # ==========================================================================
    # TIMESTAMPS
    # ==========================================================================

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Oluşturulma Tarihi",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Güncellenme Tarihi",
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="İade Tamamlanma Tarihi",
    )

    class Meta:
        verbose_name = "Ödeme İadesi"
        verbose_name_plural = "Ödeme İadeleri"

        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "payment_transaction",
                    "-created_at",
                ],
                name="refund_payment_created_idx",
            ),
            models.Index(
                fields=[
                    "status",
                    "-created_at",
                ],
                name="refund_status_created_idx",
            ),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "payment_transaction",
                    "provider_refund_id",
                ],
                condition=models.Q(
                    provider_refund_id__isnull=False,
                ),
                name="unique_provider_refund",
            ),

            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="refund_amount_gt_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.refund_reference} - "
            f"{self.amount} {self.currency}"
        )