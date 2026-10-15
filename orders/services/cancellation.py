
from collections.abc import Mapping
from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from products.models import StoreProduct

from ..exceptions import CancellationError, ReservationError
from ..models import (
    Order,
    OrderItem,
    PaymentRefund,
    PaymentRefundItem,
    PaymentStatus,
    PaymentTransaction,
    PaymentTransactionItem,
    PaymentTransactionItemType,
    RefundStatus,
    RefundType,
    ReturnRequest,
    ReturnStatus,
    SubOrder,
    SubOrderCancellation,
    SubOrderItemCancellation,
    SubOrderItemCancellationUnit,
    SubOrderStatus,
)
from .stock_reservation import StockReservationService
from .refund import RefundError, RefundService


class CancellationService:
    """
    SubOrder ve OrderItem seviyesinde satıcı iptali.

    Tüm iptal işlemleri cancel_items() üzerinden yürütülür.

    Genel kilit sırası:
        Order -> SubOrder -> PaymentTransaction
        -> PaymentTransactionItem -> StoreProduct -> OrderItem

    Stok geri yükleme StockReservationService üzerinden yapılır.
    OrderItem.quantity tarihsel satın alma miktarı olarak korunur.
    """

    CANCELLABLE_SUBORDER_STATES = {
        SubOrderStatus.PENDING,
        SubOrderStatus.PREPARING,
    }

    RESERVED_RETURN_STATUSES = {
        ReturnStatus.AUTHORIZED,
        ReturnStatus.IN_TRANSIT,
        ReturnStatus.AWAITING_SELLER_REVIEW,
        ReturnStatus.DISPUTED,
        ReturnStatus.REFUND_PENDING,
        ReturnStatus.COMPLETED,
    }

    # Parent refund reservation statuses are defined centrally by RefundService.
    RESERVED_REFUND_STATUSES = RefundService.RESERVED_REFUND_STATUSES

    # ------------------------------------------------------------------
    # Public API: cancel a quantity from one or more OrderItems
    # ------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def cancel_items(
        cls,
        *,
        suborder: SubOrder,
        items: Mapping,
        cancelled_by=None,
        reason: str,
    ) -> list[SubOrderItemCancellation]:
        """
        Bir veya birden fazla OrderItem için belirtilen miktarları iptal eder.

        Örnek:
            items={order_item_id: 1}

        İptal edilecek birimler, satın alınan birim numaraları arasından
        seçilir. Önceden iptal edilmiş veya iade sürecinde olan birimler
        tekrar iptal edilemez.

        Alt siparişteki bütün birimler iptal edilirse:
            - SubOrderCancellation oluşturulur.
            - SubOrder CANCELLED durumuna geçirilir.

        Dönüş değeri:
            Oluşturulan SubOrderItemCancellation kayıtları.
        """

        reason = cls._validate_reason(reason)
        normalized_items = cls._normalize_items(items)

        # Order her zaman ilk kilitlenen kayıt olmalı.
        locked_order = (
            Order.objects
            .select_for_update()
            .get(pk=suborder.order_id)
        )

        locked_suborder = (
            SubOrder.objects
            .select_for_update()
            .get(
                pk=suborder.pk,
                order=locked_order,
            )
        )

        cls._validate_suborder_state(locked_suborder)

        if SubOrderCancellation.objects.filter(
            suborder_id=locked_suborder.pk,
        ).exists():
            raise CancellationError(
                "Bu alt sipariş için daha önce bir iptal kaydı oluşturulmuş."
            )

        payment_transaction = cls._get_successful_payment_transaction(
            order=locked_order,
        )

        if locked_order.currency != payment_transaction.currency:
            raise CancellationError(
                "Sipariş ve ödeme para birimleri uyuşmuyor."
            )

        # OrderItem kimlikleri ve mağaza ürünlerini önceden belirle.
        item_refs = list(
            OrderItem.objects
            .filter(sub_order=locked_suborder)
            .values_list("pk", "store_product_id")
        )

        if not item_refs:
            raise CancellationError(
                "Bu alt siparişte iptal edilebilecek ürün bulunamadı."
            )

        store_product_ids = {
            store_product_id
            for _, store_product_id in item_refs
        }

        valid_order_item_ids = {
            order_item_id
            for order_item_id, _ in item_refs
        }

        unknown_ids = (
            set(normalized_items.keys()) - valid_order_item_ids
        )

        if unknown_ids:
            raise CancellationError(
                "İptal edilecek ürünlerden biri bu alt siparişe ait değil."
            )

        # PaymentTransactionItem kayıtlarını ürünlerden önce kilitle.
        transaction_items = list(
            PaymentTransactionItem.objects
            .select_for_update()
            .filter(
                payment_transaction=payment_transaction,
                suborder=locked_suborder,
            )
            .order_by("pk")
        )

        product_payment_items_by_order_item = {}
        shipping_items = []

        for payment_item in transaction_items:
            if payment_item.item_type == PaymentTransactionItemType.PRODUCT:
                if (
                    payment_item.order_item_id is None
                    or payment_item.order_item_id not in valid_order_item_ids
                ):
                    raise CancellationError(
                        "Ürün ödeme kalemi geçerli bir sipariş kalemiyle eşleşmiyor."
                    )

                product_payment_items_by_order_item.setdefault(
                    payment_item.order_item_id,
                    [],
                ).append(payment_item)

            elif payment_item.item_type == PaymentTransactionItemType.SHIPPING:
                if payment_item.order_item_id is not None:
                    raise CancellationError(
                        "Kargo ödeme kaleminde sipariş kalemi bulunmamalıdır."
                    )

                shipping_items.append(payment_item)

            else:
                raise CancellationError(
                    "Desteklenmeyen ödeme kalemi türü."
                )

        duplicate_payment_item_ids = [
            order_item_id
            for order_item_id, payment_items in (
                product_payment_items_by_order_item.items()
            )
            if len(payment_items) > 1
        ]

        if duplicate_payment_item_ids:
            raise CancellationError(
                "Bir sipariş kalemi için birden fazla ürün ödeme kalemi bulundu."
            )

        # Ortak stok kilit sırası: StoreProduct -> OrderItem.
        list(
            StoreProduct.objects
            .select_for_update()
            .filter(pk__in=store_product_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        order_items = list(
            OrderItem.objects
            .select_for_update()
            .filter(sub_order=locked_suborder)
            .order_by("pk")
        )

        order_items_by_id = {
            order_item.pk: order_item
            for order_item in order_items
        }

        # Önceki iptal kayıtlarını ve birimlerini doğrula.
        historical_cancellations = list(
            SubOrderItemCancellation.objects
            .filter(order_item__sub_order=locked_suborder)
            .prefetch_related("units")
            .order_by("pk")
        )

        for cancellation in historical_cancellations:
            units = list(cancellation.units.all())

            if len(units) != cancellation.quantity:
                raise CancellationError(
                    "Önceki iptal kaydındaki adet ile birim kayıtları eşleşmiyor."
                )

            if any(
                unit.order_item_id != cancellation.order_item_id
                for unit in units
            ):
                raise CancellationError(
                    "Önceki iptal kaydında farklı bir sipariş kalemine "
                    "ait birim bulundu."
                )

            order_item = order_items_by_id.get(cancellation.order_item_id)

            if order_item is None:
                raise CancellationError(
                    "Önceki iptal kaydı bu alt siparişe ait değil."
                )

            if any(
                not 1 <= unit.unit_number <= order_item.quantity
                for unit in units
            ):
                raise CancellationError(
                    "Önceki iptal kaydında geçersiz ürün birimi bulundu."
                )

        # İptal birimlerini yükle ve iptal kaydıyla ilişkilerini doğrula.
        existing_cancelled_units = {}

        cancelled_unit_rows = list(
            SubOrderItemCancellationUnit.objects
            .filter(order_item_id__in=order_items_by_id)
            .select_related(
                "cancellation",
                "cancellation__order_item",
            )
            .order_by("order_item_id", "unit_number")
        )

        for unit in cancelled_unit_rows:
            order_item = order_items_by_id[unit.order_item_id]

            if (
                unit.cancellation.order_item_id != unit.order_item_id
                or unit.cancellation.order_item.sub_order_id
                != locked_suborder.pk
            ):
                raise CancellationError(
                    "İptal edilen birim ile iptal kaydının ilişkisi tutarsız."
                )

            if not 1 <= unit.unit_number <= order_item.quantity:
                raise CancellationError(
                    "İptal kayıtlarında geçersiz ürün birimi bulundu."
                )

            existing_numbers = existing_cancelled_units.setdefault(
                unit.order_item_id,
                set(),
            )
            if unit.unit_number in existing_numbers:
                raise CancellationError(
                    "Aynı satın alınmış ürün birimi birden fazla iptal "
                    "kaydıyla ilişkilendirilmiş. Manuel kontrol gerekli."
                )
            existing_numbers.add(unit.unit_number)

        # İade sürecinde ayrılmış veya iadesi tamamlanmış birimler.
        reserved_return_units = {}

        return_unit_rows = (
            ReturnRequest.objects
            .filter(
                order_item_id__in=order_items_by_id,
                status__in=cls.RESERVED_RETURN_STATUSES,
            )
            .values_list("order_item_id", "unit_number")
        )

        for order_item_id, unit_number in return_unit_rows:
            order_item = order_items_by_id[order_item_id]

            if not 1 <= unit_number <= order_item.quantity:
                raise CancellationError(
                    "İade kaydında geçersiz ürün birimi bulundu."
                )

            item_return_units = reserved_return_units.setdefault(
                order_item_id,
                set(),
            )
            if unit_number in item_return_units:
                raise CancellationError(
                    "Aynı satın alınmış ürün birimi birden fazla iade kaydıyla "
                    "rezerve edilmiş; manuel kontrol gerekli."
                )
            item_return_units.add(unit_number)

        # Geçmiş veride aynı birim hem iptal hem iade edilmiş görünüyorsa
        # yeni işlem yapılmamalı; kayıtlar önce uzlaştırılmalıdır.
        for order_item_id in order_items_by_id:
            cancelled_numbers = existing_cancelled_units.get(
                order_item_id,
                set(),
            )
            return_numbers = reserved_return_units.get(
                order_item_id,
                set(),
            )

            if cancelled_numbers & return_numbers:
                raise CancellationError(
                    "Aynı ürün birimi hem iptal hem iade kaydında bulunuyor."
                )

        selected_units = {}
        refund_by_order_item = {}

        # Her OrderItem için seçilecek birimleri ve ürün refund tutarını
        # hesapla. Bu aşamada henüz veritabanına iptal kaydı yazılmaz.
        for order_item_id, quantity_to_cancel in normalized_items.items():
            order_item = order_items_by_id[order_item_id]

            if order_item.quantity < 1:
                raise CancellationError(
                    "Sipariş kaleminin miktarı geçersiz."
                )

            cancelled_numbers = existing_cancelled_units.get(
                order_item_id,
                set(),
            )

            reserved_numbers = reserved_return_units.get(
                order_item_id,
                set(),
            )

            valid_unit_numbers = set(
                range(1, order_item.quantity + 1)
            )

            if not cancelled_numbers.issubset(valid_unit_numbers):
                raise CancellationError(
                    "İptal kayıtlarında geçersiz ürün birimi bulundu."
                )

            unavailable_numbers = cancelled_numbers | reserved_numbers

            available_numbers = [
                unit_number
                for unit_number in range(
                    1,
                    order_item.quantity + 1,
                )
                if unit_number not in unavailable_numbers
            ]

            if quantity_to_cancel > len(available_numbers):
                raise CancellationError(
                    "İptal edilecek miktar, iptal edilebilir ürün "
                    "miktarını aşıyor."
                )

            selected = available_numbers[:quantity_to_cancel]
            selected_units[order_item_id] = selected

            payment_items = product_payment_items_by_order_item.get(
                order_item_id,
                [],
            )

            if len(payment_items) != 1:
                raise CancellationError(
                    "İptal edilecek ürün için tek ve geçerli bir ödeme kalemi bulunamadı."
                )

            payment_item = payment_items[0]

            if payment_item is None:
                raise CancellationError(
                    "İptal edilecek ürün için ödeme kalemi bulunamadı."
                )

            if payment_item.paid_price is None:
                raise CancellationError(
                    "Ürünün gerçek ödeme tutarı bulunamadı."
                )

            if payment_item.paid_price < Decimal("0.00"):
                raise CancellationError(
                    "Ürünün ödeme tutarı geçersiz."
                )

            item_refund = sum(
                (
                    cls._get_unit_refund_amount(
                        paid_price=payment_item.paid_price,
                        quantity=order_item.quantity,
                        unit_number=unit_number,
                    )
                    for unit_number in selected
                ),
                Decimal("0.00"),
            )

            refund_by_order_item[order_item_id] = item_refund

        product_refund_amount = sum(
            refund_by_order_item.values(),
            Decimal("0.00"),
        )

        # İşlem sonrasında satın alınan bütün birimlerin iptal edilmiş
        # olup olmadığını belirle.
        fully_cancelled = True

        for order_item in order_items:
            final_cancelled_numbers = (
                existing_cancelled_units.get(order_item.pk, set())
                | set(selected_units.get(order_item.pk, []))
            )

            expected_numbers = set(
                range(1, order_item.quantity + 1)
            )

            if final_cancelled_numbers != expected_numbers:
                fully_cancelled = False
                break

        # Kargo yalnızca bütün ürün birimleri iptal edildiğinde ve daha
        # önce başka bir refund tarafından ayrılmamışsa iade edilebilir.
        shipping_refund_amount = Decimal("0.00")
        refund_shipping = False

        if (
            fully_cancelled
            and locked_suborder.shipping_amount > Decimal("0.00")
        ):
            if len(shipping_items) != 1:
                raise CancellationError(
                    "Alt sipariş için geçerli kargo ödeme kalemi bulunamadı."
                )

            shipping_item = shipping_items[0]

            if shipping_item.paid_price is None:
                raise CancellationError(
                    "Kargo ödeme tutarı bulunamadı."
                )

            if shipping_item.paid_price < Decimal("0.00"):
                raise CancellationError(
                    "Kargo ödeme tutarı geçersiz."
                )

            if (
                shipping_item.paid_price == Decimal("0.00")
                and locked_suborder.shipping_amount > Decimal("0.00")
            ):
                raise CancellationError(
                    "Alt siparişte kargo ücreti bulunmasına rağmen "
                    "ödeme kaleminin tahsil edilen tutarı sıfır."
                )

            if not cls._shipping_refund_already_reserved(
                payment_transaction=payment_transaction,
                suborder=locked_suborder,
                shipping_item=shipping_item,
            ):
                shipping_refund_amount = shipping_item.paid_price
                refund_shipping = shipping_refund_amount > Decimal("0.00")

        total_refund_amount = (
            product_refund_amount + shipping_refund_amount
        ).quantize(Decimal("0.01"))

        # Önce hem toplam ödeme hem de her bir sağlayıcı ödeme kalemi
        # için kalan iade kapasitesini doğrula. Bu kontroller refund,
        # stok ve iptal kayıtları yazılmadan önce yapılır.
        cls._validate_refund_amount(
            payment_transaction=payment_transaction,
            refund_amount=total_refund_amount,
        )

        try:
            for order_item_id, item_amount in refund_by_order_item.items():
                if item_amount <= Decimal("0.00"):
                    continue

                payment_item = product_payment_items_by_order_item[
                    order_item_id
                ][0]

                RefundService.validate_payment_item_refund_capacity(
                    transaction_item=payment_item,
                    proposed_amount=item_amount,
                )

            if refund_shipping:
                RefundService.validate_payment_item_refund_capacity(
                    transaction_item=shipping_items[0],
                    proposed_amount=shipping_refund_amount,
                )
        except RefundError as exc:
            raise CancellationError(str(exc)) from exc

        if total_refund_amount > Decimal("0.00"):
            if not str(payment_transaction.payment_id or "").strip():
                raise CancellationError(
                    "Ödeme işleminde iyzico payment_id bilgisi bulunamadı."
                )

            for order_item_id, item_amount in refund_by_order_item.items():
                if item_amount <= Decimal("0.00"):
                    continue
                payment_item = product_payment_items_by_order_item[
                    order_item_id
                ][0]
                if not str(payment_item.provider_transaction_id or "").strip():
                    raise CancellationError(
                        "İade edilecek ürün ödeme kaleminin sağlayıcı "
                        "işlem kimliği bulunamadı."
                    )

            if refund_shipping and not str(
                shipping_items[0].provider_transaction_id or ""
            ).strip():
                raise CancellationError(
                    "Kargo ödeme kaleminin sağlayıcı işlem kimliği bulunamadı."
                )

        # PaymentRefund, stok ve iptal kayıtlarıyla aynı transaction
        # içerisinde oluşturulur. Provider çağrısı yapılmaz.
        payment_refund = None

        if total_refund_amount > Decimal("0.00"):
            payment_refund = PaymentRefund.objects.create(
                payment_transaction=payment_transaction,
                suborder=locked_suborder,
                amount=total_refund_amount,
                currency=payment_transaction.currency,
                status=RefundStatus.PENDING,
                reason=reason,
                refund_type=RefundType.SELLER_PARTIAL_CANCELLATION,
                refund_shipping=refund_shipping,
            )

        # Yalnızca iptal edilen miktar kadar stok geri yükle.
        try:
            for order_item_id, selected in selected_units.items():
                StockReservationService.restore_cancelled_quantity(
                    order_item_id=order_item_id,
                    quantity=len(selected),
                )

        except ReservationError as exc:
            raise CancellationError(
                "İptal sırasında stok durumu güncellenemedi."
            ) from exc

        # Her OrderItem için ayrı iptal kaydı, seçilen her birim için
        # de ayrı SubOrderItemCancellationUnit kaydı oluştur.
        cancellation_records = []

        for order_item_id in sorted(selected_units):
            selected = selected_units[order_item_id]
            order_item = order_items_by_id[order_item_id]

            cancellation = SubOrderItemCancellation.objects.create(
                order_item=order_item,
                cancelled_by=cancelled_by,
                quantity=len(selected),
                reason=reason,
                refund_amount=refund_by_order_item[order_item_id],
                payment_refund=payment_refund,
            )

            SubOrderItemCancellationUnit.objects.bulk_create(
                [
                    SubOrderItemCancellationUnit(
                        cancellation=cancellation,
                        order_item=order_item,
                        unit_number=unit_number,
                    )
                    for unit_number in selected
                ]
            )

            cancellation_records.append(cancellation)

        # İade üst kaydı ve tüm ödeme kalemi kırılımı aynı transaction'da
        # oluşturulur. Böylece sonraki bir iptal/iade, henüz child item'ları
        # oluşturulmamış bir PENDING refund'ı kapasite kontrolünün dışında
        # bırakamaz. Sağlayıcı çağrısı bu servis içinde yapılmaz.
        if payment_refund is not None:
            refund_item_breakdown_total = Decimal("0.00")

            for order_item_id in sorted(refund_by_order_item):
                item_amount = refund_by_order_item[order_item_id].quantize(
                    Decimal("0.01")
                )
                if item_amount <= Decimal("0.00"):
                    continue

                payment_item = product_payment_items_by_order_item[
                    order_item_id
                ][0]

                PaymentRefundItem.objects.create(
                    payment_refund=payment_refund,
                    payment_transaction_item=payment_item,
                    amount=item_amount,
                    status=RefundStatus.PENDING,
                    conversation_id=(
                        f"RF-{payment_refund.refund_reference}-"
                        f"{uuid4().hex[:24]}"
                    ),
                )
                refund_item_breakdown_total += item_amount

            if refund_shipping:
                PaymentRefundItem.objects.create(
                    payment_refund=payment_refund,
                    payment_transaction_item=shipping_items[0],
                    amount=shipping_refund_amount.quantize(
                        Decimal("0.01")
                    ),
                    status=RefundStatus.PENDING,
                    conversation_id=(
                        f"RF-{payment_refund.refund_reference}-"
                        f"{uuid4().hex[:24]}"
                    ),
                )
                refund_item_breakdown_total += shipping_refund_amount

            if refund_item_breakdown_total.quantize(Decimal("0.01")) != total_refund_amount:
                raise CancellationError(
                    "İptal refund kalemlerinin toplamı üst refund tutarıyla "
                    "eşleşmiyor."
                )

        # Alt sipariş ancak satın alınan bütün birimler iptal edildiğinde
        # CANCELLED yapılır.
        if fully_cancelled:
            locked_suborder.status = SubOrderStatus.CANCELLED
            locked_suborder.cancelled_at = timezone.now()

            locked_suborder.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "updated_at",
                ]
            )

            historical_product_refunds = (
                SubOrderItemCancellation.objects
                .filter(order_item__sub_order=locked_suborder)
                .aggregate(total=Sum("refund_amount"))
                .get("total")
                or Decimal("0.00")
            )

            # Özet kayıt, önceki kısmi iptal tutarlarını da kapsar.
            # payment_refund alanı bu işlemin refund kaydını gösterir.
            SubOrderCancellation.objects.create(
                suborder=locked_suborder,
                cancelled_by=cancelled_by,
                reason=reason,
                refund_amount=(
                    historical_product_refunds
                    + shipping_refund_amount
                ),
                payment_refund=payment_refund,
            )

        return cancellation_records

    # ------------------------------------------------------------------
    # Compatibility API: cancel all remaining units of a SubOrder
    # ------------------------------------------------------------------

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
        Geriye dönük uyumluluk metodu.

        Alt siparişte kalan bütün ürün birimlerini cancel_items() üzerinden
        iptal eder. Asıl iptal mantığı yalnızca cancel_items() içindedir.
        """

        locked_order = (
            Order.objects
            .select_for_update()
            .get(pk=suborder.order_id)
        )

        locked_suborder = (
            SubOrder.objects
            .select_for_update()
            .get(
                pk=suborder.pk,
                order=locked_order,
            )
        )

        cls._validate_suborder_state(locked_suborder)

        if SubOrderCancellation.objects.filter(
            suborder_id=locked_suborder.pk,
        ).exists():
            raise CancellationError(
                "Bu alt sipariş için daha önce bir iptal kaydı oluşturulmuş."
            )

        if ReturnRequest.objects.filter(
            order_item__sub_order=locked_suborder,
            status__in=cls.RESERVED_RETURN_STATUSES,
        ).exists():
            raise CancellationError(
                "İade sürecinde olan ürün birimleri bulunan alt sipariş "
                "tamamen iptal edilemez."
            )

        order_items = list(
            OrderItem.objects
            .filter(sub_order=locked_suborder)
            .order_by("pk")
        )

        if not order_items:
            raise CancellationError(
                "Bu alt siparişte iptal edilebilecek ürün bulunamadı."
            )

        cancelled_numbers_by_item = {}

        cancelled_unit_rows = (
            SubOrderItemCancellationUnit.objects
            .filter(
                order_item__sub_order=locked_suborder,
            )
            .values_list("order_item_id", "unit_number")
        )

        for order_item_id, unit_number in cancelled_unit_rows:
            cancelled_numbers_by_item.setdefault(
                order_item_id,
                set(),
            ).add(unit_number)

        remaining_items = {}

        for order_item in order_items:
            already_cancelled = len(
                cancelled_numbers_by_item.get(order_item.pk, set())
            )

            remaining_quantity = (
                order_item.quantity - already_cancelled
            )

            if remaining_quantity > 0:
                remaining_items[order_item.pk] = remaining_quantity

        if not remaining_items:
            raise CancellationError(
                "Bu alt siparişte iptal edilebilecek ürün kalmadı."
            )

        cls.cancel_items(
            suborder=locked_suborder,
            items=remaining_items,
            cancelled_by=cancelled_by,
            reason=reason,
        )

        cancellation = SubOrderCancellation.objects.filter(
            suborder_id=locked_suborder.pk,
        ).first()

        if cancellation is None:
            raise CancellationError(
                "Alt siparişin tüm ürün birimleri iptal edilemedi."
            )

        return cancellation
    # ------------------------------------------------------------------
    # Validation and money helpers
    # ------------------------------------------------------------------

    @classmethod
    def _validate_reason(cls, reason: str) -> str:
        reason = (reason or "").strip()

        if not reason:
            raise CancellationError(
                "İptal nedeni belirtilmelidir."
            )

        if len(reason) > 255:
            raise CancellationError(
                "İptal nedeni en fazla 255 karakter olabilir."
            )

        return reason

    @classmethod
    def _normalize_items(cls, items: Mapping) -> dict[int, int]:
        if not isinstance(items, Mapping) or not items:
            raise CancellationError(
                "İptal edilecek en az bir ürün miktarı belirtilmelidir."
            )

        normalized = {}

        for raw_item_id, quantity in items.items():
            if (
                isinstance(raw_item_id, bool)
                or not isinstance(raw_item_id, int)
                or raw_item_id <= 0
            ):
                raise CancellationError(
                    "Geçersiz sipariş kalemi kimliği."
                )

            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity <= 0
            ):
                raise CancellationError(
                    "İptal edilecek miktar pozitif bir tam sayı olmalıdır."
                )

            if raw_item_id in normalized:
                raise CancellationError(
                    "Aynı sipariş kalemi birden fazla kez belirtilmiş."
                )

            normalized[raw_item_id] = quantity

        return normalized

    @classmethod
    def _validate_suborder_state(cls, suborder: SubOrder) -> None:
        if suborder.status == SubOrderStatus.CANCELLED:
            raise CancellationError(
                "Bu alt sipariş zaten iptal edilmiş."
            )

        if suborder.status not in cls.CANCELLABLE_SUBORDER_STATES:
            raise CancellationError(
                "Bu sipariş mevcut durumunda iptal edilemez."
            )

    @classmethod
    def _get_successful_payment_transaction(
        cls,
        *,
        order: Order,
    ) -> PaymentTransaction:
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

    @staticmethod
    def _get_unit_refund_amount(
        *,
        paid_price: Decimal,
        quantity: int,
        unit_number: int,
    ) -> Decimal:
        """
        Tahsil edilen ürün tutarını kuruş hassasiyetinde birimlere dağıtır.
        Kalan kuruşlar ilk birimlere verilir.
        """

        if quantity < 1 or not 1 <= unit_number <= quantity:
            raise CancellationError(
                "Ürün biriminin iade tutarı hesaplanamadı."
            )

        paid_price = Decimal(
            paid_price
        ).quantize(Decimal("0.01"))

        if paid_price < Decimal("0.00"):
            raise CancellationError(
                "Ürünün ödeme tutarı geçersiz."
            )

        total_cents = int(
            paid_price * Decimal("100")
        )

        base_cents, remainder = divmod(
            total_cents,
            quantity,
        )

        unit_cents = base_cents + (
            1 if unit_number <= remainder else 0
        )

        return (
            Decimal(unit_cents) / Decimal("100")
        ).quantize(Decimal("0.01"))

    @classmethod
    def _shipping_refund_already_reserved(
        cls,
        *,
        payment_transaction: PaymentTransaction,
        suborder: SubOrder,
        shipping_item: PaymentTransactionItem,
    ) -> bool:
        """Kargo ödeme kaleminin tamamının rezerve edilip edilmediğini denetler.

        Kargo refund'ı bu akışta kısmi olamaz. Daha önce bir miktar ayrılmış
        ama kalan tutar varsa, ikinci bir kargo refund'ı üretmek yerine manuel
        mutabakat ister.
        """
        if (
            shipping_item.payment_transaction_id != payment_transaction.pk
            or shipping_item.suborder_id != suborder.pk
            or shipping_item.item_type != PaymentTransactionItemType.SHIPPING
            or shipping_item.order_item_id is not None
        ):
            raise CancellationError(
                "Kargo ödeme kalemi ilgili ödeme/alt siparişle eşleşmiyor."
            )

        if shipping_item.paid_price is None:
            raise CancellationError(
                "Kargo ödeme kaleminin tahsil edilen tutarı bulunamadı."
            )
        paid_amount = RefundService._money(shipping_item.paid_price)
        if paid_amount < Decimal("0.00"):
            raise CancellationError(
                "Kargo ödeme kaleminin tahsil edilen tutarı geçersiz."
            )

        try:
            reserved_amount = RefundService.get_reserved_payment_item_refund_amount(
                transaction_item=shipping_item,
            )
        except RefundError as exc:
            raise CancellationError(str(exc)) from exc

        if reserved_amount > paid_amount:
            raise CancellationError(
                "Kargo ödeme kalemindeki mevcut refund tutarları tahsilatı "
                "aşıyor; manuel mutabakat gerekli."
            )

        if reserved_amount == paid_amount:
            return True

        if reserved_amount > Decimal("0.00"):
            raise CancellationError(
                "Orijinal kargo bedeli için daha önce kısmi veya tutarsız "
                "bir refund kaydı bulundu; manuel mutabakat gerekli."
            )

        return False

    @classmethod
    def _validate_refund_amount(
        cls,
        *,
        payment_transaction: PaymentTransaction,
        refund_amount: Decimal,
    ) -> None:
        """RefundService ile aynı toplam-ödeme kapasite kuralını uygular."""
        try:
            RefundService.validate_payment_transaction_refund_capacity(
                payment_transaction=payment_transaction,
                refund_amount=refund_amount,
            )
        except RefundError as exc:
            raise CancellationError(str(exc)) from exc
