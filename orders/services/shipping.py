from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.db import transaction
from django.utils import timezone

from orders.exceptions import ShippingOperationError
from orders.models import CargoCompany, SubOrder, SubOrderStatus

class ShippingService:
    """
    Verilen ara toplam için kargo ücretini hesaplar.

    Kurallar:
        - subtotal <= 0 TL ise kargo 0 TL.
        - subtotal 750 TL ve üzeriyse ücretsiz kargo.
        - subtotal 750 TL altındaysa standart kargo ücreti 99,99 TL.

    NOT:
        Bu service subtotal'ın Order'a mı yoksa SubOrder'a mı
        ait olduğunu bilmez.

        Sadece kendisine verilen subtotal üzerinden kargo
        ücretini hesaplar.

    Sorumlulukları:
        - Kargo ücretini hesaplamak.
        - SubOrder'ı kargoya vermek.
        - Kargo takip bilgilerini kaydetmek.
        - SubOrder'ı teslim edildi olarak işaretlemek.

    Sorumlu olmadığı işler:
        - Seller authorization.
        - HTTP response / redirect / message.
        - Kargo firması API entegrasyonu.
    """

    FREE_SHIPPING_THRESHOLD = Decimal("750.00")
    STANDARD_SHIPPING_FEE = Decimal("99.99")
    MONEY_QUANTUM = Decimal("0.01")

    @classmethod
    def calculate_shipping(cls, *, subtotal):
        """
        Verilen ara toplam için kargo ücretini hesaplar.

        Args:
            subtotal:
                Kargo hesabında kullanılacak ara toplam.

        Returns:
            Decimal:
                Kargo ücreti.
        """

        subtotal = cls._normalize_money(subtotal)

        # --------------------------------------------------------------
        # NO SELECTED PRODUCTS
        # --------------------------------------------------------------
        #
        # Sepette ürün olsa bile hiçbir ürün seçili değilse subtotal = 0
        # olur. Bu durumda kargo ücreti de hesaplanmaz.
        # --------------------------------------------------------------

        if subtotal <= Decimal("0.00"):
            return Decimal("0.00")

        # --------------------------------------------------------------
        # FREE SHIPPING
        # --------------------------------------------------------------

        if subtotal >= cls.FREE_SHIPPING_THRESHOLD:
            return Decimal("0.00")

        # --------------------------------------------------------------
        # STANDARD SHIPPING
        # --------------------------------------------------------------

        return cls.STANDARD_SHIPPING_FEE

    # ==========================================================================
    # SHIP SUBORDER
    # ==========================================================================

    @classmethod
    @transaction.atomic
    def ship_suborder(
        cls,
        *,
        suborder,
        cargo_company,
        cargo_tracking_number,
    ):
        """
        SubOrder'ı kargoya verir.

        İşlem:
            PREPARING
                ↓
            Kargo bilgilerini kaydet
                ↓
            shipped_at = now()
                ↓
            SHIPPED

        Tüm işlem tek transaction içerisindedir.
        """

        cargo_company = cargo_company.strip()
        cargo_tracking_number = cargo_tracking_number.strip()

        if cargo_company not in CargoCompany.values:
            raise ShippingOperationError(
                "Geçersiz kargo firması."
            )

        if not cargo_tracking_number:
            raise ShippingOperationError(
                "Kargo takip numarası girilmelidir."
            )

        if len(cargo_tracking_number) > 100:
            raise ShippingOperationError(
                "Kargo takip numarası çok uzun."
            )

        suborder = (
            SubOrder.objects
            .select_for_update()
            .get(pk=suborder.pk)
        )

        if suborder.status != SubOrderStatus.PREPARING:
            raise ShippingOperationError(
                "Yalnızca hazırlanıyor durumundaki siparişler "
                "kargoya verilebilir."
            )

        if suborder.shipped_at is not None:
            raise ShippingOperationError(
                "Bu sipariş zaten kargoya verilmiş."
            )

        suborder.cargo_company = cargo_company
        suborder.cargo_tracking_number = cargo_tracking_number
        suborder.shipped_at = timezone.now()
        suborder.status = SubOrderStatus.SHIPPED

        suborder.save(
            update_fields=[
                "cargo_company",
                "cargo_tracking_number",
                "shipped_at",
                "status",
                "updated_at",
            ]
        )

        return suborder

    # ==========================================================================
    # MARK DELIVERED
    # ==========================================================================

    @classmethod
    @transaction.atomic
    def mark_suborder_delivered(
        cls,
        *,
        suborder,
    ):
        """
        SubOrder'ı teslim edildi olarak işaretler.

        İşlem:
            SHIPPED
                ↓
            delivered_at = now()
                ↓
            DELIVERED
        """

        suborder = (
            SubOrder.objects
            .select_for_update()
            .get(pk=suborder.pk)
        )

        if suborder.status != SubOrderStatus.SHIPPED:
            raise ShippingOperationError(
                "Yalnızca kargolanmış siparişler "
                "teslim edildi olarak işaretlenebilir."
            )

        if suborder.delivered_at is not None:
            raise ShippingOperationError(
                "Bu sipariş zaten teslim edilmiş."
            )

        suborder.delivered_at = timezone.now()
        suborder.status = SubOrderStatus.DELIVERED

        suborder.save(
            update_fields=[
                "delivered_at",
                "status",
                "updated_at",
            ]
        )

        return suborder

    # ==========================================================================
    # MONEY
    # ==========================================================================

    @classmethod
    def _normalize_money(cls, value):
        """
        Para değerini güvenli şekilde Decimal'e çevirir.
        """

        try:
            amount = Decimal(str(value))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ShippingOperationError(
                "Geçersiz para değeri."
            ) from exc

        return amount.quantize(
            cls.MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )