from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from ..exceptions import (
    CancellationError,
    ReservationError,
)
from ..models import (
    Order,
    PaymentRefund,
    PaymentStatus,
    PaymentTransaction,
    RefundStatus,
    RefundType,
    SubOrder,
    SubOrderCancellation,
    SubOrderStatus,
)
from .stock_reservation import StockReservationService


class CancellationService:
    """
    SubOrder cancellation domain service.

    Sorumlulukları:
        - SubOrder cancellation state kontrolü
        - Ödeme işlemini doğrulama
        - Tüketilmiş stokların geri yüklenmesini başlatma
        - PaymentRefund kaydı oluşturma
        - SubOrderCancellation snapshot oluşturma
        - SubOrder -> CANCELLED geçişi

    Sorumlu olmadığı işler:
        - HTTP response / redirect
        - Django messages
        - Form validation
        - Provider refund HTTP çağrısı

    Not:
        Gerçek provider refund işlemi Step 36'da yapılacaktır.

    Transaction / lock sırası:

        Order
            ↓
        SubOrder
            ↓
        PaymentTransaction
            ↓
        StockReservation
    """

    CANCELLABLE_SUBORDER_STATES = {
        SubOrderStatus.PENDING,
        SubOrderStatus.PREPARING,
    }

    # ======================================================================
    # CANCEL SUBORDER
    # ======================================================================

    @classmethod
    @transaction.atomic
    def cancel_suborder(
        cls,
        *,
        suborder: SubOrder,
        cancelled_by=None,
        reason: str,
    ) -> SubOrderCancellation:
        """
        Ödenmiş bir SubOrder'ı iptal eder.

        Tek transaction içerisinde:

            1. Order lock
            2. SubOrder lock
            3. Başarılı PaymentTransaction doğrulama
            4. CONSUMED reservation'ları geri yükleme
            5. PaymentRefund(PENDING) oluşturma
            6. SubOrderCancellation oluşturma
            7. SubOrder -> CANCELLED

        Başarısız herhangi bir adımda bütün değişiklikler rollback olur.
        """

        # ------------------------------------------------------------------
        # Validate reason
        # ------------------------------------------------------------------

        reason = (reason or "").strip()

        if not reason:
            raise CancellationError(
                "İptal nedeni belirtilmelidir."
            )

        if len(reason) > 255:
            raise CancellationError(
                "İptal nedeni en fazla 255 karakter olabilir."
            )

        # ------------------------------------------------------------------
        # IMPORTANT:
        # Order'ı önce lock ediyoruz.
        #
        # StockReservationService'in lock sırası:
        #
        #     Order -> StoreProduct -> StockReservation
        #
        # CancellationService de bu sırayı bozmasın.
        # ------------------------------------------------------------------

        locked_order = (
            Order.objects
            .select_for_update()
            .get(pk=suborder.order_id)
        )

        # ------------------------------------------------------------------
        # SubOrder lock
        # ------------------------------------------------------------------

        locked_suborder = (
            SubOrder.objects
            .select_for_update()
            .select_related(
                "order",
                "store",
            )
            .get(
                pk=suborder.pk,
                order=locked_order,
            )
        )

        # ------------------------------------------------------------------
        # Already cancelled / inconsistent state
        # ------------------------------------------------------------------

        if locked_suborder.status == SubOrderStatus.CANCELLED:
            raise CancellationError(
                "Bu alt sipariş zaten iptal edilmiş."
            )

        if (
            SubOrderCancellation.objects
            .filter(suborder_id=locked_suborder.pk)
            .exists()
        ):
            raise CancellationError(
                "Bu alt sipariş için daha önce bir iptal kaydı oluşturulmuş."
            )

        # ------------------------------------------------------------------
        # SubOrder state
        # ------------------------------------------------------------------

        if (
            locked_suborder.status
            not in cls.CANCELLABLE_SUBORDER_STATES
        ):
            raise CancellationError(
                "Bu sipariş mevcut durumunda iptal edilemez."
            )

        # ------------------------------------------------------------------
        # Successful payment
        # ------------------------------------------------------------------

        payment_transaction = cls._get_successful_payment_transaction(
            order=locked_order,
        )

        # ------------------------------------------------------------------
        # Currency consistency
        # ------------------------------------------------------------------

        if locked_order.currency != payment_transaction.currency:
            raise CancellationError(
                "Sipariş ve ödeme para birimleri uyuşmuyor."
            )

        # ------------------------------------------------------------------
        # Refund amount
        #
        # Sadece bu SubOrder'ın snapshot toplamı iade edilir.
        #
        # Order.total_amount kullanılmaz.
        # ------------------------------------------------------------------

        refund_amount = locked_suborder.total_amount

        if refund_amount < Decimal("0.00"):
            raise CancellationError(
                "Geçersiz iade tutarı."
            )

        # ------------------------------------------------------------------
        # Refund limit validation
        #
        # Aynı PaymentTransaction üzerinden başka SubOrder'lar daha önce
        # refund edilmiş veya pending durumdaysa toplam refund,
        # paid_price'ı aşmamalı.
        # ------------------------------------------------------------------

        cls._validate_refund_amount(
            payment_transaction=payment_transaction,
            refund_amount=refund_amount,
        )

        # ------------------------------------------------------------------
        # Restore consumed stock
        #
        # StockReservationService:
        #
        #     CONSUMED
        #         ↓
        #     RELEASED
        #
        #     stock += quantity
        #     sold_count -= quantity
        #
        # CancellationService bu stok mantığını tekrar uygulamaz.
        # ------------------------------------------------------------------

        try:
            StockReservationService.restore_consumed_suborder(
                suborder=locked_suborder,
            )

        except ReservationError as exc:
            raise CancellationError(
                "İptal sırasında stok durumu güncellenemedi."
            ) from exc

        # ------------------------------------------------------------------
        # PaymentRefund
        #
        # Provider çağrısı YOK.
        #
        # Step 36'da:
        #
        #     PENDING -> provider refund
        #                  ↓
        #             SUCCESS / FAILED
        #
        # Eğer refund_amount == 0 ise PaymentRefund oluşturulmaz.
        # ------------------------------------------------------------------

        payment_refund = None

        if refund_amount > Decimal("0.00"):
            payment_refund = PaymentRefund.objects.create(
                payment_transaction=payment_transaction,
                suborder=locked_suborder,
                amount=refund_amount,
                currency=payment_transaction.currency,
                status=RefundStatus.PENDING,
                reason=reason,
                refund_type=RefundType.SUBORDER_CANCELLATION,
                refund_shipping=True,
            )

        # ------------------------------------------------------------------
        # Cancellation snapshot
        # ------------------------------------------------------------------

        cancellation = SubOrderCancellation.objects.create(
            suborder=locked_suborder,
            cancelled_by=cancelled_by,
            reason=reason,
            refund_amount=refund_amount,
            payment_refund=payment_refund,
        )

        # ------------------------------------------------------------------
        # SubOrder -> CANCELLED
        # ------------------------------------------------------------------

        locked_suborder.status = SubOrderStatus.CANCELLED
        locked_suborder.cancelled_at = timezone.now()

        locked_suborder.save(
            update_fields=[
                "status",
                "cancelled_at",
                "updated_at",
            ]
        )

        return cancellation

    # ======================================================================
    # SUCCESSFUL PAYMENT
    # ======================================================================

    @classmethod
    def _get_successful_payment_transaction(
        cls,
        *,
        order: Order,
    ) -> PaymentTransaction:
        """
        Order için tek başarılı PaymentTransaction döndürür.

        Birden fazla SUCCESS transaction olması veri tutarsızlığı kabul edilir.
        """

        transactions = list(
            PaymentTransaction.objects
            .select_for_update()
            .filter(
                order=order,
                status__in=[
                    PaymentStatus.SUCCESS,
                    PaymentStatus.PARTIALLY_REFUNDED,
                ],
            )
            .order_by("-id")[:2]
        )

        if not transactions:
            raise CancellationError(
                "Bu sipariş için başarılı bir ödeme bulunamadı."
            )

        if len(transactions) > 1:
            raise CancellationError(
                "Sipariş için birden fazla başarılı ödeme bulundu."
            )

        return transactions[0]

    # ======================================================================
    # REFUND VALIDATION
    # ======================================================================

    @classmethod
    def _validate_refund_amount(
        cls,
        *,
        payment_transaction,
        refund_amount,
    ):
        """
                Aynı payment transaction üzerindeki mevcut PENDING/SUCCESS
                refund'larla birlikte toplam tutarın paid_price'ı aşmasını engeller.
        """
        if refund_amount <= Decimal("0.00"):
            return
    
        already_refunded = (
            PaymentRefund.objects
            .filter(
                payment_transaction=payment_transaction,
                status__in=[
                    RefundStatus.PENDING,
                    RefundStatus.PROCESSING,
                    RefundStatus.SUCCESS,
                    RefundStatus.RECONCILIATION_REQUIRED,
                ],
            )
            .aggregate(total=Sum("amount"))
            .get("total")
            or Decimal("0.00")
        )
    
        paid_price = payment_transaction.paid_price
    
        if paid_price is None:
            raise CancellationError(
                "Başarılı ödemenin tutarı bulunamadı."
            )
    
        remaining_refundable = paid_price - already_refunded
    
        if refund_amount > remaining_refundable:
            raise CancellationError(
                "İade tutarı, kalan iade edilebilir ödeme tutarını aşıyor."
            )