import hmac
import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4
import iyzipay
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ..models import (
    Order,
    PaymentRefund,
    PaymentRefundItem,
    PaymentTransaction,
    PaymentTransactionItem,
    PaymentTransactionItemType,
    SubOrder,
    PaymentStatus,
    RefundStatus,
    RefundType,
    SubOrderStatus,
    ReturnRequest,
    ReturnStatus,
    SubOrderItemCancellation,
)



class RefundError(Exception):
    """Ödeme iadesi gerçekleştirilemedi."""


class RefundService:
    PROVIDER = "iyzico"

    # PaymentRefund (parent) ve PaymentRefundItem (child) için
    # tutar kapasitesi aynı iş kuralını izler; FAILED parent kayıtlarının
    # child breakdown'ı ayrıca değerlendirilir.
    RESERVED_REFUND_STATUSES = {
        RefundStatus.PENDING,
        RefundStatus.PROCESSING,
        RefundStatus.SUCCESS,
        RefundStatus.RECONCILIATION_REQUIRED,
    }

    RESERVED_ITEM_STATUSES = {
        RefundStatus.PENDING,
        RefundStatus.PROCESSING,
        RefundStatus.SUCCESS,
        RefundStatus.RECONCILIATION_REQUIRED,
    }

    PROCESSING_TIMEOUT_MINUTES = 15

    @classmethod
    def process_refund(cls, *, payment_refund_id):
        """
        Logical PaymentRefund kaydındaki bekleyen refund item'larını
        provider tarafında işler.

        Provider HTTP çağrıları DB transaction dışında gerçekleştirilir.

        Provider çağrısından sonra local DB'ye sonuç yazılırken
        beklenmeyen bir hata oluşursa refund item'ı
        RECONCILIATION_REQUIRED durumuna alınır.
        """

        attempted_failed_item_ids = set()

        while True:
            try:
                claim = cls._claim_next_item(
                    payment_refund_id=payment_refund_id,
                    skip_retryable_item_ids=attempted_failed_item_ids,
                )
            except RefundError:
                # A reconciliation-required refund is deliberately not retried.
                # Propagate the domain error so callers/tests can distinguish an
                # unresolved provider result from a completed refund.
                raise

            if claim is None:
                return cls._get_refund(
                    payment_refund_id=payment_refund_id,
                )

            try:
                response = cls._call_provider(
                    payment_transaction_id=claim["provider_transaction_id"],
                    amount=claim["amount"],
                    currency=claim["currency"],
                    conversation_id=claim["conversation_id"],
                    reason=claim["reason"],
                )

            except Exception as exc:
                cls._mark_reconciliation_required(
                    refund_item_id=claim["refund_item_id"],
                    message=str(exc),
                )

                return cls._get_refund(
                    payment_refund_id=payment_refund_id,
                )

            try:
                result_status = cls._apply_provider_response(
                    refund_item_id=claim["refund_item_id"],
                    response=response,
                    expected_amount=claim["amount"],
                    expected_currency=claim["currency"],
                    expected_payment_transaction_id=(
                        claim["provider_transaction_id"]
                    ),
                    expected_payment_id=claim["provider_payment_id"],
                    expected_conversation_id=claim["conversation_id"],
                )

            except Exception as exc:
                cls._mark_reconciliation_required(
                    refund_item_id=claim["refund_item_id"],
                    message=(
                        "Provider refund sonucu local veritabanına "
                        f"uygulanamadı: {exc}"
                    ),
                )

                return cls._get_refund(
                    payment_refund_id=payment_refund_id,
                )

            if result_status == RefundStatus.RECONCILIATION_REQUIRED:
                return cls._get_refund(
                    payment_refund_id=payment_refund_id,
                )

            if result_status == RefundStatus.FAILED:
                attempted_failed_item_ids.add(claim["refund_item_id"])
                current_refund = cls._get_refund(
                    payment_refund_id=payment_refund_id,
                )
                if current_refund.status == RefundStatus.RECONCILIATION_REQUIRED:
                    return current_refund

                # A failed child must not block independent PENDING children.
                # Retryable failures can also be tried once per invocation,
                # but the same child is not immediately retried in a tight loop.
                has_pending_items = PaymentRefundItem.objects.filter(
                    payment_refund_id=payment_refund_id,
                    status=RefundStatus.PENDING,
                ).exists()
                has_unattempted_retryable_items = (
                    PaymentRefundItem.objects
                    .filter(
                        payment_refund_id=payment_refund_id,
                        status=RefundStatus.FAILED,
                        retryable=True,
                    )
                    .exclude(pk__in=attempted_failed_item_ids)
                    .exists()
                )
                if has_pending_items or has_unattempted_retryable_items:
                    continue
                return current_refund


    @classmethod
    @transaction.atomic
    def _claim_next_item(
        cls,
        *,
        payment_refund_id,
        skip_retryable_item_ids=None,
    ):
        # Önce kilitsiz bir okuma yaparak hangi kayıtların
        # kilitleneceğini belirliyoruz.
        refund_reference = (
            PaymentRefund.objects
            .select_related("payment_transaction")
            .get(pk=payment_refund_id)
        )

        order_id = refund_reference.payment_transaction.order_id
        suborder_id = refund_reference.suborder_id
        payment_transaction_id = (
            refund_reference.payment_transaction_id
        )

        # Ortak kilit sırası:
        # Order -> SubOrder -> PaymentTransaction
        # -> PaymentRefund -> PaymentRefundItem

        order = (
            Order.objects
            .select_for_update()
            .get(pk=order_id)
        )

        suborder = (
            SubOrder.objects
            .select_for_update()
            .get(
                pk=suborder_id,
                order_id=order.pk,
            )
        )

        payment_transaction = (
            PaymentTransaction.objects
            .select_for_update()
            .get(
                pk=payment_transaction_id,
                order_id=order.pk,
            )
        )

        refund = (
            PaymentRefund.objects
            .select_for_update()
            .get(pk=payment_refund_id)
        )

        # Kilitler alınana kadar ilişkiler değişmiş olabileceğinden
        # ilk okunan kimlikleri tekrar doğruluyoruz.
        if (
            refund.payment_transaction_id
            != payment_transaction.pk
            or refund.suborder_id != suborder.pk
        ):
            raise RefundError(
                "İade kaydının ilişkileri işlem sırasında değişti."
            )

        cls._validate_refund(
            refund=refund,
            order=order,
            suborder=suborder,
            payment_transaction=payment_transaction,
        )

        if refund.status == RefundStatus.SUCCESS:
            return None

        cls._ensure_refund_items(
            refund=refund,
            suborder=suborder,
            payment_transaction=payment_transaction,
        )

        recovered_stale_items = cls._recover_stale_processing_items(
            refund=refund,
        )

        if recovered_stale_items:
            # Bir child refund'ın provider sonucu belirsizse aynı parent
            # altındaki diğer refund'ları otomatik göndermeyi durdur.
            cls._sync_refund_status(refund=refund)
            return None

        # Legacy/tutarsız kayıtlarda child RECONCILIATION_REQUIRED olduğu
        # halde parent PENDING kalmış olabilir. Bu durumda da yeni provider
        # çağrısı başlatma.
        has_unresolved_item = PaymentRefundItem.objects.filter(
            payment_refund=refund,
            status=RefundStatus.RECONCILIATION_REQUIRED,
        ).exists()
        if has_unresolved_item:
            cls._sync_refund_status(refund=refund)
            return None

        # Finish each never-attempted PENDING child before retrying an older
        # retryable failure. Otherwise a low-PK failure could starve all later
        # refund items because process_refund intentionally returns after a
        # failed provider attempt to avoid tight retries.
        claimable_items = (
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_transaction_item")
            .filter(payment_refund=refund)
        )
        refund_item = (
            claimable_items
            .filter(status=RefundStatus.PENDING)
            .order_by("pk")
            .first()
        )
        if refund_item is None:
            retryable_items = claimable_items.filter(
                status=RefundStatus.FAILED,
                retryable=True,
            )
            if skip_retryable_item_ids:
                retryable_items = retryable_items.exclude(
                    pk__in=skip_retryable_item_ids
                )
            refund_item = retryable_items.order_by("pk").first()

        if refund_item is None:
            cls._sync_refund_status(refund=refund)

            cls._sync_payment_transaction_status(
                payment_transaction=payment_transaction,
            )

            return None

        refund_item.status = RefundStatus.PROCESSING
        refund_item.retryable = None
        refund_item.completed_at = None
        refund_item.save(
            update_fields=[
                "status",
                "retryable",
                "completed_at",
                "updated_at",
            ]
        )

        transaction_item = refund_item.payment_transaction_item

        return {
            "refund_item_id": refund_item.pk,
            "provider_transaction_id": (
                transaction_item.provider_transaction_id
            ),
            "provider_payment_id": payment_transaction.payment_id,
            "amount": refund_item.amount,
            "currency": refund.currency,
            "conversation_id": refund_item.conversation_id,
            "reason": refund.reason or "customer_request",
        }


    @classmethod
    def _calculate_unit_refund_amount(
        cls,
        *,
        paid_price,
        quantity,
        unit_number,
    ):
        """
        Satırın tahsil edilen toplam tutarını ürün birimlerine
        kuruş hassasiyetinde, deterministik olarak dağıtır.

        Örnek:
            100.01 TL / 2 adet
            Birim 1: 50.01 TL
            Birim 2: 50.00 TL
        """
        if quantity < 1 or not 1 <= unit_number <= quantity:
            raise RefundError(
                "İade edilecek ürün birimi geçersiz."
            )

        total_cents = int(
            cls._money(paid_price) * Decimal("100")
        )

        base_cents, remainder_cents = divmod(
            total_cents,
            quantity,
        )

        unit_cents = base_cents + (
            1 if unit_number <= remainder_cents else 0
        )

        return (
            Decimal(unit_cents) / Decimal("100")
        ).quantize(Decimal("0.01"))


    @classmethod
    def _validate_refund(
        cls,
        *,
        refund,
        order,
        suborder,
        payment_transaction,
    ):
        if refund.payment_transaction_id != payment_transaction.pk:
            raise RefundError(
                "İade ile ödeme işlemi eşleşmiyor."
            )

        if refund.suborder_id != suborder.pk:
            raise RefundError(
                "İade ile alt sipariş eşleşmiyor."
            )

        if suborder.order_id != order.pk:
            raise RefundError(
                "Alt sipariş ana sipariş ile eşleşmiyor."
            )

        if payment_transaction.order_id != order.pk:
            raise RefundError(
                "Ödeme işlemi ana sipariş ile eşleşmiyor."
            )

        if payment_transaction.provider != cls.PROVIDER:
            raise RefundError(
                "Bu ödeme sağlayıcısı için refund desteği bulunmuyor."
            )

        if refund.currency != payment_transaction.currency:
            raise RefundError(
                "İade ve ödeme para birimleri uyuşmuyor."
            )

        if refund.amount is None or refund.amount <= Decimal("0.00"):
            raise RefundError(
                "İade tutarı sıfırdan büyük olmalıdır."
            )

        # --------------------------------------------------------------
        # Refund type and domain state
        # --------------------------------------------------------------

        if refund.refund_type == RefundType.SUBORDER_CANCELLATION:
            if suborder.status != SubOrderStatus.CANCELLED:
                raise RefundError(
                    "Tam alt sipariş iptalinde alt sipariş "
                    "CANCELLED durumunda olmalıdır."
                )

        elif (
            refund.refund_type
            == RefundType.SELLER_PARTIAL_CANCELLATION
        ):
            if suborder.status not in {
                SubOrderStatus.PENDING,
                SubOrderStatus.PREPARING,
                SubOrderStatus.CANCELLED,
            }:
                raise RefundError(
                    "Satıcı kısmi iptali mevcut sipariş "
                    "durumunda işlenemez."
                )

            if not SubOrderItemCancellation.objects.filter(
                payment_refund_id=refund.pk,
                order_item__sub_order_id=suborder.pk,
            ).exists():
                raise RefundError(
                    "Kısmi iptal ile ilişkili iptal kaydı bulunamadı."
                )

        elif refund.refund_type in (
            RefundType.CUSTOMER_REQUEST,
            RefundType.SELLER_FAULT,
        ):
            if suborder.status != SubOrderStatus.DELIVERED:
                raise RefundError(
                    "Ürün iadesi için alt sipariş DELIVERED "
                    "durumunda olmalıdır."
                )

            return_request = (
                ReturnRequest.objects
                .filter(payment_refund_id=refund.pk)
                .select_related("order_item")
                .first()
            )

            if return_request is None:
                raise RefundError(
                    "Müşteri iadesiyle ilişkili iade talebi bulunamadı."
                )

            if return_request.order_item.sub_order_id != suborder.pk:
                raise RefundError(
                    "İade talebi ile ödeme iadesinin alt siparişleri "
                    "eşleşmiyor."
                )

            if return_request.status not in (
                ReturnStatus.REFUND_PENDING,
                ReturnStatus.COMPLETED,
            ):
                raise RefundError(
                    "İade talebi para iadesi işlemine uygun durumda değil."
                )

        else:
            raise RefundError("Desteklenmeyen refund türü.")

        # --------------------------------------------------------------
        # Shipping transaction item validation
        # --------------------------------------------------------------

        # if refund.refund_shipping:
        #     shipping_items = list(
        #         PaymentTransactionItem.objects.filter(
        #             payment_transaction_id=payment_transaction.pk,
        #             suborder_id=suborder.pk,
        #             item_type=PaymentTransactionItemType.SHIPPING,
        #         ).order_by("pk")[:2]
        #     )

        #     if (
        #         len(shipping_items) != 1
        #         or shipping_items[0].order_item_id is not None
        #     ):
        #         raise RefundError(
        #             "Kargo iadesi için tek ve geçerli bir "
        #             "ödeme kalemi bulunamadı."
        #         )

        # --------------------------------------------------------------
        # Logical refund state
        # --------------------------------------------------------------

        if refund.status == RefundStatus.SUCCESS:
            return

        if refund.status == RefundStatus.RECONCILIATION_REQUIRED:
            raise RefundError(
                "Bu iade için önce provider mutabakatı yapılmalıdır."
            )

        if not cls._clean(payment_transaction.payment_id):
            raise RefundError(
                "Ödeme işleminde iyzico payment_id bilgisi bulunamadı."
            )

        # --------------------------------------------------------------
        # Payment transaction state
        # --------------------------------------------------------------

        if payment_transaction.status not in {
            PaymentStatus.SUCCESS,
            PaymentStatus.PARTIALLY_REFUNDED,
        }:
            raise RefundError(
                "Bu ödeme işlemi iade edilebilir durumda değil."
            )


    @staticmethod
    def _refund_item_reserves_amount(refund_item):
        return (
            refund_item.status in RefundService.RESERVED_ITEM_STATUSES
            or (
                refund_item.status == RefundStatus.FAILED
                and refund_item.retryable is True
            )
        )

    @classmethod
    def validate_payment_transaction_refund_capacity(
        cls,
        *,
        payment_transaction,
        refund_amount,
        exclude_refund_id=None,
    ):
        """Validate the transaction-wide refund capacity.

        Call within an atomic operation after the parent Order row is locked;
        cancellation, return approval, and refund processing share that lock
        to serialize capacity decisions for the same order.
        """
        requested_amount = cls._money(refund_amount)

        if requested_amount < Decimal("0.00"):
            raise RefundError("İade tutarı negatif olamaz.")

        if requested_amount == Decimal("0.00"):
            return

        if payment_transaction.paid_price is None:
            raise RefundError("Başarılı ödemenin tutarı bulunamadı.")

        paid_price = cls._money(payment_transaction.paid_price)
        if paid_price <= Decimal("0.00"):
            raise RefundError("Başarılı ödemenin tutarı geçersiz.")

        # Lock parent rows first, then child rows. This matches the lock order
        # used by _claim_next_item() and _apply_provider_response().
        parent_query = (
            PaymentRefund.objects
            .select_for_update()
            .filter(
                payment_transaction_id=payment_transaction.pk,
                status__in=(
                    cls.RESERVED_REFUND_STATUSES
                    | {RefundStatus.FAILED}
                ),
            )
            .order_by("pk")
        )
        if exclude_refund_id is not None:
            parent_query = parent_query.exclude(pk=exclude_refund_id)
        parent_rows = list(parent_query)

        for parent in parent_rows:
            if parent.amount is None or cls._money(parent.amount) <= Decimal("0.00"):
                raise RefundError(
                    "Rezerve/FAILED refund kaydının tutarı geçersiz; "
                    "manuel mutabakat gerekli."
                )

        failed_parent_ids = [
            parent.pk
            for parent in parent_rows
            if parent.status == RefundStatus.FAILED
        ]
        failed_child_rows = list(
            PaymentRefundItem.objects
            .select_for_update()
            .filter(payment_refund_id__in=failed_parent_ids)
            .order_by("pk")
        )

        failed_child_amounts_by_parent = defaultdict(
            lambda: Decimal("0.00")
        )
        failed_item_total = Decimal("0.00")
        for item in failed_child_rows:
            if item.amount is None or cls._money(item.amount) <= Decimal("0.00"):
                raise RefundError(
                    "FAILED refund altındaki refund item tutarı geçersiz; "
                    "manuel mutabakat gerekli."
                )
            item_amount = cls._money(item.amount)
            failed_child_amounts_by_parent[item.payment_refund_id] += item_amount
            if cls._refund_item_reserves_amount(item):
                failed_item_total += item_amount

        reserved_refund_total = sum(
            (
                cls._money(parent.amount)
                for parent in parent_rows
                if parent.status in cls.RESERVED_REFUND_STATUSES
            ),
            Decimal("0.00"),
        )

        # A FAILED parent with no child breakdown is ambiguous. If it has a
        # partial child breakdown, reserve the unexplained parent remainder.
        # This prevents legacy/inconsistent data from opening refund capacity.
        failed_parent_without_items_total = Decimal("0.00")
        failed_parent_missing_breakdown_total = Decimal("0.00")
        for parent in parent_rows:
            if parent.status != RefundStatus.FAILED:
                continue
            child_total = failed_child_amounts_by_parent.get(
                parent.pk,
                Decimal("0.00"),
            )
            parent_amount = cls._money(parent.amount)
            if child_total > parent_amount:
                raise RefundError(
                    "FAILED refund child kalemlerinin toplamı üst refund "
                    "tutarını aşıyor; manuel mutabakat gerekli."
                )
            if child_total == Decimal("0.00"):
                failed_parent_without_items_total += parent_amount
            elif child_total < parent_amount:
                failed_parent_missing_breakdown_total += (
                    parent_amount - child_total
                )

        already_reserved = cls._money(
            reserved_refund_total
            + failed_item_total
            + failed_parent_without_items_total
            + failed_parent_missing_breakdown_total
        )
        remaining = cls._money(paid_price - already_reserved)

        if remaining < Decimal("0.00"):
            raise RefundError(
                "Kayıtlardaki iade tutarları tahsil edilen tutarı aşıyor; "
                "manuel mutabakat gerekli."
            )

        if requested_amount > remaining:
            raise RefundError(
                "İade tutarı, kalan iade edilebilir ödeme tutarını aşıyor."
            )

    @classmethod
    def get_reserved_payment_item_refund_amount(
        cls,
        *,
        transaction_item,
        exclude_refund_id=None,
    ):
        """Return the amount already reserved against one payment item.

        The parent Order must already be locked by the caller. Legacy parent
        refunds without any child breakdown are estimated from the refund type;
        a parent with a partial or inconsistent child breakdown is rejected
        rather than allowing its unrepresented amount to free capacity.
        """
        parent_statuses = cls.RESERVED_REFUND_STATUSES | {
            RefundStatus.FAILED,
        }
        parent_query = (
            PaymentRefund.objects
            .select_for_update()
            .select_related("suborder")
            .filter(
                payment_transaction_id=transaction_item.payment_transaction_id,
                suborder_id=transaction_item.suborder_id,
                status__in=parent_statuses,
            )
            .order_by("pk")
        )
        if exclude_refund_id is not None:
            parent_query = parent_query.exclude(pk=exclude_refund_id)
        parent_rows = list(parent_query)
        parent_by_id = {parent.pk: parent for parent in parent_rows}

        # Validate complete breakdowns for candidate parent refunds. In normal
        # flows child creation is atomic; a partly present breakdown signals
        # legacy/corrupt data and must not be used to infer free capacity.
        all_parent_items = list(
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_refund", "payment_transaction_item")
            .filter(payment_refund_id__in=parent_by_id)
            .order_by("pk")
        )
        amount_by_parent = defaultdict(lambda: Decimal("0.00"))
        child_count_by_parent = defaultdict(int)
        for item in all_parent_items:
            parent = item.payment_refund
            linked_payment_item = item.payment_transaction_item
            if (
                parent.payment_transaction_id
                != linked_payment_item.payment_transaction_id
                or parent.suborder_id != linked_payment_item.suborder_id
            ):
                raise RefundError(
                    "Refund alt kalemi farklı bir ödeme/alt siparişe bağlı; "
                    "manuel mutabakat gerekli."
                )
            if item.amount is None or cls._money(item.amount) <= Decimal("0.00"):
                raise RefundError(
                    "Mevcut refund item tutarı geçersiz; manuel mutabakat gerekli."
                )
            amount_by_parent[parent.pk] += cls._money(item.amount)
            child_count_by_parent[parent.pk] += 1

        for parent in parent_rows:
            child_count = child_count_by_parent.get(parent.pk, 0)
            if child_count and cls._money(amount_by_parent[parent.pk]) != cls._money(parent.amount):
                raise RefundError(
                    "Mevcut refund alt kalemlerinin toplamı üst refund tutarıyla "
                    "eşleşmiyor; manuel mutabakat gerekli."
                )

        # Load all child rows for this specific provider payment item too,
        # including rows whose parent is in an older terminal state. A child
        # SUCCESS remains real refunded money regardless of parent status.
        item_query = (
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_refund", "payment_transaction_item")
            .filter(payment_transaction_item_id=transaction_item.pk)
            .order_by("pk")
        )
        if exclude_refund_id is not None:
            item_query = item_query.exclude(payment_refund_id=exclude_refund_id)
        item_rows = list(item_query)

        represented_refund_ids = set()
        total = Decimal("0.00")
        for item in item_rows:
            parent = item.payment_refund
            if (
                parent.payment_transaction_id
                != transaction_item.payment_transaction_id
                or parent.suborder_id != transaction_item.suborder_id
            ):
                raise RefundError(
                    "Ödeme kalemindeki refund başka bir ödeme/alt siparişe ait; "
                    "manuel mutabakat gerekli."
                )
            represented_refund_ids.add(item.payment_refund_id)
            if not cls._refund_item_reserves_amount(item):
                continue
            if item.amount is None or cls._money(item.amount) <= Decimal("0.00"):
                raise RefundError(
                    "Rezerve durumdaki refund item tutarı geçersiz; "
                    "manuel mutabakat gerekli."
                )
            total += cls._money(item.amount)

        for parent_refund in parent_rows:
            if parent_refund.pk in represented_refund_ids:
                continue

            # Old partial-cancellation parents only cover product rows that
            # are actually linked to their cancellation records, not every
            # product in the same SubOrder.
            if parent_refund.refund_type == RefundType.SELLER_PARTIAL_CANCELLATION:
                has_cancellation_records = SubOrderItemCancellation.objects.filter(
                    payment_refund_id=parent_refund.pk,
                    order_item__sub_order_id=parent_refund.suborder_id,
                ).exists()
                if not has_cancellation_records:
                    raise RefundError(
                        "Kısmi iptal refund kaydının iptal satırları bulunamadı; "
                        "manuel mutabakat gerekli."
                    )

                if transaction_item.item_type == PaymentTransactionItemType.PRODUCT:
                    includes_product = SubOrderItemCancellation.objects.filter(
                        payment_refund_id=parent_refund.pk,
                        order_item_id=transaction_item.order_item_id,
                    ).exists()
                    if not includes_product:
                        continue
                elif not parent_refund.refund_shipping:
                    continue

            expected_amount = cls._get_refund_item_amount(
                refund=parent_refund,
                transaction_item=transaction_item,
                suborder=parent_refund.suborder,
            )
            if expected_amount > Decimal("0.00"):
                total += expected_amount

        return cls._money(total)

    @classmethod
    def validate_payment_item_refund_capacity(
        cls,
        *,
        transaction_item,
        proposed_amount,
        exclude_refund_id=None,
    ):
        """Aynı PaymentTransactionItem'a bağlı bütün refund kayıtlarının
        toplamını, o kalemin paid_price değeriyle karşılaştırır."""
        proposed = cls._money(proposed_amount)
        if proposed < Decimal("0.00"):
            raise RefundError("İade kalemi tutarı negatif olamaz.")
        if proposed == Decimal("0.00"):
            return

        max_refundable = cls._get_item_refundable_limit(
            transaction_item=transaction_item,
        )
        already_reserved = cls.get_reserved_payment_item_refund_amount(
            transaction_item=transaction_item,
            exclude_refund_id=exclude_refund_id,
        )

        if already_reserved + proposed > max_refundable:
            raise RefundError(
                "Aynı ödeme kalemine bağlı refund kayıtlarının toplamı, "
                "ödeme kaleminin tahsil edilen tutarını aşıyor."
            )

    @classmethod
    def _ensure_refund_items(
        cls,
        *,
        refund,
        suborder,
        payment_transaction,
    ):
        transaction_items = (
            PaymentTransactionItem.objects
            .select_for_update()
            .filter(
                payment_transaction_id=refund.payment_transaction_id,
                suborder_id=refund.suborder_id,
            )
            .order_by("pk")
        )


        if refund.refund_type == RefundType.SUBORDER_CANCELLATION:
            # İptal akışındaki mevcut davranış korunur.
            transaction_items = transaction_items


        elif (
            refund.refund_type
            == RefundType.SELLER_PARTIAL_CANCELLATION
        ):
            cancellations = list(
                SubOrderItemCancellation.objects
                .filter(
                    payment_refund_id=refund.pk,
                    order_item__sub_order_id=suborder.pk,
                )
                .order_by("id")
            )

            if not cancellations:
                raise RefundError(
                    "Kısmi iptal için ürün iptal kaydı bulunamadı."
                )

            order_item_ids = {
                cancellation.order_item_id
                for cancellation in cancellations
            }

            item_filter = Q(
                item_type=PaymentTransactionItemType.PRODUCT,
                order_item_id__in=order_item_ids,
            )

            if refund.refund_shipping:
                item_filter |= Q(
                    item_type=PaymentTransactionItemType.SHIPPING,
                    suborder_id=suborder.pk,
                )

            transaction_items = transaction_items.filter(item_filter)

        elif refund.refund_type in (
            RefundType.CUSTOMER_REQUEST,
            RefundType.SELLER_FAULT,
        ):
            return_request = (
                ReturnRequest.objects
                .filter(payment_refund_id=refund.pk)
                .select_related("order_item")
                .first()
            )

            if return_request is None:
                raise RefundError(
                    "İade kalemini hesaplamak için iade talebi bulunamadı."
                )

            product_filter = Q(
                item_type=PaymentTransactionItemType.PRODUCT,
                order_item_id=return_request.order_item_id,
            )

            if refund.refund_shipping:
                transaction_items = transaction_items.filter(
                    product_filter
                    | Q(
                        item_type=PaymentTransactionItemType.SHIPPING,
                        order_item__isnull=True,
                    )
                )
            else:
                transaction_items = transaction_items.filter(product_filter)

        else:
            raise RefundError("Desteklenmeyen refund türü.")

        transaction_items = list(transaction_items.order_by("pk"))

        if not transaction_items:
            raise RefundError(
                "Alt sipariş için ödeme kırılımı bulunamadı."
            )

        if refund.refund_shipping:
            shipping_items = [
                item for item in transaction_items
                if item.item_type == PaymentTransactionItemType.SHIPPING
            ]
            if (
                len(shipping_items) != 1
                or shipping_items[0].order_item_id is not None
            ):
                raise RefundError(
                    "Kargo iadesi için tek ve geçerli bir "
                    "ödeme kalemi bulunamadı."
                )

        existing_refund_item_rows = list(
            PaymentRefundItem.objects
            .select_for_update()
            .filter(payment_refund=refund)
            .order_by("pk")
        )
        existing_refund_items = {}
        for item in existing_refund_item_rows:
            if item.payment_transaction_item_id in existing_refund_items:
                raise RefundError(
                    "Aynı refund içinde aynı ödeme kalemi için birden fazla "
                    "refund item kaydı bulundu."
                )
            existing_refund_items[item.payment_transaction_item_id] = item

        expected_amounts = {
            transaction_item.pk: cls._get_refund_item_amount(
                refund=refund,
                transaction_item=transaction_item,
                suborder=suborder,
            )
            for transaction_item in transaction_items
        }

        expected_item_ids = {
            item_id
            for item_id, amount in expected_amounts.items()
            if amount > Decimal("0.00")
        }

        unexpected_item_ids = (
            set(existing_refund_items) - expected_item_ids
        )

        if unexpected_item_ids:
            raise RefundError(
                "Mevcut refund kalemleri beklenen ödeme "
                "kalemleriyle eşleşmiyor."
            )

        expected_total = Decimal("0.00")

        for transaction_item in transaction_items:
            expected_amount = expected_amounts[transaction_item.pk]

            if expected_amount <= Decimal("0.00"):
                continue

            expected_total += expected_amount

            if not cls._clean(transaction_item.provider_transaction_id):
                raise RefundError(
                    "İade edilecek ödeme kaleminin sağlayıcı işlem kimliği "
                    "bulunamadı."
                )

            max_refundable = cls._get_item_refundable_limit(
                transaction_item=transaction_item,
            )
            if expected_amount > max_refundable:
                raise RefundError(
                    "İade tutarı ödeme kaleminin "
                    "iade edilebilir tutarını aşıyor."
                )

            existing = existing_refund_items.get(
                transaction_item.pk
            )

            if existing is not None and cls._money(existing.amount) != expected_amount:
                raise RefundError(
                    "Mevcut refund kaleminin tutarı "
                    "beklenen iade tutarıyla eşleşmiyor."
                )

            reserved_elsewhere = cls.get_reserved_payment_item_refund_amount(
                transaction_item=transaction_item,
                exclude_refund_id=refund.pk,
            )
            this_refund_reserved_amount = Decimal("0.00")
            if existing is None:
                # Yeni oluşturulacak child PENDING olacağından hemen rezerve sayılır.
                this_refund_reserved_amount = expected_amount
            elif cls._refund_item_reserves_amount(existing):
                this_refund_reserved_amount = cls._money(existing.amount)

            if reserved_elsewhere + this_refund_reserved_amount > max_refundable:
                raise RefundError(
                    "Aynı ödeme kalemine bağlı refund kayıtlarının toplamı, "
                    "ödeme kaleminin tahsil edilen tutarını aşıyor."
                )

            if existing is not None:
                continue

            # Normal akışta bütün child item'lar parent refund PENDING
            # iken tek transaction'da oluşturulur. FAILED/SUCCESS gibi
            # terminal bir parent'ta eksik child'ı otomatik üretmek yeni
            # bir provider çağrısını yanlışlıkla başlatabilir.
            if refund.status != RefundStatus.PENDING:
                raise RefundError(
                    "PENDING durumunda olmayan refund kaydında eksik "
                    "ödeme kalemi bulundu; manuel mutabakat gerekli."
                )

            conversation_id = (
                f"RF-{refund.refund_reference}-"
                f"{uuid4().hex[:24]}"
            )

            PaymentRefundItem.objects.create(
                payment_refund=refund,
                payment_transaction_item=transaction_item,
                amount=expected_amount,
                status=RefundStatus.PENDING,
                conversation_id=conversation_id,
            )

        expected_total = cls._money(
            expected_total
        )

        if expected_total != cls._money(refund.amount):
            raise RefundError(
                "Refund kalemleri toplamı ile "
                "refund toplamı eşleşmiyor."
            )

    @classmethod
    def _get_refund_item_amount(
        cls,
        *,
        refund,
        transaction_item,
        suborder,
    ):
        """
        Refund policy'ye göre bu payment transaction item'ından
        ne kadar iade edileceğini belirler.

        Product:
            refund'a dahilse kendi paid_price'ı.

        Shipping:
            refund.refund_shipping=True ise shipping paid_price'ı.

        Buradaki amount business refund tutarıdır.
        Provider'daki maksimum iade edilebilir tutar değildir.
        """

        item_type = transaction_item.item_type

        if transaction_item.suborder_id != suborder.pk:
            raise RefundError(
                "Ödeme kalemi farklı bir alt siparişe ait."
            )

        # Alt sipariş iptali: mevcut davranışı koru.
        # --------------------------------------------------------------
        # PRODUCT
        # --------------------------------------------------------------
        if item_type == PaymentTransactionItemType.PRODUCT:
            if transaction_item.paid_price is None:
                raise RefundError(
                    "Ürün ödeme kaleminin tahsil edilen tutarı bulunamadı."
                )
            if cls._money(transaction_item.paid_price) < Decimal("0.00"):
                raise RefundError(
                    "Ürün ödeme kaleminin tahsil edilen tutarı geçersiz."
                )
            if transaction_item.order_item_id is None:
                raise RefundError(
                    "Ürün ödeme kaleminde OrderItem bulunmuyor."
                )
            order_item = transaction_item.order_item

            if (
                refund.refund_type
                == RefundType.SUBORDER_CANCELLATION
            ):
                return cls._money(transaction_item.paid_price)

            if (
                refund.refund_type
                == RefundType.SELLER_PARTIAL_CANCELLATION
            ):
                cancellations = list(
                    SubOrderItemCancellation.objects
                    .filter(
                        payment_refund_id=refund.pk,
                        order_item_id=transaction_item.order_item_id,
                        order_item__sub_order_id=suborder.pk,
                    )
                    .prefetch_related("units")
                    .order_by("pk")
                )

                if not cancellations:
                    raise RefundError(
                        "Ödeme kalemi için kısmi iptal kaydı bulunamadı."
                    )

                order_item = transaction_item.order_item
                cancelled_units = set()
                expected_amount = Decimal("0.00")

                for cancellation in cancellations:
                    units = list(cancellation.units.all())

                    if len(units) != cancellation.quantity:
                        raise RefundError(
                            "İptal kaydındaki adet ile birim kayıtları "
                            "eşleşmiyor."
                        )

                    for unit in units:
                        if unit.order_item_id != order_item.pk:
                            raise RefundError(
                                "İptal edilen birim yanlış sipariş "
                                "kalemine bağlı."
                            )

                        if not 1 <= unit.unit_number <= order_item.quantity:
                            raise RefundError(
                                "İptal edilen ürün birimi geçersiz."
                            )

                        if unit.unit_number in cancelled_units:
                            raise RefundError(
                                "Aynı ürün birimi birden fazla kez "
                                "iptal edilmiş."
                            )

                        cancelled_units.add(unit.unit_number)

                        expected_amount += (
                            cls._calculate_unit_refund_amount(
                                paid_price=transaction_item.paid_price,
                                quantity=order_item.quantity,
                                unit_number=unit.unit_number,
                            )
                        )

                return cls._money(expected_amount)

            # Müşteri iadesi: ürün birimi ve isteğe bağlı kargo bedeli.
            if refund.refund_type in (
                RefundType.CUSTOMER_REQUEST,
                RefundType.SELLER_FAULT,
            ):
                return_request = (
                    ReturnRequest.objects
                    .filter(payment_refund_id=refund.pk)
                    .select_related("order_item")
                    .first()
                )

                if return_request is None:
                    raise RefundError(
                        "Refund ile ilişkili iade talebi bulunamadı."
                    )

                if return_request.order_item_id != order_item.pk:
                    return Decimal("0.00")

                return cls._calculate_unit_refund_amount(
                    paid_price=transaction_item.paid_price,
                    quantity=order_item.quantity,
                    unit_number=return_request.unit_number,
                )

            raise RefundError("Desteklenmeyen refund türü.")

        # --------------------------------------------------------------
        # SHIPPING
        # --------------------------------------------------------------
        if item_type == PaymentTransactionItemType.SHIPPING:
            if transaction_item.order_item_id is not None:
                raise RefundError(
                    "Kargo ödeme kaleminde OrderItem bulunmamalıdır."
                )

            if not refund.refund_shipping:
                return Decimal("0.00")

            if transaction_item.paid_price is None:
                raise RefundError(
                    "Kargo ödeme kaleminin tahsil edilen tutarı bulunamadı."
                )
            if cls._money(transaction_item.paid_price) < Decimal("0.00"):
                raise RefundError(
                    "Kargo ödeme kaleminin tahsil edilen tutarı geçersiz."
                )

            if refund.refund_type not in (
                RefundType.SUBORDER_CANCELLATION,
                RefundType.SELLER_PARTIAL_CANCELLATION,
                RefundType.CUSTOMER_REQUEST,
                RefundType.SELLER_FAULT,
            ):
                raise RefundError(
                    "Bu refund türü kargo iadesini desteklemiyor."
                )

            return cls._money(transaction_item.paid_price)

        raise RefundError("Desteklenmeyen ödeme kalemi türü.")



    @classmethod
    def _get_item_refundable_limit(
        cls,
        *,
        transaction_item,
    ):
        if transaction_item.paid_price is None:
            raise RefundError(
                "Ödeme kaleminin tahsil edilen tutarı bulunamadı."
            )

        paid_price = cls._money(
            transaction_item.paid_price
        )

        if paid_price < Decimal("0.00"):
            raise RefundError(
                "Ödeme kaleminin iade edilebilir tutarı geçersiz."
            )

        # 0 tutarlı ürün/kargo kalemleri transaction toplamında geçerlidir;
        # bu kalem için pozitif refund tutarı kapasite kontrolünden geçemez.
        return paid_price

    @classmethod
    def _call_provider(
        cls,
        *,
        payment_transaction_id,
        amount,
        currency,
        conversation_id,
        reason,
    ):
        options = {
            "api_key": settings.IYZICO_API_KEY,
            "secret_key": settings.IYZICO_SECRET_KEY,
            "base_url": settings.IYZICO_BASE_URL,
        }

        request = {
            "locale": "tr",
            "conversationId": conversation_id,
            "paymentTransactionId": payment_transaction_id,
            "price": cls._money_string(amount),
            "currency": currency,
            "reason": "other",
            "description": reason,
        }

        result = iyzipay.Refund().create(
            request,
            options,
        )

        raw_response = result.read().decode(
            "utf-8"
        )

        return json.loads(raw_response)


    @classmethod
    @transaction.atomic
    def _apply_provider_response(
        cls,
        *,
        refund_item_id,
        response,
        expected_amount,
        expected_currency,
        expected_payment_transaction_id,
        expected_payment_id,
        expected_conversation_id,
    ):
        # Kilit almadan ilişkili kayıtların kimliklerini belirle.
        reference = (
            PaymentRefundItem.objects
            .filter(pk=refund_item_id)
            .values(
                "payment_refund_id",
                "payment_refund__suborder_id",
                "payment_refund__payment_transaction_id",
                "payment_refund__payment_transaction__order_id",
            )
            .get()
        )

        order = (
            Order.objects
            .select_for_update()
            .get(
                pk=reference[
                    "payment_refund__payment_transaction__order_id"
                ]
            )
        )

        suborder = (
            SubOrder.objects
            .select_for_update()
            .get(
                pk=reference["payment_refund__suborder_id"],
                order_id=order.pk,
            )
        )

        payment_transaction = (
            PaymentTransaction.objects
            .select_for_update()
            .get(
                pk=reference[
                    "payment_refund__payment_transaction_id"
                ],
                order_id=order.pk,
            )
        )

        refund = (
            PaymentRefund.objects
            .select_for_update()
            .get(pk=reference["payment_refund_id"])
        )

        refund_item = (
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_transaction_item")
            .get(pk=refund_item_id)
        )

        if (
            refund_item.payment_refund_id != refund.pk
            or refund.payment_transaction_id != payment_transaction.pk
            or refund.suborder_id != suborder.pk
        ):
            raise RefundError(
                "İade kaleminin ilişkileri işlem sırasında değişti."
            )

        transaction_item = refund_item.payment_transaction_item
        claim_matches_current_state = (
            transaction_item.payment_transaction_id == payment_transaction.pk
            and transaction_item.suborder_id == suborder.pk
            and cls._money(refund_item.amount) == cls._money(expected_amount)
            and cls._clean(refund.currency) == cls._clean(expected_currency)
            and cls._clean(payment_transaction.currency) == cls._clean(expected_currency)
            and cls._clean(payment_transaction.payment_id)
            == cls._clean(expected_payment_id)
            and cls._clean(transaction_item.provider_transaction_id)
            == cls._clean(expected_payment_transaction_id)
            and cls._clean(refund_item.conversation_id)
            == cls._clean(expected_conversation_id)
        )
        if not claim_matches_current_state:
            if refund_item.status in {
                RefundStatus.PROCESSING,
                RefundStatus.RECONCILIATION_REQUIRED,
            }:
                cls._set_reconciliation_required(
                    refund_item=refund_item,
                    message=(
                        "Provider çağrısından sonra refund veya ödeme kalemi "
                        "kimlik/tutar bilgileri değişmiş; manuel mutabakat gerekli."
                    ),
                )
                cls._sync_refund_status(refund=refund)
                return RefundStatus.RECONCILIATION_REQUIRED
            return refund_item.status

        if not isinstance(response, dict):
            if refund_item.status != RefundStatus.PROCESSING:
                return refund_item.status

            cls._set_reconciliation_required(
                refund_item=refund_item,
                message="iyzico geçersiz bir yanıt döndürdü.",
            )
            cls._sync_refund_status(refund=refund)
            return RefundStatus.RECONCILIATION_REQUIRED

        status = str(response.get("status") or "").strip().lower()

        if status not in {"success", "failure"}:
            if refund_item.status != RefundStatus.PROCESSING:
                return refund_item.status
            cls._set_reconciliation_required(
                refund_item=refund_item,
                message=(
                    "iyzico yanıtında tanınmayan veya eksik status alanı "
                    "bulundu; sağlayıcı sonucu doğrulanmalı."
                ),
            )
            cls._sync_refund_status(refund=refund)
            return RefundStatus.RECONCILIATION_REQUIRED

        if status == "success":
            if refund_item.status not in {
                RefundStatus.PROCESSING,
                RefundStatus.RECONCILIATION_REQUIRED,
            }:
                return refund_item.status

        else:
            if refund_item.status != RefundStatus.PROCESSING:
                return refund_item.status

        if status == "success":
            try:
                cls._validate_success_response(
                    response=response,
                    expected_amount=expected_amount,
                    expected_currency=expected_currency,
                    expected_payment_transaction_id=(
                        expected_payment_transaction_id
                    ),
                    expected_payment_id=expected_payment_id,
                    expected_conversation_id=expected_conversation_id,
                )
            except RefundError as exc:
                cls._set_reconciliation_required(
                    refund_item=refund_item,
                    message=str(exc),
                )
                cls._sync_refund_status(refund=refund)
                return RefundStatus.RECONCILIATION_REQUIRED

            refund_item.status = RefundStatus.SUCCESS
            refund_item.retryable = False
            refund_item.provider_refund_id = cls._clean(
                response.get("refundHostReference")
            )
            refund_item.provider_error_code = ""
            refund_item.provider_error_message = ""
            refund_item.completed_at = timezone.now()

        else:
            refund_item.status = RefundStatus.FAILED
            refund_item.retryable = cls._to_bool(
                response.get("retryable")
            )
            refund_item.provider_error_code = cls._clean(
                response.get("errorCode")
            )[:100]
            refund_item.provider_error_message = cls._clean(
                response.get("errorMessage")
            )[:500]
            refund_item.completed_at = None

        refund_item.save(
            update_fields=[
                "status",
                "retryable",
                "provider_refund_id",
                "provider_error_code",
                "provider_error_message",
                "completed_at",
                "updated_at",
            ]
        )

        cls._sync_refund_status(refund=refund)
        cls._sync_payment_transaction_status(
            payment_transaction=payment_transaction,
        )

        return refund_item.status

    @classmethod
    def _validate_success_response(
        cls,
        *,
        response,
        expected_amount,
        expected_currency,
        expected_payment_transaction_id,
        expected_payment_id,
        expected_conversation_id,
    ):
        provider_transaction_id = cls._clean(
            response.get("paymentTransactionId")
        )

        if not provider_transaction_id:
            raise RefundError(
                "iyzico refund yanıtında "
                "paymentTransactionId alanı eksik."
            )

        if provider_transaction_id != expected_payment_transaction_id:
            raise RefundError(
                "iyzico refund yanıtındaki "
                "paymentTransactionId beklenen değerle eşleşmiyor."
            )

        provider_currency = cls._clean(
            response.get("currency")
        )

        if not provider_currency:
            raise RefundError(
                "iyzico refund yanıtında currency alanı eksik."
            )

        if provider_currency != expected_currency:
            raise RefundError(
                "iyzico refund yanıtındaki para birimi "
                "beklenen değerle eşleşmiyor."
            )

        if response.get("price") is None:
            raise RefundError(
                "iyzico refund yanıtında price alanı eksik."
            )

        try:
            raw_provider_price = Decimal(str(response["price"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RefundError(
                "iyzico refund yanıtındaki price alanı geçersiz."
            ) from exc

        if not raw_provider_price.is_finite():
            raise RefundError(
                "iyzico refund yanıtındaki price alanı sonlu bir tutar değil."
            )

        provider_price = cls._normalize_money(raw_provider_price)
        if raw_provider_price != provider_price:
            raise RefundError(
                "iyzico refund yanıtındaki price alanı iki ondalık basamakla "
                "eşleşmiyor."
            )

        if provider_price != cls._money(expected_amount):
            raise RefundError(
                "iyzico refund yanıtındaki tutar "
                "beklenen tutarla eşleşmiyor."
            )

        provider_payment_id = cls._clean(
            response.get("paymentId")
        )

        provider_conversation_id = cls._clean(
            response.get("conversationId")
        )

        signature = cls._clean(
            response.get("signature")
        )

        if not provider_payment_id:
            raise RefundError(
                "iyzico refund yanıtında paymentId alanı eksik."
            )

        if provider_payment_id != cls._clean(expected_payment_id):
            raise RefundError(
                "iyzico refund yanıtındaki paymentId "
                "beklenen ödeme kimliğiyle eşleşmiyor."
            )

        if not provider_conversation_id:
            raise RefundError(
                "iyzico refund yanıtında conversationId alanı eksik."
            )

        if provider_conversation_id != expected_conversation_id:
            raise RefundError(
                "iyzico refund yanıtındaki conversationId "
                "beklenen değerle eşleşmiyor."
            )

        if not signature:
            raise RefundError(
                "iyzico refund yanıtında imza bulunamadı."
            )

        # iyzico imza hesabında fiyatın sonundaki gereksiz
        # sıfırlar kaldırılmalıdır: 10.50 -> 10.5, 10.00 -> 10.
        signature_price = format(
            provider_price.normalize(),
            "f",
        )

        expected_signature = (
            iyzipay.Refund().calculate_hmac_sha256_signature(
                [
                    provider_payment_id,
                    signature_price,
                    provider_currency,
                    provider_conversation_id,
                ],
                settings.IYZICO_SECRET_KEY,
            )
        )

        if not hmac.compare_digest(
            signature.lower(),
            expected_signature.lower(),
        ):
            raise RefundError(
                "iyzico refund yanıtının imzası doğrulanamadı."
            )

        refund_reference = cls._clean(
            response.get("refundHostReference")
        )

        if not refund_reference:
            # Başarılı cevabın refundHostReference içermemesi
            # tek başına refund'ı başarısız yapmıyor.
            return


    @classmethod
    @transaction.atomic
    def _mark_reconciliation_required(
        cls,
        *,
        refund_item_id,
        message,
    ):
        reference = (
            PaymentRefundItem.objects
            .filter(pk=refund_item_id)
            .values(
                "payment_refund_id",
                "payment_refund__suborder_id",
                "payment_refund__payment_transaction_id",
                "payment_refund__payment_transaction__order_id",
            )
            .get()
        )

        order = (
            Order.objects
            .select_for_update()
            .get(
                pk=reference[
                    "payment_refund__payment_transaction__order_id"
                ]
            )
        )

        suborder = (
            SubOrder.objects
            .select_for_update()
            .get(
                pk=reference["payment_refund__suborder_id"],
                order_id=order.pk,
            )
        )

        payment_transaction = (
            PaymentTransaction.objects
            .select_for_update()
            .get(
                pk=reference[
                    "payment_refund__payment_transaction_id"
                ],
                order_id=order.pk,
            )
        )

        refund = (
            PaymentRefund.objects
            .select_for_update()
            .get(pk=reference["payment_refund_id"])
        )

        refund_item = (
            PaymentRefundItem.objects
            .select_for_update()
            .get(pk=refund_item_id)
        )

        if (
            refund_item.payment_refund_id != refund.pk
            or refund.payment_transaction_id != payment_transaction.pk
            or refund.suborder_id != suborder.pk
        ):
            raise RefundError(
                "İade kaleminin ilişkileri işlem sırasında değişti."
            )

        if refund_item.status != RefundStatus.PROCESSING:
            return

        cls._set_reconciliation_required(
            refund_item=refund_item,
            message=message,
        )

        cls._sync_refund_status(refund=refund)


    @classmethod
    def _set_reconciliation_required(
        cls,
        *,
        refund_item,
        message,
    ):
        refund_item.status = (
            RefundStatus.RECONCILIATION_REQUIRED
        )
        refund_item.retryable = None
        refund_item.provider_error_message = (
            str(message or "")[:500]
        )
        refund_item.save(
            update_fields=[
                "status",
                "retryable",
                "provider_error_message",
                "updated_at",
            ]
        )

    @classmethod
    def _sync_refund_status(cls, *, refund):
        statuses = list(
            PaymentRefundItem.objects
            .filter(payment_refund=refund)
            .values_list(
                "status",
                "retryable",
            )
        )

        if not statuses:
            new_status = RefundStatus.PENDING

        elif any(
            status == RefundStatus.RECONCILIATION_REQUIRED
            for status, _ in statuses
        ):
            new_status = (
                RefundStatus.RECONCILIATION_REQUIRED
            )

        elif any(
            status in {
                RefundStatus.PENDING,
                RefundStatus.PROCESSING,
            }
            for status, _ in statuses
        ):
            new_status = RefundStatus.PENDING

        elif all(
            status == RefundStatus.SUCCESS
            for status, _ in statuses
        ):
            new_status = RefundStatus.SUCCESS

        elif any(
            status == RefundStatus.FAILED
            and retryable is True
            for status, retryable in statuses
        ):
            new_status = RefundStatus.PENDING

        else:
            new_status = RefundStatus.FAILED

        if refund.status != new_status:
            refund.status = new_status

            update_fields = [
                "status",
                "updated_at",
            ]

            if new_status == RefundStatus.SUCCESS:
                refund.completed_at = timezone.now()
                update_fields.append("completed_at")
            else:
                refund.completed_at = None
                update_fields.append("completed_at")

            refund.save(
                update_fields=update_fields
            )

        if (
            refund.status == RefundStatus.SUCCESS
            and refund.refund_type in (
                RefundType.CUSTOMER_REQUEST,
                RefundType.SELLER_FAULT,
            )
        ):
            return_request = (
                ReturnRequest.objects
                .select_for_update()
                .filter(payment_refund_id=refund.pk)
                .first()
            )

            if (
                return_request is not None
                and return_request.status == ReturnStatus.REFUND_PENDING
            ):
                return_request.status = ReturnStatus.COMPLETED
                return_request.completed_at = timezone.now()

                return_request.save(
                    update_fields=[
                        "status",
                        "completed_at",
                        "updated_at",
                    ]
                )

    @classmethod
    def _sync_payment_transaction_status(
        cls,
        *,
        payment_transaction,
    ):
        successful_refund_total = (
            PaymentRefundItem.objects
            .filter(
                payment_transaction_item__payment_transaction=(
                    payment_transaction
                ),
                status=RefundStatus.SUCCESS,
            )
            .aggregate(
                total=Sum("amount"),
            )
            .get("total")
            or Decimal("0.00")
        )

        transaction_items = list(
            PaymentTransactionItem.objects.filter(
                payment_transaction=payment_transaction,
            )
        )

        if not transaction_items:
            return

        refundable_total = sum(
            (
                cls._get_item_refundable_limit(
                    transaction_item=item,
                )
                for item in transaction_items
            ),
            Decimal("0.00"),
        )

        successful_refund_total = cls._money(
            successful_refund_total
        )

        refundable_total = cls._money(
            refundable_total
        )

        if successful_refund_total > refundable_total:
            raise RefundError(
                "Başarılı refund kalemlerinin toplamı ödeme kalemlerinin "
                "tahsil edilen toplamını aşıyor; manuel mutabakat gerekli."
            )

        if successful_refund_total <= Decimal("0.00"):
            return

        if successful_refund_total >= refundable_total:
            new_status = PaymentStatus.REFUNDED
        else:
            new_status = PaymentStatus.PARTIALLY_REFUNDED

        if payment_transaction.status != new_status:
            payment_transaction.status = new_status
            payment_transaction.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

    @classmethod
    @transaction.atomic
    def _recover_stale_processing_items(
        cls,
        *,
        refund,
    ):
        timeout_minutes = getattr(
            settings,
            "IYZICO_REFUND_PROCESSING_TIMEOUT_MINUTES",
            cls.PROCESSING_TIMEOUT_MINUTES,
        )

        stale_before = (
            timezone.now()
            - timedelta(minutes=timeout_minutes)
        )

        stale_items = list(
            PaymentRefundItem.objects
            .select_for_update()
            .filter(
                payment_refund=refund,
                status=RefundStatus.PROCESSING,
                updated_at__lt=stale_before,
            )
            .order_by("pk")
        )

        if not stale_items:
            return False

        now = timezone.now()

        for item in stale_items:
            item.status = (
                RefundStatus.RECONCILIATION_REQUIRED
            )
            item.retryable = None
            item.provider_error_message = (
                "Refund provider çağrısı sırasında işlem kesildi "
                "ve sonucun provider tarafında gerçekleşip "
                "gerçekleşmediği doğrulanamadı."
            )
            item.updated_at = now

            item.save(
                update_fields=[
                    "status",
                    "retryable",
                    "provider_error_message",
                    "updated_at",
                ]
            )

        return True

    @staticmethod
    def _get_refund(*, payment_refund_id):
        return (
            PaymentRefund.objects
            .select_related(
                "payment_transaction",
                "suborder",
            )
            .get(pk=payment_refund_id)
        )

    @staticmethod
    def _money(value):
        return Decimal(value).quantize(
            Decimal("0.01")
        )

    @classmethod
    def _normalize_money(cls, value):
        if value is None:
            raise RefundError(
                "Para değeri bulunamadı."
            )

        try:
            return cls._money(value)
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise RefundError(
                "Geçersiz para değeri."
            ) from exc

    @classmethod
    def _money_string(cls, value):
        return format(
            cls._money(value),
            "f",
        )

    @staticmethod
    def _clean(value):
        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _to_bool(value):
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            value = value.strip().lower()

            if value in {"true", "1", "yes"}:
                return True

            if value in {"false", "0", "no"}:
                return False

        if isinstance(value, int):
            return bool(value)

        return None
