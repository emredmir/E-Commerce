import json
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
)



class RefundError(Exception):
    """Ödeme iadesi gerçekleştirilemedi."""
    

class RefundService:
    PROVIDER = "iyzico"

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
    
        while True:
            claim = cls._claim_next_item(
                payment_refund_id=payment_refund_id,
            )
    
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
    
            if result_status in {
                RefundStatus.FAILED,
                RefundStatus.RECONCILIATION_REQUIRED,
            }:
                return cls._get_refund(
                    payment_refund_id=payment_refund_id,
                )

    @classmethod
    @transaction.atomic
    def _claim_next_item(cls, *, payment_refund_id):
        refund = (
            PaymentRefund.objects
            .select_for_update()
            .get(pk=payment_refund_id)
        )

        # ------------------------------------------------------------------
        # Global lock order:
        #
        #     Order
        #       ↓
        #     SubOrder
        #       ↓
        #     PaymentTransaction
        #
        # CancellationService ile aynı sırayı kullanıyoruz.
        # ------------------------------------------------------------------

        order = (
            Order.objects
            .select_for_update()
            .get(
                pk=refund.payment_transaction.order_id
            )
        )

        suborder = (
            SubOrder.objects
            .select_for_update()
            .get(
                pk=refund.suborder_id,
                order=order,
            )
        )

        payment_transaction = (
            PaymentTransaction.objects
            .select_for_update()
            .get(
                pk=refund.payment_transaction_id,
                order=order,
            )
        )

        cls._validate_refund(
            refund=refund,
            order=order,
            suborder=suborder,
            payment_transaction=payment_transaction,
        )

        if refund.status == RefundStatus.SUCCESS:
            return None

        cls._recover_stale_processing_items(
            refund=refund,
        )

        cls._ensure_refund_items(
            refund=refund,
            suborder=suborder,
            payment_transaction=payment_transaction,
        )

        refund_item = (
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_transaction_item")
            .filter(
                payment_refund=refund,
            )
            .filter(
                Q(status=RefundStatus.PENDING)
                |
                Q(
                    status=RefundStatus.FAILED,
                    retryable=True,
                )
            )
            .order_by("id")
            .first()
        )

        if refund_item is None:
            cls._sync_refund_status(
                refund=refund,
            )

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
            "amount": refund_item.amount,
            "currency": refund.currency,
            "conversation_id": refund_item.conversation_id,
            "reason": refund.reason or "customer_request",
        }

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

        if suborder.status != SubOrderStatus.CANCELLED:
            raise RefundError(
                "İade yalnızca iptal edilmiş alt siparişler için yapılabilir."
            )

        if payment_transaction.provider != cls.PROVIDER:
            raise RefundError(
                "Bu ödeme sağlayıcısı için refund desteği bulunmuyor."
            )

        if refund.currency != payment_transaction.currency:
            raise RefundError(
                "İade ve ödeme para birimleri uyuşmuyor."
            )

        if refund.amount <= Decimal("0.00"):
            raise RefundError(
                "İade tutarı sıfırdan büyük olmalıdır."
            )

        if refund.refund_type == RefundType.SUBORDER_CANCELLATION:
            if not refund.refund_shipping:
                raise RefundError(
                    "Alt sipariş iptalinde kargo iadesi açık olmalıdır."
                )

        # --------------------------------------------------------------
        # Logical refund state FIRST
        # --------------------------------------------------------------

        if refund.status == RefundStatus.SUCCESS:
            return

        if refund.status == RefundStatus.RECONCILIATION_REQUIRED:
            raise RefundError(
                "Bu iade için önce provider mutabakatı yapılmalıdır."
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

    @classmethod
    def _ensure_refund_items(
        cls,
        *,
        refund,
        suborder,
        payment_transaction,
    ):
        transaction_items = list(
            PaymentTransactionItem.objects
            .filter(
                payment_transaction=payment_transaction,
                suborder=suborder,
            )
            .order_by("id")
        )

        if not transaction_items:
            raise RefundError(
                "Alt sipariş için ödeme kırılımı bulunamadı."
            )

        existing_refund_items = {
            item.payment_transaction_item_id: item
            for item in (
                PaymentRefundItem.objects
                .filter(payment_refund=refund)
            )
        }

        expected_total = Decimal("0.00")

        for transaction_item in transaction_items:
            expected_amount = cls._get_refund_item_amount(
                refund=refund,
                transaction_item=transaction_item,
                suborder=suborder,
            )

            if expected_amount <= Decimal("0.00"):
                continue

            expected_total += expected_amount

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

            if existing is not None:
                if existing.amount != expected_amount:
                    raise RefundError(
                        "Mevcut refund kaleminin tutarı "
                        "beklenen iade tutarıyla eşleşmiyor."
                    )

                continue

            already_reserved = (
                PaymentRefundItem.objects
                .filter(
                    payment_transaction_item=transaction_item,
                )
                .filter(
                    Q(
                        status__in=cls.RESERVED_ITEM_STATUSES,
                    )
                    | Q(
                        status=RefundStatus.FAILED,
                        retryable=True,
                    )
                )
                .exclude(payment_refund=refund)
                .aggregate(
                    total=Sum("amount"),
                )
                .get("total")
                or Decimal("0.00")
            )

            remaining = (
                max_refundable
                - cls._money(already_reserved)
            )

            if expected_amount > remaining:
                raise RefundError(
                    "Ödeme kalemi için kalan "
                    "iade edilebilir tutar yetersiz."
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

        if item_type == PaymentTransactionItemType.PRODUCT:
            if transaction_item.order_item_id is None:
                raise RefundError(
                    "Ürün ödeme kaleminde OrderItem bulunmuyor."
                )

            return cls._money(
                transaction_item.paid_price
            )

        if item_type == PaymentTransactionItemType.SHIPPING:
            if transaction_item.order_item_id is not None:
                raise RefundError(
                    "Kargo ödeme kaleminde OrderItem bulunmamalıdır."
                )

            if transaction_item.suborder_id != suborder.pk:
                raise RefundError(
                    "Kargo ödeme kalemi alt sipariş ile eşleşmiyor."
                )

            if not refund.refund_shipping:
                return Decimal("0.00")

            return cls._money(
                transaction_item.paid_price
            )

        raise RefundError(
            "Desteklenmeyen ödeme kalemi türü."
        )

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

        if paid_price <= Decimal("0.00"):
            raise RefundError(
                "Ödeme kaleminin iade edilebilir tutarı geçersiz."
            )

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
    ):
        refund_item = (
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_refund")
            .get(pk=refund_item_id)
        )

        refund = (
            PaymentRefund.objects
            .select_for_update()
            .get(pk=refund_item.payment_refund_id)
        )

        payment_transaction = (
            PaymentTransaction.objects
            .select_for_update()
            .get(pk=refund.payment_transaction_id)
        )

        if refund_item.status != RefundStatus.PROCESSING:
            return refund_item.status

        if not isinstance(response, dict):
            cls._set_reconciliation_required(
                refund_item=refund_item,
                message="iyzico geçersiz bir yanıt döndürdü.",
            )

            cls._sync_refund_status(
                refund=refund,
            )

            return RefundStatus.RECONCILIATION_REQUIRED

        status = str(
            response.get("status") or ""
        ).strip().lower()

        if status == "success":
            try:
                cls._validate_success_response(
                    response=response,
                    expected_amount=expected_amount,
                    expected_currency=expected_currency,
                    expected_payment_transaction_id=(
                        expected_payment_transaction_id
                    ),
                )
            except RefundError as exc:
                cls._set_reconciliation_required(
                    refund_item=refund_item,
                    message=str(exc),
                )

                cls._sync_refund_status(
                    refund=refund,
                )

                return RefundStatus.RECONCILIATION_REQUIRED

            refund_item.status = RefundStatus.SUCCESS
            refund_item.retryable = False
            refund_item.provider_refund_id = (
                cls._clean(
                    response.get(
                        "refundHostReference"
                    )
                )
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

        cls._sync_refund_status(
            refund=refund,
        )

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
    ):
        provider_transaction_id = cls._clean(
            response.get("paymentTransactionId")
        )

        if (
            provider_transaction_id
            and provider_transaction_id
            != expected_payment_transaction_id
        ):
            raise RefundError(
                "iyzico refund yanıtındaki "
                "paymentTransactionId beklenen değerle eşleşmiyor."
            )

        provider_currency = cls._clean(
            response.get("currency")
        )

        if (
            provider_currency
            and provider_currency != expected_currency
        ):
            raise RefundError(
                "iyzico refund yanıtındaki para birimi "
                "beklenen değerle eşleşmiyor."
            )

        if response.get("price") is not None:
            provider_price = cls._normalize_money(
                response.get("price")
            )

            if provider_price != cls._money(expected_amount):
                raise RefundError(
                    "iyzico refund yanıtındaki tutar "
                    "beklenen tutarla eşleşmiyor."
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
        refund_item = (
            PaymentRefundItem.objects
            .select_for_update()
            .select_related("payment_refund")
            .get(pk=refund_item_id)
        )

        if refund_item.status != RefundStatus.PROCESSING:
            return

        cls._set_reconciliation_required(
            refund_item=refund_item,
            message=message,
        )

        refund = (
            PaymentRefund.objects
            .select_for_update()
            .get(pk=refund_item.payment_refund_id)
        )

        cls._sync_refund_status(
            refund=refund,
        )

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

    @classmethod
    def _sync_payment_transaction_status(
        cls,
        *,
        payment_transaction,
    ):
        if payment_transaction.status == PaymentStatus.REFUNDED:
            return

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
        )

        if not stale_items:
            return

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