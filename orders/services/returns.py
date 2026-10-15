from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.db.models import Sum

from orders.exceptions import (
    ReturnOperationError,
    ReturnRequestNotFoundError, ReservationError
)
from orders.models import (
    CargoCompany,
    Order,
    OrderItem,
    ReturnDeliverySource,
    ReturnReason,
    ReturnRequest,
    ReturnStatus,
    SubOrder,
    SubOrderStatus,
    PaymentTransactionItem,
    PaymentStatus,
    PaymentTransactionItemType,
    PaymentRefundItem,
    PaymentRefund,
    RefundStatus,
    RefundType,
    SubOrderItemCancellationUnit,
)

from orders.services.refund import RefundError as RefundServiceError, RefundService
from orders.services.stock_reservation import StockReservationService

class ReturnService:
    """
    Müşteri iadelerinin iş kurallarını yönetir.

    Sorumlulukları:
        - İade talebi oluşturmak.
        - İade edilebilir ürün miktarını kontrol etmek.
        - İade kargo bilgilerini kaydetmek.
        - İade paketinin teslim alındığını kaydetmek.
        - Test ortamındaki teslimat simülasyonunu yönetmek.

    Sorumlu olmadığı işler:
        - HTTP response, redirect veya messages üretmek.
        - Kullanıcının oturum açmasını sağlamak.
        - Ödeme sağlayıcısına doğrudan istek göndermek.
        - Satıcı yetkilendirmesini yalnızca view katmanına bırakıp
          servis içinde mağaza sahipliğini göz ardı etmek.
    """

    # Bu durumlar satın alınmış ürün birimi üzerinde hak tutar.
    # COMPLETED, daha önce iade edilmiş bir birimi de tüketilmiş
    # saymamızı sağlar.
    QUANTITY_RESERVED_STATUSES = (
        ReturnStatus.AUTHORIZED,
        ReturnStatus.IN_TRANSIT,
        ReturnStatus.AWAITING_SELLER_REVIEW,
        ReturnStatus.DISPUTED,
        ReturnStatus.REFUND_PENDING,
        ReturnStatus.COMPLETED,
    )

    # =========================================================================
    # CREATE RETURN REQUEST
    # =========================================================================

    @classmethod
    @transaction.atomic
    def create_return_request(
        cls,
        *,
        order_item_id,
        requested_by,
        reason,
        customer_note="",
    ):
        """
        Satın alınmış tek bir ürün birimi için iade talebi oluşturur.

        Aynı OrderItem.quantity birden büyükse her ürün birimi için
        ayrı ReturnRequest açılabilir.

        Kilit sırası:
            Order -> SubOrder -> OrderItem

        Böylece aynı sipariş ürünü için eşzamanlı iade taleplerinin
        miktar kontrolünü atlaması önlenir.
        """

        reason = str(reason).strip()
        customer_note = (customer_note or "").strip()

        if reason not in ReturnReason.values:
            raise ReturnOperationError(
                "Geçersiz iade nedeni."
            )

        if reason == ReturnReason.OTHER and not customer_note:
            raise ReturnOperationError(
                "Diğer seçeneğinde iade nedeni açıklanmalıdır."
            )

        if requested_by is None or not getattr(
            requested_by,
            "is_authenticated",
            False,
        ):
            raise ReturnOperationError(
                "İade talebi oluşturmak için giriş yapmalısınız."
            )

        # Kilit sırasını belirleyebilmek için ilişkili ID'leri öğren.
        # Asıl kontroller ve kayıt işlemi kilitler alındıktan sonra yapılır.
        identity = (
            OrderItem.objects
            .filter(pk=order_item_id)
            .values(
                "sub_order_id",
                "sub_order__order_id",
            )
            .first()
        )

        if identity is None:
            raise ReturnOperationError(
                "İade edilecek sipariş ürünü bulunamadı."
            )

        order_id = identity["sub_order__order_id"]
        sub_order_id = identity["sub_order_id"]

        # -------------------------------------------------------------
        # LOCK ORDER -> SUBORDER -> ORDER ITEM
        # -------------------------------------------------------------

        try:
            order = (
                Order.objects
                .select_for_update()
                .get(pk=order_id)
            )

            suborder = (
                SubOrder.objects
                .select_for_update()
                .get(
                    pk=sub_order_id,
                    order_id=order.pk,
                )
            )

            order_item = (
                OrderItem.objects
                .select_for_update()
                .get(
                    pk=order_item_id,
                    sub_order_id=suborder.pk,
                )
            )

        except (
            Order.DoesNotExist,
            SubOrder.DoesNotExist,
            OrderItem.DoesNotExist,
        ) as exc:
            raise ReturnOperationError(
                "Sipariş ürünü artık bulunamıyor."
            ) from exc

        # -------------------------------------------------------------
        # CUSTOMER OWNERSHIP
        # -------------------------------------------------------------

        if order.user_id != requested_by.pk:
            raise ReturnOperationError(
                "Yalnızca kendi siparişinizdeki ürünler için "
                "iade talebi oluşturabilirsiniz."
            )

        # -------------------------------------------------------------
        # DELIVERY ELIGIBILITY
        # -------------------------------------------------------------

        if suborder.status != SubOrderStatus.DELIVERED:
            raise ReturnOperationError(
                "Yalnızca teslim edilmiş ürünler için "
                "iade talebi oluşturabilirsiniz."
            )

        # -------------------------------------------------------------
        # AVAILABLE UNIT
        # -------------------------------------------------------------

        # İade sürecinde olan veya tamamlanan birimler tekrar kullanılamaz.
        # Reddedilmiş ve iptal edilmiş iadelerin birimleri yeniden kullanılabilir.
        reserved_unit_rows = list(
            ReturnRequest.objects.filter(
                order_item=order_item,
                status__in=ReturnService.QUANTITY_RESERVED_STATUSES,
            ).values_list(
                "unit_number",
                flat=True,
            )
        )
        reserved_unit_numbers = set(reserved_unit_rows)
        if len(reserved_unit_rows) != len(reserved_unit_numbers):
            raise ReturnOperationError(
                "Aynı satın alınmış ürün birimi birden fazla iade kaydıyla "
                "rezerve edilmiş; manuel kontrol gerekli."
            )

        cancelled_unit_rows = list(
            SubOrderItemCancellationUnit.objects.filter(
                order_item=order_item,
            ).values_list(
                "unit_number",
                flat=True,
            )
        )
        cancelled_unit_numbers = set(cancelled_unit_rows)
        if len(cancelled_unit_rows) != len(cancelled_unit_numbers):
            raise ReturnOperationError(
                "Aynı satın alınmış ürün birimi birden fazla iptal kaydıyla "
                "işaretlenmiş; manuel kontrol gerekli."
            )

        used_unit_numbers = (
            reserved_unit_numbers | cancelled_unit_numbers
        )

        unit_number = next(
            (
                number
                for number in range(1, order_item.quantity + 1)
                if number not in used_unit_numbers
            ),
            None,
        )

        if unit_number is None:
            raise ReturnOperationError(
                "Bu üründen iade edilebilecek başka bir adet kalmadı."
            )

        # -------------------------------------------------------------
        # CREATE
        # -------------------------------------------------------------

        return ReturnRequest.objects.create(
            order_item=order_item,
            unit_number=unit_number,
            requested_by=requested_by,
            reason=reason,
            customer_note=customer_note,
            status=ReturnStatus.AUTHORIZED,
        )

    # =========================================================================
    # RETURN REQUEST IDENTITY / LOCKING
    # =========================================================================

    @classmethod
    def _get_return_identity(cls, *, return_request_id):
        """
        ReturnRequest'in kilit sırasını belirlemek için gerekli ID'leri alır.
        """

        identity = (
            ReturnRequest.objects
            .filter(pk=return_request_id)
            .values(
                "order_item__sub_order_id",
                "order_item__sub_order__order_id",
            )
            .first()
        )

        if identity is None:
            raise ReturnRequestNotFoundError(
                "İade talebi bulunamadı."
            )

        return identity

    @classmethod
    def _lock_return_request(
        cls,
        *,
        return_request_id,
    ):
        """
        Çağıran atomic transaction içindeyken kullanılır.

        Kilit sırası:
            Order -> SubOrder -> ReturnRequest
        """

        identity = cls._get_return_identity(
            return_request_id=return_request_id,
        )

        try:
            order = (
                Order.objects
                .select_for_update()
                .get(
                    pk=identity["order_item__sub_order__order_id"],
                )
            )

            suborder = (
                SubOrder.objects
                .select_for_update()
                .get(
                    pk=identity["order_item__sub_order_id"],
                    order_id=order.pk,
                )
            )

            return_request = (
                ReturnRequest.objects
                .select_for_update()
                .get(pk=return_request_id)
            )

        except (
            Order.DoesNotExist,
            SubOrder.DoesNotExist,
            ReturnRequest.DoesNotExist,
        ) as exc:
            raise ReturnRequestNotFoundError(
                "İade talebi bulunamadı."
            ) from exc

        # İade talebi farklı bir SubOrder'a bağlandıysa
        # eski kimlik bilgileri üzerinden işlem yapılmasını engelle.
        if return_request.order_item.sub_order_id != suborder.pk:
            raise ReturnOperationError(
                "İade talebinin sipariş ilişkisi değişmiş."
            )

        return order, suborder, return_request

    # =========================================================================
    # MARK RETURN SHIPPED
    # =========================================================================

    @classmethod
    @transaction.atomic
    def mark_return_shipped(
        cls,
        *,
        return_request_id,
        requested_by,
        cargo_company,
        tracking_number,
    ):
        """
        Müşterinin iade paketini kargoya verdiğini kaydeder.

        Bu işlem tek başına ürünün fiziksel olarak kargoya teslim
        edildiğini doğrulamaz; müşteri beyanını kaydeder.
        """

        cargo_company = (cargo_company or "").strip()
        tracking_number = (tracking_number or "").strip()

        if requested_by is None or not getattr(
            requested_by,
            "is_authenticated",
            False,
        ):
            raise ReturnOperationError(
                "İade kargo bilgisi girmek için giriş yapmalısınız."
            )

        if cargo_company not in CargoCompany.values:
            raise ReturnOperationError(
                "Geçersiz iade kargo firması."
            )

        if not tracking_number:
            raise ReturnOperationError(
                "İade kargo takip numarası girilmelidir."
            )

        if len(tracking_number) > 100:
            raise ReturnOperationError(
                "İade kargo takip numarası çok uzun."
            )

        _, _, return_request = cls._lock_return_request(
            return_request_id=return_request_id,
        )

        if return_request.requested_by_id != requested_by.pk:
            raise ReturnOperationError(
                "Yalnızca kendi iade talebinizin kargo bilgisini "
                "girebilirsiniz."
            )

        if return_request.status != ReturnStatus.AUTHORIZED:
            raise ReturnOperationError(
                "Bu iade talebi için kargo bilgisi girilemez."
            )

        now = timezone.now()

        return_request.return_cargo_company = cargo_company
        return_request.return_tracking_number = tracking_number
        return_request.return_shipped_at = now
        return_request.status = ReturnStatus.IN_TRANSIT

        return_request.save(
            update_fields=[
                "return_cargo_company",
                "return_tracking_number",
                "return_shipped_at",
                "status",
                "updated_at",
            ]
        )

        return return_request

    # =========================================================================
    # CONFIRM RETURN DELIVERY
    # =========================================================================

    @classmethod
    @transaction.atomic
    def confirm_return_delivered(
        cls,
        *,
        return_request_id,
        source=ReturnDeliverySource.SELLER,
        store=None,
        confirmed_by=None,
    ):
        """
        İade paketinin mağazaya ulaştığını kaydeder.

        SELLER:
            Satıcı teslim alındığını manuel onaylar.

        SIMULATED:
            Yalnızca simülasyon ayarı açıksa ve belirtilen bekleme
            süresi dolmuşsa test amacıyla teslimat kaydedilir.

        CARRIER:
            Henüz kullanılamaz; kargo entegrasyonu eklendiğinde
            ayrı bir doğrulama akışı üzerinden etkinleştirilecektir.
        """

        try:
            source = ReturnDeliverySource(source)
        except (TypeError, ValueError) as exc:
            raise ReturnOperationError(
                "Geçersiz teslimat doğrulama kaynağı."
            ) from exc

        if source == ReturnDeliverySource.CARRIER:
            raise ReturnOperationError(
                "Kargo firması üzerinden teslimat doğrulaması "
                "henüz desteklenmiyor."
            )

        _, suborder, return_request = cls._lock_return_request(
            return_request_id=return_request_id,
        )

        if return_request.status != ReturnStatus.IN_TRANSIT:
            raise ReturnOperationError(
                "Yalnızca kargoya verilmiş iadeler teslim alındı "
                "olarak işaretlenebilir."
            )

        # -------------------------------------------------------------
        # SELLER CONFIRMATION
        # -------------------------------------------------------------

        if source == ReturnDeliverySource.SELLER:
            # İşlem yapılan mağaza, iadenin ait olduğu mağaza olmalı.
            if store is None or store.pk != suborder.store_id:
                raise ReturnOperationError(
                    "Bu iade başka bir mağazaya ait."
                )

            # İşlemi yapan kullanıcının oturumu açık olmalı.
            if confirmed_by is None or not getattr(
                confirmed_by,
                "is_authenticated",
                False,
            ):
                raise ReturnOperationError(
                    "Teslimat onayı için giriş yapmalısınız."
                )

            # Mağazanın sahibi işlemi yapan kullanıcı olmalı.
            if store.seller.user_id != confirmed_by.pk:
                raise ReturnOperationError(
                    "Bu mağaza için teslimat onayı verme yetkiniz yok."
                )

            review_started_at = timezone.now()

        # -------------------------------------------------------------
        # TEST DELIVERY SIMULATION
        # -------------------------------------------------------------

        else:
            if not getattr(
                settings,
                "RETURN_SIMULATED_DELIVERY_ENABLED",
                False,
            ):
                raise ReturnOperationError(
                    "Otomatik teslimat simülasyonu kapalı."
                )

            if return_request.return_shipped_at is None:
                raise ReturnOperationError(
                    "İade kargoya verilmeden otomatik teslimat "
                    "gerçekleştirilemez."
                )

            delay_minutes = getattr(
                settings,
                "RETURN_SIMULATED_DELIVERY_DELAY_MINUTES",
                None,
            )

            if (
                not isinstance(delay_minutes, int)
                or isinstance(delay_minutes, bool)
                or delay_minutes < 0
            ):
                raise ReturnOperationError(
                    "İade teslimat simülasyonu süresi "
                    "settings.py içinde geçerli bir tam sayı olmalıdır."
                )

            review_started_at = timezone.now()
            delivery_due_at = (
                return_request.return_shipped_at
                + timedelta(minutes=delay_minutes)
            )

            if review_started_at < delivery_due_at:
                raise ReturnOperationError(
                    "Otomatik teslimat için bekleme süresi dolmadı."
                )

        # -------------------------------------------------------------
        # UPDATE
        # -------------------------------------------------------------

        review_timeout_minutes = getattr(
            settings,
            "RETURN_SELLER_REVIEW_TIMEOUT_MINUTES",
            None,
        )

        if (
            not isinstance(review_timeout_minutes, int)
            or isinstance(review_timeout_minutes, bool)
            or review_timeout_minutes < 0
        ):
            raise ReturnOperationError(
                "Satıcı inceleme süresi settings.py içinde "
                "geçerli bir tam sayı olarak tanımlanmalıdır."
            )

        return_request.delivered_at = review_started_at
        return_request.delivery_confirmation_source = source
        return_request.delivery_confirmed_by = (
            confirmed_by if source == ReturnDeliverySource.SELLER else None
        )
        return_request.seller_review_deadline = (
            review_started_at
            + timedelta(minutes=review_timeout_minutes)
        )
        return_request.status = ReturnStatus.AWAITING_SELLER_REVIEW

        return_request.save(
            update_fields=[
                "delivered_at",
                "delivery_confirmation_source",
                "delivery_confirmed_by",
                "seller_review_deadline",
                "status",
                "updated_at",
            ]
        )

        return return_request

    @classmethod
    def _get_shipping_refund_item(
        cls,
        *,
        suborder,
        return_request,
        payment_item,
    ):
        """
        Bu iade onayında orijinal kargo bedelinin de iade edilip
        edilmeyeceğini belirler.

        Kargo bedeli yalnızca bir kez iade edilir. Bekleyen veya
        sonucu belirsiz bir kargo iadesi varsa tekrar oluşturulmaz.
        """

        # Kusurlu / yanlış / hasarlı ürün gibi nedenlerde kargo
        # bedeli, daha önce iade edilmediyse ilk onayda eklenebilir.
        shipping_eligible_reasons = {
            "defective",
            "wrong_item",
            "damaged",
            "not_as_described",
        }

        should_refund_shipping = (
            return_request.reason in shipping_eligible_reasons
        )

        # Cayma ve diğer nedenlerde kargo iadesi için alt siparişteki
        # bütün ürün adetlerinin iadesi onaylanmış olmalıdır.
        if not should_refund_shipping:
            total_units = (
                OrderItem.objects
                .filter(sub_order_id=suborder.pk)
                .aggregate(total=Sum("quantity"))["total"]
                or 0
            )

            # İptal edilmiş fiziksel birimleri belirle.
            # Aynı birim için mükerrer kayıt varsa bir kez say.
            cancelled_unit_rows = list(
                SubOrderItemCancellationUnit.objects
                .filter(
                    order_item__sub_order_id=suborder.pk,
                )
                .values_list(
                    "order_item_id",
                    "unit_number",
                )
            )
            cancelled_unit_keys = set(cancelled_unit_rows)

            if len(cancelled_unit_rows) != len(cancelled_unit_keys):
                raise ReturnOperationError(
                    "İptal edilmiş ürün birimlerinde mükerrer kayıt bulundu."
                )

            if len(cancelled_unit_keys) > total_units:
                raise ReturnOperationError(
                    "İptal edilmiş ürün birimlerinin sayısı "
                    "alt sipariş miktarıyla tutarsız."
                )

            # İade edilebilir birim sayısı:
            # toplam birim - iptal edilmiş birimler.
            eligible_units = total_units - len(cancelled_unit_keys)

            # Daha önce kabul edilmiş iadelerin benzersiz birimlerini al.
            accepted_unit_rows = list(
                ReturnRequest.objects
                .filter(
                    order_item__sub_order_id=suborder.pk,
                    status__in=[
                        ReturnStatus.REFUND_PENDING,
                        ReturnStatus.COMPLETED,
                    ],
                )
                .exclude(
                    pk=return_request.pk,
                )
                .values_list(
                    "order_item_id",
                    "unit_number",
                )
            )
            accepted_unit_keys = set(accepted_unit_rows)

            if len(accepted_unit_rows) != len(accepted_unit_keys):
                raise ReturnOperationError(
                    "Daha önce kabul edilmiş iadelerde mükerrer ürün birimi "
                    "bulundu; manuel kontrol gerekli."
                )

            # Bu talep henüz REFUND_PENDING durumuna geçirilmedi;
            # onaylanmakta olan birim de hesaba katılmalı.
            current_unit_key = (
                return_request.order_item_id,
                return_request.unit_number,
            )
            if current_unit_key in accepted_unit_keys:
                raise ReturnOperationError(
                    "Bu ürün birimi için daha önce para iadesi kabul edilmiş."
                )
            accepted_unit_keys.add(current_unit_key)

            # İptal edilmiş birimleri kabul edilmiş iade sayısına katma.
            accepted_units = len(
                accepted_unit_keys - cancelled_unit_keys
            )

            should_refund_shipping = (
                eligible_units > 0
                and accepted_units >= eligible_units
            )

        if not should_refund_shipping:
            return None

        # Orijinal kargo bedeli, ürün bedeliyle aynı ödeme işleminin
        # ve aynı alt siparişin ödeme kaleminden alınır.
        shipping_items = list(
            PaymentTransactionItem.objects
            .select_for_update()
            .filter(
                payment_transaction_id=payment_item.payment_transaction_id,
                suborder_id=suborder.pk,
                item_type=PaymentTransactionItemType.SHIPPING,
            )
            .order_by("pk")[:2]
        )

        if not shipping_items:
            if suborder.shipping_amount == Decimal("0.00"):
                return None

            raise ReturnOperationError(
                "Kargo ücreti kayıtlı olduğu hâlde başarılı "
                "ödemede karşılık gelen kargo kalemi bulunamadı."
            )

        if len(shipping_items) != 1:
            raise ReturnOperationError(
                "Kargo iadesi için birden fazla orijinal "
                "kargo ödeme kalemi bulundu."
            )

        shipping_item = shipping_items[0]

        if shipping_item.order_item_id is not None:
            raise ReturnOperationError(
                "Kargo ödeme kalemi beklenen veri yapısında değil."
            )

        if shipping_item.paid_price is None:
            raise ReturnOperationError(
                "Orijinal kargo ödeme kaleminin tahsilat tutarı yok."
            )

        shipping_paid_amount = RefundService._money(
            shipping_item.paid_price
        )

        if shipping_paid_amount == Decimal("0.00"):
            if suborder.shipping_amount == Decimal("0.00"):
                return None

            raise ReturnOperationError(
                "Siparişte kargo ücreti görünmesine rağmen "
                "ödeme kaleminin tutarı sıfır."
            )

        if shipping_paid_amount < Decimal("0.00"):
            raise ReturnOperationError(
                "Orijinal kargo ödeme kaleminin tutarı geçersiz."
            )

        if not shipping_item.provider_transaction_id:
            raise ReturnOperationError(
                "Orijinal kargo ödeme kaleminin sağlayıcı "
                "işlem bilgisi bulunamadı."
            )


        # RefundService, hem materialize edilmiş child item'ları hem de eski
        # child breakdown'ı olmayan parent refund kayıtlarını hesaba katar.
        try:
            reserved_shipping_amount = (
                RefundService.get_reserved_payment_item_refund_amount(
                    transaction_item=shipping_item,
                )
            )
        except RefundServiceError as exc:
            raise ReturnOperationError(str(exc)) from exc

        if reserved_shipping_amount > shipping_paid_amount:
            raise ReturnOperationError(
                "Orijinal kargo ödeme kalemine ait refund tutarları "
                "tahsil edilen tutarı aşıyor; manuel mutabakat gerekli."
            )

        if reserved_shipping_amount == shipping_paid_amount:
            # Kargo bedeli zaten tamamen iade edilmiş veya ayrılmış.
            return None

        if reserved_shipping_amount > Decimal("0.00"):
            # Kargo tutarının bu akışta kısmi olarak rezerve edilmesi beklenmez.
            raise ReturnOperationError(
                "Orijinal kargo bedeli için daha önce kısmi veya tutarsız "
                "bir refund kaydı bulundu; manuel mutabakat gerekli."
            )

        return shipping_item

    @classmethod
    @transaction.atomic
    def approve_return(
        cls,
        *,
        return_request_id,
        store,
        approved_by,
    ):
        """
        Satıcı iade talebini onaylar ve birim bazlı para iadesini hazırlar.

        Bu metot ödeme sağlayıcısına istek göndermez.
        PaymentRefund ve PaymentRefundItem kayıtlarını hazırlar.
        Sağlayıcı işlemi transaction tamamlandıktan sonra başlatılmalıdır.
        """

        if approved_by is None or not getattr(
            approved_by,
            "is_authenticated",
            False,
        ):
            raise ReturnOperationError(
                "İade onayı için giriş yapmalısınız."
            )

        if store is None:
            raise ReturnOperationError(
                "İade için mağaza bilgisi bulunamadı."
            )

        order, suborder, return_request = cls._lock_return_request(
            return_request_id=return_request_id,
        )

        # -------------------------------------------------------------
        # STORE OWNERSHIP
        # -------------------------------------------------------------

        if store.pk != suborder.store_id:
            raise ReturnOperationError(
                "Bu iade başka bir mağazaya ait."
            )

        if store.seller.user_id != approved_by.pk:
            raise ReturnOperationError(
                "Bu mağaza için iade onaylama yetkiniz yok."
            )

        # -------------------------------------------------------------
        # RETURN AND ORDER STATUS
        # -------------------------------------------------------------

        if return_request.status != ReturnStatus.AWAITING_SELLER_REVIEW:
            raise ReturnOperationError(
                "Yalnızca satıcı incelemesindeki iadeler onaylanabilir."
            )

        if suborder.status != SubOrderStatus.DELIVERED:
            raise ReturnOperationError(
                "Bu alt sipariş teslim edilmiş durumda olmadığı için "
                "iade onaylanamaz."
            )

        if return_request.payment_refund_id is not None:
            raise ReturnOperationError(
                "Bu iade talebi için para iadesi zaten oluşturulmuş."
            )

        # -------------------------------------------------------------
        # ORDER ITEM REFERENCE
        # -------------------------------------------------------------
        # Henüz OrderItem için ayrıca select_for_update kullanmıyoruz.
        # Withdrawal durumunda önce stok servisinin StoreProduct,
        # ardından OrderItem kilidi alması gerekiyor.
        # -------------------------------------------------------------

        order_item_reference = (
            OrderItem.objects
            .filter(
                pk=return_request.order_item_id,
                sub_order_id=suborder.pk,
            )
            .first()
        )

        if order_item_reference is None:
            raise ReturnOperationError(
                "İade edilen sipariş kalemi bulunamadı."
            )

        if not (
            1 <= return_request.unit_number
            <= order_item_reference.quantity
        ):
            raise ReturnOperationError(
                "İade talebindeki ürün birimi geçersiz."
            )

        # İptal edilmiş ürün birimi iade edilemez.
        if SubOrderItemCancellationUnit.objects.filter(
            order_item_id=order_item_reference.pk,
            unit_number=return_request.unit_number,
        ).exists():
            raise ReturnOperationError(
                "İptal edilmiş ürün birimi için iade işlemi yapılamaz."
            )

        duplicate_unit_return_exists = (
            ReturnRequest.objects
            .filter(
                order_item_id=order_item_reference.pk,
                unit_number=return_request.unit_number,
                status__in=cls.QUANTITY_RESERVED_STATUSES,
            )
            .exclude(pk=return_request.pk)
            .exists()
        )
        if duplicate_unit_return_exists:
            raise ReturnOperationError(
                "Aynı satın alınmış ürün birimi için başka bir iade talebi "
                "zaten stok veya para iadesi hakkı tutuyor; manuel kontrol gerekli."
            )

        # -------------------------------------------------------------
        # LOCK SUCCESSFUL PAYMENT ITEM
        # -------------------------------------------------------------

        payment_items = list(
            PaymentTransactionItem.objects
            .select_for_update()
            .select_related("payment_transaction")
            .filter(
                payment_transaction__order_id=order.pk,
                payment_transaction__provider=RefundService.PROVIDER,
                payment_transaction__status__in=[
                    PaymentStatus.SUCCESS,
                    PaymentStatus.PARTIALLY_REFUNDED,
                ],
                suborder_id=suborder.pk,
                order_item_id=order_item_reference.pk,
                item_type=PaymentTransactionItemType.PRODUCT,
            )
            .order_by(
                "-payment_transaction__created_at",
                "-pk",
            )[:2]
        )

        if len(payment_items) != 1:
            raise ReturnOperationError(
                "İade için tek ve kesin bir başarılı ödeme kalemi "
                "bulunamadı."
            )

        payment_item = payment_items[0]

        if payment_item.paid_price is None:
            raise ReturnOperationError(
                "Ürün ödeme kaleminin tahsil edilen tutarı bulunamadı."
            )
        if RefundService._money(payment_item.paid_price) < Decimal("0.00"):
            raise ReturnOperationError(
                "Ürün ödeme kaleminin tahsil edilen tutarı geçersiz."
            )
        if not str(payment_item.provider_transaction_id or "").strip():
            raise ReturnOperationError(
                "Ürün ödeme kaleminin sağlayıcı işlem kimliği bulunamadı."
            )
        if not str(
            payment_item.payment_transaction.payment_id or ""
        ).strip():
            raise ReturnOperationError(
                "Ödeme işleminde iyzico payment_id bilgisi bulunamadı."
            )

        # -------------------------------------------------------------
        # RESTORE RETURNED STOCK
        # -------------------------------------------------------------
        # Yalnızca cayma hakkı (WITHDRAWAL) iadelerinde stok geri yüklenir.
        #
        # Önemli kilit sırası:
        # Order -> StoreProduct -> OrderItem
        #
        # Bu nedenle restore_returned_item() çağrısından önce
        # OrderItem üzerinde select_for_update kullanmıyoruz.
        # -------------------------------------------------------------

        if return_request.reason == ReturnReason.WITHDRAWAL:
            # Cayma iadesi yeniden satılabilir kabul edildiği için hem stock
            # artar hem sold_count azalır. Her iki muhasebe etkisini ayrı ayrı
            # izleyebilmek için iki timestamp de kaydedilir.
            if return_request.stock_restored_at is not None:
                raise ReturnOperationError(
                    "Bu iade için stok zaten geri yüklenmiş."
                )
            if return_request.sold_count_adjusted_at is not None:
                raise ReturnOperationError(
                    "Bu iade için satış sayacı zaten düzeltilmiş."
                )

            try:
                StockReservationService.restore_returned_item(
                    order=order,
                    order_item_id=order_item_reference.pk,
                    quantity=1,
                )
            except ReservationError as exc:
                raise ReturnOperationError(
                    "İade edilen ürünün stoğu güncellenemedi."
                ) from exc

            now = timezone.now()
            return_request.stock_restored_at = now
            return_request.sold_count_adjusted_at = now
        else:
            # Kusurlu/yanlış/hasarlı/açıklamaya uymayan ürünler satılabilir
            # stoğa dönmez. Ancak net satış adedi azaltılır.
            if return_request.sold_count_adjusted_at is not None:
                raise ReturnOperationError(
                    "Bu iade için satış sayacı zaten düzeltilmiş."
                )

            try:
                StockReservationService.decrement_sold_count_for_return(
                    order=order,
                    order_item_id=order_item_reference.pk,
                    quantity=1,
                )
            except ReservationError as exc:
                raise ReturnOperationError(
                    "İade edilen ürünün satış sayacı güncellenemedi."
                ) from exc

            return_request.sold_count_adjusted_at = timezone.now()

        # -------------------------------------------------------------
        # LOCK ORDER ITEM
        # -------------------------------------------------------------
        # Withdrawal durumunda stok servisi bu satırı zaten kilitlemiştir.
        # Burada kilidin tekrar istenmesi aynı transaction içinde güvenlidir.
        # Diğer iade nedenlerinde bu, OrderItem üzerindeki ilk açık kilittir.
        # -------------------------------------------------------------

        try:
            order_item = (
                OrderItem.objects
                .select_for_update()
                .get(
                    pk=return_request.order_item_id,
                    sub_order_id=suborder.pk,
                )
            )
        except OrderItem.DoesNotExist as exc:
            raise ReturnOperationError(
                "İade edilen sipariş kalemi bulunamadı."
            ) from exc

        # Kilit sonrasında birim sınırını tekrar doğrula.
        if not (
            1 <= return_request.unit_number <= order_item.quantity
        ):
            raise ReturnOperationError(
                "İade talebindeki ürün birimi geçersiz."
            )


        # -------------------------------------------------------------
        # UNIT REFUND AMOUNT
        # -------------------------------------------------------------

        unit_amount = RefundService._calculate_unit_refund_amount(
            paid_price=payment_item.paid_price,
            quantity=order_item.quantity,
            unit_number=return_request.unit_number,
        )

        if unit_amount <= Decimal("0.00"):
            raise ReturnOperationError(
                "Bu ürün birimi için hesaplanan tahsilat tutarı "
                "sıfır olduğundan standart para iadesi oluşturulamıyor."
            )

        # -------------------------------------------------------------
        # CHECK PREVIOUSLY RESERVED REFUNDS
        # -------------------------------------------------------------

        try:
            RefundService.validate_payment_item_refund_capacity(
                transaction_item=payment_item,
                proposed_amount=unit_amount,
            )
        except RefundServiceError as exc:
            raise ReturnOperationError(str(exc)) from exc

        # -------------------------------------------------------------
        # ORIGINAL SHIPPING REFUND
        # -------------------------------------------------------------

        shipping_item = cls._get_shipping_refund_item(
            suborder=suborder,
            return_request=return_request,
            payment_item=payment_item,
        )

        refund_shipping = shipping_item is not None

        shipping_amount = (
            RefundService._money(shipping_item.paid_price)
            if shipping_item is not None
            else Decimal("0.00")
        )

        refund_amount = RefundService._money(
            unit_amount + shipping_amount
        )

        try:
            RefundService.validate_payment_transaction_refund_capacity(
                payment_transaction=payment_item.payment_transaction,
                refund_amount=refund_amount,
            )
            if shipping_item is not None:
                RefundService.validate_payment_item_refund_capacity(
                    transaction_item=shipping_item,
                    proposed_amount=shipping_amount,
                )
        except RefundServiceError as exc:
            raise ReturnOperationError(str(exc)) from exc

        # -------------------------------------------------------------
        # CREATE REFUND
        # -------------------------------------------------------------

        refund = PaymentRefund.objects.create(
            payment_transaction=payment_item.payment_transaction,
            suborder=suborder,
            amount=refund_amount,
            currency=payment_item.payment_transaction.currency,
            status=RefundStatus.PENDING,
            reason=(
                f"Müşteri ürün iadesi: "
                f"{return_request.get_reason_display()}"
                + (
                    " (orijinal kargo bedeli dahil)"
                    if refund_shipping
                    else ""
                )
            ),
            refund_type=RefundType.CUSTOMER_REQUEST,
            refund_shipping=refund_shipping,
        )

        # -------------------------------------------------------------
        # UPDATE RETURN REQUEST
        # -------------------------------------------------------------

        return_request.payment_refund = refund
        return_request.status = ReturnStatus.REFUND_PENDING
        return_request.seller_approved_at = timezone.now()
        return_request.seller_approved_by = approved_by

        return_request.save(
            update_fields=[
                "payment_refund",
                "status",
                "seller_approved_at",
                "seller_approved_by",
                "stock_restored_at",
                "sold_count_adjusted_at",
                "updated_at",
            ]
        )

        # -------------------------------------------------------------
        # CREATE PRODUCT REFUND ITEM
        # -------------------------------------------------------------
        # Ürün iadesi, yalnızca iade edilen birimin tutarıyla kaydedilir.
        # -------------------------------------------------------------

        PaymentRefundItem.objects.create(
            payment_refund=refund,
            payment_transaction_item=payment_item,
            amount=unit_amount,
            status=RefundStatus.PENDING,
            conversation_id=(
                f"RET-{return_request.reference.hex}-"
                f"{uuid4().hex[:24]}"
            ),
        )

        # -------------------------------------------------------------
        # CREATE SHIPPING REFUND ITEM
        # -------------------------------------------------------------

        if shipping_item is not None:
            PaymentRefundItem.objects.create(
                payment_refund=refund,
                payment_transaction_item=shipping_item,
                amount=shipping_amount,
                status=RefundStatus.PENDING,
                conversation_id=(
                    f"RET-{return_request.reference.hex}-"
                    f"{uuid4().hex[:24]}"
                ),
            )

        return return_request

    @classmethod
    @transaction.atomic
    def dispute_return(
        cls,
        *,
        return_request_id,
        store,
        disputed_by,
        reason,
    ):
        """
        Satıcı, inceleme süresi içindeki iade talebine itiraz eder.

        İtiraz edilen iade DISPUTED durumuna alınır.
        Para iadesi oluşturulmaz.
        """

        if disputed_by is None or not getattr(
            disputed_by,
            "is_authenticated",
            False,
        ):
            raise ReturnOperationError(
                "İtiraz etmek için giriş yapmalısınız."
            )

        if store is None:
            raise ReturnOperationError(
                "İade için mağaza bilgisi bulunamadı."
            )

        reason = (reason or "").strip()

        if not reason:
            raise ReturnOperationError(
                "İtiraz gerekçesi girilmelidir."
            )

        if len(reason) > 2000:
            raise ReturnOperationError(
                "İtiraz gerekçesi en fazla 2000 karakter olabilir."
            )

        _, suborder, return_request = cls._lock_return_request(
            return_request_id=return_request_id,
        )

        # Mağaza doğrulaması
        if store.pk != suborder.store_id:
            raise ReturnOperationError(
                "Bu iade başka bir mağazaya ait."
            )

        if store.seller.user_id != disputed_by.pk:
            raise ReturnOperationError(
                "Bu mağaza için itiraz etme yetkiniz yok."
            )

        # Yalnızca inceleme aşamasındaki iadeye itiraz edilebilir.
        if (
            return_request.status
            != ReturnStatus.AWAITING_SELLER_REVIEW
        ):
            raise ReturnOperationError(
                "Yalnızca satıcı incelemesindeki iadeye "
                "itiraz edebilirsiniz."
            )

        # İnceleme süresi sona ermişse itiraz kabul edilmez.
        deadline = return_request.seller_review_deadline

        if deadline is None:
            raise ReturnOperationError(
                "İade talebinin inceleme son tarihi bulunamadı."
            )

        now = timezone.now()

        if deadline <= now:
            raise ReturnOperationError(
                "Satıcı inceleme süresi dolduğu için "
                "bu iadeye artık itiraz edemezsiniz."
            )

        if return_request.payment_refund_id is not None:
            raise ReturnOperationError(
                "Para iadesi oluşturulmuş bir talebe itiraz edilemez."
            )

        return_request.status = ReturnStatus.DISPUTED
        return_request.disputed_at = now
        return_request.disputed_by = disputed_by
        return_request.dispute_reason = reason

        return_request.save(
            update_fields=[
                "status",
                "disputed_at",
                "disputed_by",
                "dispute_reason",
                "updated_at",
            ]
        )

        return return_request
