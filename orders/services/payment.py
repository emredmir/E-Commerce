import ipaddress
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import iyzipay

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    IyzicoOnboardingStatus,
)
from orders.exceptions import (
    InvalidOrderStateError,
    PaymentAlreadyInProgressError,
    PaymentAlreadyProcessedError,
    PaymentError,
    PaymentGatewayError,
    PaymentInitializationError,
    PaymentValidationError,
    PaymentVerificationError,
    ReservationError,
    StoredCardNotFoundError,
)
from orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    PaymentTransaction,
    ReservationStatus,
    StockReservation,
    StoredCard,
)

from .stock_reservation import StockReservationService


logger = logging.getLogger(__name__)


# =============================================================================
# DATA TRANSFER OBJECTS
# =============================================================================


@dataclass(frozen=True)
class PaymentBuyerData:
    """
    iyzico buyer bilgileri.

    identity_number:
        Turkish buyer için TCKN.
        Yabancı müşteri desteği eklenirse burada pasaport vb.
        kullanılabilir.

    registration_address:
        Buyer'ın kayıt adresi.
        Bu değer billing/shipping address ile otomatik eşitlenmez;
        caller tarafından açıkça verilmelidir.
    """

    name: str
    surname: str
    registration_address: str
    ip: str


@dataclass(frozen=True)
class PaymentCardData:
    """
    Yeni kart ile ödeme için gerekli bilgiler.

    ÖNEMLİ:
        Bu bilgiler hiçbir şekilde DB'ye kaydedilmez.
        Özellikle card_number ve cvc saklanmaz.
    """

    card_holder_name: str
    card_number: str
    expire_month: str
    expire_year: str
    cvc: str


@dataclass(frozen=True)
class StoredCardPaymentData:
    """
    Kayıtlı kart ile ödeme için provider'a gönderilecek bilgiler.

    ÖNEMLİ:
        PAN / expiry / CVC içermez.
        Yalnızca iyzico Card Storage tarafından üretilen
        cardUserKey ve cardToken kullanılır.
    """

    card_user_key: str
    card_token: str


@dataclass(frozen=True)
class PaymentInitializationResult:
    """
    Frontend'e 3DS ekranını başlatmak için dönen sonuç.
    """

    payment_transaction_id: int
    payment_id: str
    conversation_id: str
    three_ds_html_content: str


@dataclass(frozen=True)
class PaymentCompletionResult:
    """
    Başarılı 3DS ödeme tamamlama sonucu.
    """

    payment_transaction_id: int
    order_id: int
    order_number: str
    payment_id: str
    conversation_id: str
    paid_price: Decimal

# =============================================================================
# PAYMENT SERVICE
# =============================================================================


class PaymentService:
    """
    iyzico Marketplace ödeme akışını yöneten service.

    Kapsam:

        Order
            ↓
        PaymentTransaction
            ↓
        iyzico 3DS Initialize
            ↓
        3DS callback
            ↓
        iyzico 3DS Completion
            ↓
        PaymentTransaction = SUCCESS
            ↓
        StockReservation consume
            ↓
        Order = PAID

    Bu servis:
        - HTTP response üretmez.
        - Template render etmez.
        - Django messages kullanmaz.
        - Kart PAN/CVC saklamaz.
        - DB transaction'ını iyzico HTTP çağrısı boyunca açık tutmaz.
        - Ödeme doğrulaması sonrasında reservation consume edebilir.
        - Başarılı ödeme sonrasında Order'ı PAID yapabilir.
    """

    PROVIDER = "iyzico"

    PAYMENT_METHOD_NEW_CARD = "new_card"
    PAYMENT_METHOD_STORED_CARD = "stored_card"

    LOCALE = "tr"
    CURRENCY = "TRY"

    PAYMENT_CHANNEL = "WEB"
    PAYMENT_GROUP = "PRODUCT"

    ITEM_TYPE_PHYSICAL = "PHYSICAL"

    SUPPORTED_INSTALLMENTS = (
        1,
        2,
        3,
        6,
        9,
        12,
    )

    MONEY_QUANTUM = Decimal("0.01")

    # -------------------------------------------------------------------------
    # PROVIDER VALIDATION ERROR CODES
    # -------------------------------------------------------------------------

    INITIALIZE_VALIDATION_ERROR_CODES = frozenset({
        "3",
        "4",
        "5",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "19",
        "20",
        "21",
        "22",
        "23",
        "25",
        "26",
        "27",
    })

    # -------------------------------------------------------------------------
    # PUBLIC API
    # -------------------------------------------------------------------------

    @classmethod
    def initialize_3ds(
        cls,
        *,
        order_id,
        buyer: PaymentBuyerData,
        payment_method=PAYMENT_METHOD_NEW_CARD,
        payment_card: PaymentCardData | None = None,
        stored_card_id=None,
        installment=1,
    ):
        """
        Order için iyzico 3DS ödeme başlatır.

        Akış:

            local validation
                ↓
            PaymentTransaction(INITIATED)
                ↓
            iyzico /payment/3dsecure/initialize
                ↓
            success
                ↓
            PaymentTransaction(PENDING)
                ↓
            threeDSHtmlContent

        Payment method:

            new_card
                ↓
            PaymentCardData

            stored_card
                ↓
            StoredCard
                ↓
            cardUserKey + cardToken
        """

        cls._validate_settings()

        payment_method = cls._normalize_payment_method(
            payment_method
        )

        installment = cls._normalize_installment(
            installment
        )

        cls._validate_buyer(
            buyer=buyer,
        )

        if payment_method == cls.PAYMENT_METHOD_NEW_CARD:
            if stored_card_id is not None:
                raise PaymentValidationError(
                    "Yeni kart ödemesinde stored_card_id gönderilemez."
                )

            if not isinstance(
                payment_card,
                PaymentCardData,
            ):
                raise PaymentValidationError(
                    "Yeni kart ödemesi için kart bilgileri gereklidir."
                )

            cls._validate_card(
                payment_card=payment_card,
            )

        else:
            if payment_card is not None:
                raise PaymentValidationError(
                    "Kayıtlı kart ödemesinde yeni kart bilgileri gönderilemez."
                )

            if stored_card_id is None:
                raise PaymentValidationError(
                    "Kayıtlı kart seçilmelidir."
                )

        (
            order,
            payment_transaction,
            request_payload,
        ) = cls._prepare_payment(
            order_id=order_id,
            buyer=buyer,
            payment_method=payment_method,
            payment_card=payment_card,
            stored_card_id=stored_card_id,
            installment=installment,
        )

        # ---------------------------------------------------------------------
        # IMPORTANT:
        #
        # iyzico HTTP çağrısı burada, transaction dışında yapılır.
        # ---------------------------------------------------------------------

        try:
            response = cls._initialize_remote(
                request_payload=request_payload,
            )

        except PaymentGatewayError:
            # PaymentTransaction INITIATED durumda kalır.
            #
            # Bunun nedeni:
            # remote isteğin iyzico tarafına ulaşıp ulaşmadığını
            # burada kesin olarak bilemememizdir.
            #
            # Körlemesine ikinci CREATE/initialize yapılmaz.
            raise

        return cls._handle_initialize_response(
            payment_transaction_id=payment_transaction.id,
            order=order,
            payment_transaction=payment_transaction,
            response=response,
            request_payload=request_payload,
        )

    @classmethod
    def complete_3ds(
        cls,
        *,
        payment_id,
        conversation_id,
        conversation_data="",
    ):
        """
        iyzico 3DS authentication sonrasında payment'ı tamamlar.

        Akış:

            callback
                ↓
            PaymentTransaction doğrulama
                ↓
            iyzico ThreedsPayment.create()
                ↓
            provider response validation
                ↓
            signature validation
                ↓
            PaymentTransaction = SUCCESS
                ↓
            StockReservationService.consume_order()
                ↓
            Order = PAID

        ÖNEMLİ:
            iyzico HTTP çağrısı transaction dışında yapılır.
        """

        cls._validate_settings()

        payment_id = cls._clean(payment_id)
        conversation_id = cls._clean(conversation_id)
        conversation_data = cls._clean(conversation_data)

        if not payment_id:
            raise PaymentVerificationError(
                "iyzico paymentId eksik."
            )

        if not conversation_id:
            raise PaymentVerificationError(
                "iyzico conversationId eksik."
            )

        # ------------------------------------------------------------------
        # İlk DB kontrolü.
        #
        # Burada transaction kısa tutuluyor.
        # Provider HTTP çağrısı yapılmıyor.
        # ------------------------------------------------------------------
        # 1. LOCAL PAYMENT TRANSACTION VALIDATION

        with transaction.atomic():
            try:
                payment_tx = (
                    PaymentTransaction.objects
                    .select_for_update()
                    .select_related("order")
                    .get(
                        provider=cls.PROVIDER,
                        conversation_id=conversation_id,
                    )
                )

            except PaymentTransaction.DoesNotExist as exc:
                raise PaymentVerificationError(
                    "iyzico callback için eşleşen ödeme kaydı bulunamadı."
                ) from exc

            # --------------------------------------------------------------
            # IDEMPOTENCY
            # --------------------------------------------------------------

            if payment_tx.status == PaymentStatus.SUCCESS:
                return PaymentCompletionResult(
                    payment_transaction_id=payment_tx.id,
                    order_id=payment_tx.order_id,
                    order_number=payment_tx.order.order_number,
                    payment_id=(
                        payment_tx.payment_id
                        or payment_id
                    ),
                    conversation_id=(
                        payment_tx.conversation_id
                    ),
                    paid_price=cls._money(
                        payment_tx.paid_price
                    ),
                )

            # --------------------------------------------------------------
            # PAYMENT ID MATCH
            # --------------------------------------------------------------

            if (
                payment_tx.payment_id
                and payment_tx.payment_id != payment_id
            ):
                raise PaymentVerificationError(
                    "iyzico paymentId doğrulaması başarısız."
                )

            # ------------------------------------------------------------------
            # PAYMENT STATE
            # ------------------------------------------------------------------

            if payment_tx.status != PaymentStatus.PENDING:
                raise InvalidOrderStateError(
                    "3DS ödeme tamamlanabilir durumda değil."
                )

            order = payment_tx.order

            expected_basket_id = payment_tx.basket_id

            expected_paid_price = cls._money(
                payment_tx.paid_price
            )

            expected_price = cls._money(
                order.subtotal
            )

            expected_currency = cls._clean(
                order.currency
            ).upper()

        # ------------------------------------------------------------------
        # Provider HTTP call TRANSACTION DIŞINDA.
        # ------------------------------------------------------------------
        # 2. IYZICO COMPLETION

        response = cls._complete_remote(
            payment_id=payment_id,
            conversation_id=conversation_id,
            conversation_data=conversation_data,
        )


        # Provider response
        # 3. RESPONSE VALIDATION


        cls._validate_completion_response(
            response=response,
            payment_id=payment_id,
            conversation_id=conversation_id,
            expected_basket_id=expected_basket_id,
            expected_paid_price=expected_paid_price,
            expected_price=expected_price,
            expected_currency=expected_currency,
        )

        # ------------------------------------------------------------------
         # 4. SIGNATURE
        # ------------------------------------------------------------------

        cls._verify_completion_signature(
            response=response,
        )

        # ------------------------------------------------------------------
        # Provider'dan gelen verileri normalize et
        # ------------------------------------------------------------------
        # 5. NORMALIZE PROVIDER DATA

        response_paid_price = cls._money(
            response.get("paidPrice")
        )

        currency = (
            cls._clean(
                response.get("currency")
            )
            or cls.CURRENCY
        ).upper()

        fraud_status = cls._normalize_optional_int(
            response.get("fraudStatus")
        )

        card_type = cls._clean(
            response.get("cardType")
        )

        card_association = cls._clean(
            response.get("cardAssociation")
        )

        last_four_digits = cls._clean(
            response.get("lastFourDigits")
        )

        # ------------------------------------------------------------------
        # FINAL DB STATE
        #
        # Burada:
        #
        # PaymentTransaction SUCCESS
        # Reservation CONSUMED
        # stock decrement
        # Order PAID
        #
        # tek transaction içinde gerçekleşir.
        # ------------------------------------------------------------------
        # 6. FINAL ATOMIC STATE CHANGE

        with transaction.atomic():
            try:
                payment_tx = (
                    PaymentTransaction.objects
                    .select_for_update()
                    .select_related("order")
                    .get(
                        provider=cls.PROVIDER,
                        conversation_id=conversation_id,
                    )
                )

            except PaymentTransaction.DoesNotExist as exc:
                raise PaymentVerificationError(
                    "Ödeme kaydı bulunamadı."
                ) from exc
            
            # IDEMPOTENCY
            # Başka bir callback bizden önce tamamladıysa:
            if payment_tx.status == PaymentStatus.SUCCESS:
                order = payment_tx.order

                return PaymentCompletionResult(
                    payment_transaction_id=payment_tx.id,
                    order_id=payment_tx.order_id,
                    order_number=order.order_number,
                    payment_id=(
                        payment_tx.payment_id
                        or payment_id
                    ),
                    conversation_id=payment_tx.conversation_id,
                    paid_price=cls._money(
                        payment_tx.paid_price
                    ),
                )

            # PAYMENT ID
            if payment_tx.payment_id:
                if payment_tx.payment_id != payment_id:
                    raise PaymentVerificationError(
                        "iyzico paymentId doğrulaması başarısız."
                    )

            else:
                payment_tx.payment_id = payment_id

            # Order'ı ayrıca lock et.
            order = (
                Order.objects
                .select_for_update()
                .get(pk=payment_tx.order_id)
            )

            # --------------------------------------------------------------
            # Order state
            # --------------------------------------------------------------

            if order.status == OrderStatus.PAID:
                # Payment transaction henüz SUCCESS değilse
                # burada tutarsız durum vardır.
                raise PaymentVerificationError(
                    "Sipariş zaten PAID durumda ancak ödeme kaydı "
                    "henüz tamamlanmış değil."
                )

            if order.status != OrderStatus.PENDING_PAYMENT:
                raise InvalidOrderStateError(
                    "Sipariş 3DS ödeme tamamlama aşamasında "
                    "uygun durumda değil."
                )

            # --------------------------------------------------------------
            # Provider paid price = transaction expected paid price
            # --------------------------------------------------------------

            expected_paid_price = cls._money(
                payment_tx.paid_price
            )

            if response_paid_price != expected_paid_price:
                raise PaymentVerificationError(
                    "iyzico paidPrice sipariş ödeme tutarıyla "
                    "eşleşmiyor."
                )

            # CURRENCY
            if currency != order.currency:
                raise PaymentVerificationError(
                    "iyzico ödeme para birimi "
                    "sipariş para birimiyle eşleşmiyor."
                )

            # --------------------------------------------------------------
            # Transaction SUCCESS
            # --------------------------------------------------------------

            payment_tx.status = PaymentStatus.SUCCESS
            payment_tx.payment_id = payment_id
            payment_tx.paid_price = response_paid_price
            payment_tx.currency = currency
            payment_tx.fraud_status = fraud_status
            payment_tx.card_type = (
                card_type or None
            )
            payment_tx.card_association = (
                card_association or None
            )

            if last_four_digits:
                payment_tx.last_four_digits = (
                    last_four_digits
                )

            payment_tx.succeeded_at = timezone.now()

            payment_tx.save(
                update_fields=[
                    "payment_id",
                    "status",
                    "paid_price",
                    "currency",
                    "fraud_status",
                    "card_type",
                    "card_association",
                    "last_four_digits",
                    "succeeded_at",
                    "updated_at",
                ]
            )

            # --------------------------------------------------------------
            # RESERVATION CONSUME
            #
            # Mevcut StockReservationService'in consume_order()
            # metodu Order=PENDING_PAYMENT bekliyor.
            #
            # O yüzden Order'ı PAID yapmadan önce consume ediyoruz.
            # --------------------------------------------------------------

            try:
                StockReservationService.consume_order(
                    order=order,
                )

            except Exception as exc:
                logger.exception(
                    "Stock reservation consume failed after "
                    "successful iyzico payment. "
                    "payment_transaction_id=%s order_id=%s",
                    payment_tx.id,
                    order.id,
                )

                raise ReservationError(
                    "Ödeme başarılı olmasına rağmen stok "
                    "rezervasyonu tamamlanamadı."
                ) from exc

            # --------------------------------------------------------------
            # ORDER PAID
            # --------------------------------------------------------------

            order.status = OrderStatus.PAID
            order.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

            return PaymentCompletionResult(
                payment_transaction_id=payment_tx.id,
                order_id=order.id,
                order_number=order.order_number,
                payment_id=payment_id,
                conversation_id=payment_tx.conversation_id,
                paid_price=response_paid_price,
            )

    # -------------------------------------------------------------------------
    # PAYMENT PREPARATION
    # -------------------------------------------------------------------------

    @classmethod
    def _prepare_payment(
        cls,
        *,
        order_id,
        buyer,
        payment_method,
        payment_card,
        stored_card_id,
        installment,
    ):
        """
        Kısa DB transaction içinde:

            Order lock
            ↓
            payment state validation
            ↓
            reservation validation
            ↓
            seller/submerchant validation
            ↓
            basket generation
            ↓
            PaymentTransaction creation

        Daha sonra transaction commit edilir ve iyzico çağrısı
        transaction dışında yapılır.
        """

        conversation_id = uuid.uuid4().hex
        basket_id = cls._build_basket_id(
            order_id=order_id,
        )

        with transaction.atomic():
            order = cls._get_locked_order(
                order_id=order_id,
            )

            identity_number = cls._get_order_buyer_identity_number(
                order=order,
            )

            cls._validate_order_state(
                order=order,
            )

            items = cls._get_order_items(
                order=order,
            )

            cls._validate_order_has_items(
                items=items,
            )

            cls._validate_active_reservations(
                order=order,
                items=items,
            )

            cls._validate_submerchants(
                items=items,
            )

            basket_items = cls._build_basket_items(
                items=items,
            )

            price = cls._calculate_basket_price(
                basket_items=basket_items,
                order=order,
            )

            paid_price = cls._money(
                order.total_amount,
            )

            if paid_price <= Decimal("0.00"):
                raise PaymentValidationError(
                    "Ödenecek sipariş tutarı sıfırdan büyük olmalıdır."
                )

            stored_card = None
            stored_card_payment = None

            if payment_method == cls.PAYMENT_METHOD_STORED_CARD:
                stored_card = cls._get_stored_card_for_order(
                    order=order,
                    stored_card_id=stored_card_id,
                )

                stored_card_payment = (
                    cls._build_stored_card_payment_data(
                        stored_card=stored_card,
                    )
                )

            request_payload = cls._build_initialize_request(
                order=order,
                buyer=buyer,
                payment_method=payment_method,
                payment_card=payment_card,
                stored_card_payment=stored_card_payment,
                installment=installment,
                conversation_id=conversation_id,
                basket_id=basket_id,
                price=price,
                paid_price=paid_price,
                basket_items=basket_items,
                identity_number=identity_number,
            )

            last_four_digits = None

            if payment_method == cls.PAYMENT_METHOD_NEW_CARD:
                last_four_digits = cls._get_last_four_digits(
                    payment_card.card_number
                )

            else:
                last_four_digits = (
                    stored_card.last_four_digits
                )

            payment_transaction = (
                PaymentTransaction.objects.create(
                    order=order,
                    provider=cls.PROVIDER,
                    conversation_id=conversation_id,
                    basket_id=basket_id,
                    status=PaymentStatus.INITIATED,
                    paid_price=paid_price,
                    currency=order.currency,
                    last_four_digits=last_four_digits,
                )
            )

        return (
            order,
            payment_transaction,
            request_payload,
        )

    # -------------------------------------------------------------------------
    # ORDER VALIDATION
    # -------------------------------------------------------------------------

    @classmethod
    def _validate_order_state(cls, *, order):
        if order.status == OrderStatus.PAID:
            raise PaymentAlreadyProcessedError(
                "Bu siparişin ödemesi zaten başarılı."
            )

        if order.status != OrderStatus.PENDING_PAYMENT:
            raise InvalidOrderStateError(
                "Bu sipariş için ödeme başlatılamaz."
            )

        # Aynı order üzerinde birden fazla aktif ödeme denemesine izin verme.
        active_payment = (
            PaymentTransaction.objects
            .filter(
                order=order,
                status__in=[
                    PaymentStatus.INITIATED,
                    PaymentStatus.PENDING,
                ],
            )
            .order_by("-id")
            .first()
        )

        if active_payment:
            raise PaymentAlreadyInProgressError(
                "Bu sipariş için devam eden bir ödeme denemesi var."
            )

    @staticmethod
    def _get_locked_order(*, order_id):
        try:
            return (
                Order.objects
                .select_for_update()
                .select_related("user")
                .get(pk=order_id)
            )
        except Order.DoesNotExist as exc:
            raise PaymentValidationError(
                "Sipariş bulunamadı."
            ) from exc

    @staticmethod
    def _get_order_items(*, order):
        return list(
            OrderItem.objects
            .filter(
                sub_order__order=order,
            )
            .select_related(
                "sub_order",
                "sub_order__store",
                "store_product",
                "store_product__store",
                "store_product__store__seller",
                "store_product__variant",
                "store_product__variant__product",
                "store_product__variant__product__category",
                "store_product__variant__product__brand",
            )
            .order_by("id")
        )

    @staticmethod
    def _validate_order_has_items(*, items):
        if not items:
            raise PaymentValidationError(
                "Ödeme yapılacak sipariş kalemi bulunamadı."
            )

    # -------------------------------------------------------------------------
    # STOCK RESERVATION VALIDATION
    # -------------------------------------------------------------------------

    @classmethod
    def _validate_active_reservations(
        cls,
        *,
        order,
        items,
    ):
        """
        Payment başlamadan önce siparişteki tüm kalemlerin
        aktif ve süresi geçmemiş reservation'a sahip olması gerekir.

        Reservation sistemi PaymentService'de oluşturulmaz;
        OrderService tarafından checkout sırasında oluşturulmuştur.
        """

        now = timezone.now()

        active_reservation_ids = set(
            StockReservation.objects.filter(
                order_item__sub_order__order=order,
                status=ReservationStatus.ACTIVE,
                expires_at__gt=now,
            ).values_list(
                "order_item_id",
                flat=True,
            )
        )

        missing = [
            item.id
            for item in items
            if item.id not in active_reservation_ids
        ]

        if missing:
            raise ReservationError(
                "Sipariş kalemlerinin stok rezervasyonu "
                "aktif değil veya süresi dolmuş."
            )

    # -------------------------------------------------------------------------
    # SUBMERCHANT VALIDATION
    # -------------------------------------------------------------------------

    @classmethod
    def _validate_submerchants(cls, *, items):
        """
        Marketplace payment'ta her basket item'ın
        aktif bir seller subMerchantKey'i olmalıdır.
        """

        for item in items:
            store_product = item.store_product

            if not store_product:
                raise PaymentValidationError(
                    "Sipariş kaleminin StoreProduct kaydı bulunamadı."
                )

            seller = store_product.store.seller

            if (
                seller.iyzico_onboarding_status
                != IyzicoOnboardingStatus.ACTIVE
            ):
                raise PaymentValidationError(
                    "Siparişteki satıcılardan biri iyzico "
                    "submerchant olarak aktif değil."
                )

            if not seller.iyzico_submerchant_key:
                raise PaymentValidationError(
                    "Siparişteki satıcılardan birinin "
                    "iyzico subMerchantKey bilgisi eksik."
                )

    # -------------------------------------------------------------------------
    # BASKET
    # -------------------------------------------------------------------------

    @classmethod
    def _build_basket_items(cls, *, items):
        """
        OrderItem -> iyzico basket item dönüşümü.

        Şu an platform commission modeli olmadığı için:

            subMerchantPrice = item.total_amount

        kabul ediyoruz.

        Yani seller şu an ürün kalemi tutarının tamamını alacak şekilde
        gönderiliyor.

        Platform komisyonu eklendiğinde bu hesap yalnızca
        _calculate_submerchant_price() içinde değiştirilecek.
        """

        basket_items = []

        for item in items:
            store_product = item.store_product
            seller = store_product.store.seller

            item_price = cls._money(
                item.total_amount,
            )

            if item_price <= Decimal("0.00"):
                raise PaymentValidationError(
                    "Ücretsiz / sıfır tutarlı ürün kalemi "
                    "bu ödeme akışında kullanılamaz."
                )

            product = store_product.variant.product

            category1 = ""

            if product.category:
                category1 = (
                    str(product.category.name)
                    .strip()
                )

            category2 = ""

            if product.brand:
                category2 = (
                    str(product.brand.name)
                    .strip()
                )

            basket_item = {
                "id": f"OI-{item.id}",
                "name": item.product_name_snapshot,
                "category1": category1,
                "category2": category2,
                "itemType": cls.ITEM_TYPE_PHYSICAL,
                "price": cls._money_to_string(
                    item_price
                ),
                "subMerchantKey": (
                    seller.iyzico_submerchant_key
                ),
                "subMerchantPrice": (
                    cls._money_to_string(
                        cls._calculate_submerchant_price(
                            item=item,
                        )
                    )
                ),
            }

            basket_items.append(
                basket_item
            )

        return basket_items

    @classmethod
    def _calculate_submerchant_price(cls, *, item):
        """
        Şimdilik platform komisyonu yok.

        Daha sonra:

            item_total
                -
            platform_commission
                -
            seller_discount_share
                =
            subMerchantPrice

        gibi bir business rule buraya taşınabilir.
        """

        amount = cls._money(
            item.total_amount,
        )

        if amount <= Decimal("0.00"):
            raise PaymentValidationError(
                "Submerchant tutarı sıfırdan büyük olmalıdır."
            )

        return amount

    @classmethod
    def _calculate_basket_price(
        cls,
        *,
        basket_items,
        order,
    ):
        """
        iyzico'da basket item price toplamı `price` değerine
        eşit olmalıdır.

        Mevcut modelimizde shipping order-level olduğu için:

            price     = ürün kalemleri toplamı
            paidPrice = müşterinin gerçekten ödeyeceği order.total_amount

        kullanıyoruz.

        Böylece shipping platform seviyesinde kalıyor.
        """

        basket_total = sum(
            (
                Decimal(
                    item["price"]
                )
                for item in basket_items
            ),
            Decimal("0.00"),
        )

        basket_total = cls._money(
            basket_total
        )

        if basket_total <= Decimal("0.00"):
            raise PaymentValidationError(
                "Sepet toplamı sıfırdan büyük olmalıdır."
            )

        # Order'ın subtotal alanı ile basket item toplamının
        # birbirini temsil etmesini bekliyoruz.
        expected_subtotal = cls._money(
            order.subtotal
        )

        if basket_total != expected_subtotal:
            raise PaymentValidationError(
                "Sipariş toplamı ile ödeme basket toplamı "
                "birbiriyle eşleşmiyor."
            )

        return basket_total

    # -------------------------------------------------------------------------
    # REQUEST
    # -------------------------------------------------------------------------

    @classmethod
    def _build_initialize_request(
        cls,
        *,
        order,
        buyer,
        payment_method,
        payment_card,
        stored_card_payment,
        installment,
        conversation_id,
        basket_id,
        price,
        paid_price,
        basket_items,
        identity_number,
    ):
        payment_card_payload = None

        if payment_method == cls.PAYMENT_METHOD_NEW_CARD:
            card_number = re.sub(
                r"\s+",
                "",
                cls._clean(
                    payment_card.card_number
                ),
            )

            payment_card_payload = {
                "cardHolderName": (
                    payment_card.card_holder_name
                ),
                "cardNumber": (
                    card_number
                ),
                "expireMonth": (
                    payment_card.expire_month
                ),
                "expireYear": (
                    payment_card.expire_year
                ),
                "cvc": payment_card.cvc,
                "registerCard": "0",
            }

        else:
            if stored_card_payment is None:
                raise PaymentValidationError(
                    "Kayıtlı kart bilgisi bulunamadı."
                )

            payment_card_payload = {
                "cardUserKey": (
                    stored_card_payment.card_user_key
                ),
                "cardToken": (
                    stored_card_payment.card_token
                ),
            }

        return {
            "locale": cls.LOCALE,
            "conversationId": conversation_id,
            "price": cls._money_to_string(price),
            "paidPrice": cls._money_to_string(paid_price),
            "currency": order.currency,
            "installment": installment,
            "basketId": basket_id,
            "paymentChannel": cls.PAYMENT_CHANNEL,
            "paymentGroup": cls.PAYMENT_GROUP,
            "callbackUrl": cls._get_callback_url(),

            "paymentCard": payment_card_payload,

            "buyer": cls._build_buyer(
                order=order,
                buyer=buyer,
                identity_number=identity_number,
            ),

            "shippingAddress": cls._build_address(
                full_name=order.shipping_full_name,
                phone=order.shipping_phone,
                address_line1=order.shipping_address_line1,
                address_line2=order.shipping_address_line2,
                city=order.shipping_city,
                state=order.shipping_state,
                postal_code=order.shipping_postal_code,
            ),

            "billingAddress": cls._build_address(
                full_name=order.billing_full_name,
                phone=order.billing_phone,
                address_line1=order.billing_address_line1,
                address_line2=order.billing_address_line2,
                city=order.billing_city,
                state=order.billing_state,
                postal_code=order.billing_postal_code,
            ),

            "basketItems": basket_items,
        }

    @classmethod
    def _build_buyer(
        cls,
        *,
        order,
        buyer,
        identity_number,
    ):

        registration_address = (
            cls._clean(
                buyer.registration_address
            )
        )

        ip = cls._clean(
            buyer.ip
        )

        registration_date = order.created_at

        last_login_date = registration_date

        if order.user_id:
            user = order.user

            if user.date_joined:
                registration_date = (
                    user.date_joined
                )

            if user.last_login:
                last_login_date = (
                    user.last_login
                )

        return {
            "id": cls._build_buyer_id(
                order=order,
            ),
            "name": cls._clean(
                buyer.name
            ),
            "surname": cls._clean(
                buyer.surname
            ),
            "identityNumber": identity_number,
            "email": cls._clean(
                order.customer_email
            ),
            "gsmNumber": cls._clean(
                order.customer_phone
            ),
            "registrationDate": (
                cls._format_datetime(
                    registration_date
                )
            ),
            "lastLoginDate": (
                cls._format_datetime(
                    last_login_date
                )
            ),
            "registrationAddress": registration_address,
            "city": cls._clean(
                order.billing_city
            ),
            "country": "Turkey",
            "zipCode": cls._clean(
                order.billing_postal_code
            ),
            "ip": ip,
        }

    @classmethod
    def _build_address(
        cls,
        *,
        full_name,
        phone,
        address_line1,
        address_line2,
        city,
        state,
        postal_code,
    ):
        address_parts = [
            cls._clean(address_line1),
            cls._clean(address_line2),
            cls._clean(state),
        ]

        address = " ".join(
            part
            for part in address_parts
            if part
        )

        return {
            "contactName": cls._clean(
                full_name
            ),
            "city": cls._clean(
                city
            ),
            "country": "Turkey",
            "address": address,
            "zipCode": cls._clean(
                postal_code
            ),
        }

    # -------------------------------------------------------------------------
    # IYZICO
    # -------------------------------------------------------------------------

    @classmethod
    def _initialize_remote(
        cls,
        *,
        request_payload,
    ):
        """
        iyzico 3DS Initialize çağrısı.

        Kart bilgileri loglanmaz.
        """

        try:
            resource = (
                iyzipay.ThreedsInitialize().create(
                    request_payload,
                    cls._options(),
                )
            )

        except Exception as exc:
            logger.exception(
                "iyzico 3DS initialize transport error."
            )

            raise PaymentGatewayError(
                "Ödeme sağlayıcısına bağlanırken "
                "bir hata oluştu."
            ) from exc

        try:
            return cls._read_json(
                resource
            )

        except Exception as exc:
            logger.exception(
                "iyzico 3DS initialize response error."
            )

            raise PaymentGatewayError(
                "Ödeme sağlayıcısından geçersiz "
                "bir yanıt alındı."
            ) from exc

    @classmethod
    def _complete_remote(
        cls,
        *,
        payment_id,
        conversation_id,
        conversation_data,
    ):
        """
        iyzico 3DS payment completion çağrısı.

        Kart PAN/CVC burada gönderilmez.
        """

        request_payload = {
            "locale": cls.LOCALE,
            "conversationId": conversation_id,
            "paymentId": payment_id,
            "conversationData": conversation_data,
        }

        try:
            resource = (
                iyzipay.ThreedsPayment().create(
                    request_payload,
                    cls._options(),
                )
            )

        except Exception as exc:
            logger.exception(
                "iyzico 3DS completion transport error."
            )

            raise PaymentGatewayError(
                "Ödeme sağlayıcısına bağlanırken "
                "bir hata oluştu."
            ) from exc

        try:
            return cls._read_json(
                resource
            )

        except Exception as exc:
            logger.exception(
                "iyzico 3DS completion response error."
            )

            raise PaymentGatewayError(
                "Ödeme sağlayıcısından geçersiz "
                "bir yanıt alındı."
            ) from exc

    @staticmethod
    def _read_json(resource):
        raw = resource.read()

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        if not raw:
            raise PaymentGatewayError(
                "iyzico boş response döndürdü."
            )

        try:
            return json.loads(raw)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentGatewayError(
                "iyzico response JSON formatında değil."
            ) from exc

    # -------------------------------------------------------------------------
    # RESPONSE
    # -------------------------------------------------------------------------

    @classmethod
    def _handle_initialize_response(
        cls,
        *,
        payment_transaction_id,
        order,
        payment_transaction,
        response,
        request_payload,
    ):
        status = response.get(
            "status"
        )

        # ---------------------------------------------------------------------
        # SUCCESS
        # ---------------------------------------------------------------------

        if status == "success":
            return cls._handle_initialize_success(
                payment_transaction=payment_transaction,
                order=order,
                response=response,
                request_payload=request_payload,
            )

        # ---------------------------------------------------------------------
        # FAILURE
        # ---------------------------------------------------------------------

        if status == "failure":
            return cls._handle_initialize_failure(
                payment_transaction=payment_transaction,
                response=response,
            )

        # ---------------------------------------------------------------------
        # UNKNOWN
        # ---------------------------------------------------------------------

        raise PaymentGatewayError(
            "iyzico ödeme başlatma yanıtı "
            "beklenmeyen bir formatta."
        )

    @classmethod
    def _handle_initialize_success(
        cls,
        *,
        payment_transaction,
        order,
        response,
        request_payload,
    ):
        payment_id = cls._clean(
            response.get("paymentId")
        )

        response_conversation_id = cls._clean(
            response.get("conversationId")
        )

        signature = cls._clean(
            response.get("signature")
        )

        html_content = cls._decode_three_ds_html(
            response.get(
                "threeDSHtmlContent"
            )
        )

        if not payment_id:
            raise PaymentVerificationError(
                "iyzico paymentId döndürmedi."
            )

        if (
            not response_conversation_id
            or response_conversation_id
            != payment_transaction.conversation_id
        ):
            raise PaymentVerificationError(
                "iyzico conversationId doğrulaması başarısız."
            )

        if not signature:
            raise PaymentVerificationError(
                "iyzico 3DS initialize signature bilgisi eksik."
            )

        cls._verify_initialize_signature(
            payment_id=payment_id,
            conversation_id=response_conversation_id,
            signature=signature,
        )

        if not html_content:
            raise PaymentInitializationError(
                "iyzico 3DS HTML içeriği döndürmedi."
            )

        paid_price = cls._normalize_optional_money(
            response.get("paidPrice")
        )

        if paid_price is None:
            paid_price = cls._money(
                order.total_amount
            )

        currency = (
            cls._clean(
                response.get("currency")
            )
            or order.currency
        ).upper()

        if currency != order.currency:
            raise PaymentVerificationError(
                "iyzico ödeme para birimi "
                "sipariş para birimiyle eşleşmiyor."
            )

        fraud_status = cls._normalize_optional_int(
            response.get("fraudStatus")
        )

        card_type = cls._clean(
            response.get("cardType")
        )

        card_association = cls._clean(
            response.get("cardAssociation")
        )


        # ---------------------------------------------------------------------
        # DB UPDATE
        # ---------------------------------------------------------------------

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects
                .select_for_update()
                .get(pk=payment_transaction.id)
            )

            if payment_tx.status == PaymentStatus.SUCCESS:
                raise PaymentAlreadyProcessedError(
                    "Bu ödeme işlemi zaten başarılı."
                )

            payment_tx.payment_id = payment_id
            payment_tx.status = PaymentStatus.PENDING
            payment_tx.paid_price = paid_price
            payment_tx.currency = currency
            payment_tx.fraud_status = fraud_status
            payment_tx.card_type = card_type or None
            payment_tx.card_association = (
                card_association or None
            )


            payment_tx.save(
                update_fields=[
                    "payment_id",
                    "status",
                    "paid_price",
                    "currency",
                    "fraud_status",
                    "card_type",
                    "card_association",
                    "updated_at",
                ]
            )

        return PaymentInitializationResult(
            payment_transaction_id=payment_tx.id,
            payment_id=payment_id,
            conversation_id=(
                payment_tx.conversation_id
            ),
            three_ds_html_content=html_content,
        )

    @classmethod
    def _handle_initialize_failure(
        cls,
        *,
        payment_transaction,
        response,
    ):
        error_code = cls._clean(
            response.get("errorCode")
        )

        error_message = cls._clean(
            response.get("errorMessage")
        )

        with transaction.atomic():
            payment_tx = (
                PaymentTransaction.objects
                .select_for_update()
                .get(pk=payment_transaction.id)
            )

            payment_id = cls._clean(
                response.get("paymentId")
            )

            if payment_id:
                payment_tx.payment_id = payment_id

            payment_tx.status = PaymentStatus.FAILED

            payment_tx.save(
                update_fields=[
                    "payment_id",
                    "status",
                    "updated_at",
                ]
            )

        # ---------------------------------------------------------------------
        # KULLANICI TARAFINDAN DÜZELTİLEBİLEN PROVIDER VALIDATION HATALARI
        # ---------------------------------------------------------------------

        if (
            error_code
            in cls.INITIALIZE_VALIDATION_ERROR_CODES
        ):

            # CVC özel mesajı
            if error_code == "15":

                raise PaymentValidationError(
                    "CVC geçersiz. "
                    "American Express kartlarda CVC 4 haneli, "
                    "diğer kartlarda 3 haneli olmalıdır."
                )

            raise PaymentValidationError(
                error_message
                or "Girdiğiniz ödeme bilgileri geçersiz."
            )

        # ---------------------------------------------------------------------
        # SİSTEM / PROVIDER HATASI
        # ---------------------------------------------------------------------

        raise PaymentInitializationError(
            "Ödeme başlatılamadı. "
            f"Kod: {error_code or '-'} | "
            f"{error_message or 'iyzico ödeme başlatma işlemini reddetti.'}"
        )

    @classmethod
    def _validate_completion_response(
        cls,
        *,
        response,
        payment_id,
        conversation_id,
        expected_basket_id,
        expected_paid_price,
        expected_price,
        expected_currency,
    ):
        status = cls._clean(
            response.get("status")
        ).lower()

        if status != "success":
            error_code = cls._clean(
                response.get("errorCode")
            )

            error_message = cls._clean(
                response.get("errorMessage")
            )

            raise PaymentVerificationError(
                "iyzico ödeme tamamlama işlemi başarısız. "
                f"Kod: {error_code or '-'} | "
                f"{error_message or 'iyzico ödemeyi onaylamadı.'}"
            )

        # ======================================================================
        # PAYMENT ID
        # ======================================================================

        response_payment_id = cls._clean(
            response.get("paymentId")
        )

        if response_payment_id != payment_id:
            raise PaymentVerificationError(
                "iyzico paymentId doğrulaması başarısız."
            )

        # ======================================================================
        # CONVERSATION ID
        # ======================================================================

        response_conversation_id = cls._clean(
            response.get("conversationId")
        )

        if response_conversation_id != conversation_id:
            raise PaymentVerificationError(
                "iyzico conversationId doğrulaması başarısız."
            )

        # ======================================================================
        # BASKET ID
        # ======================================================================

        response_basket_id = cls._clean(
            response.get("basketId")
        )

        if (
            not expected_basket_id
            or response_basket_id != expected_basket_id
        ):
            raise PaymentVerificationError(
                "iyzico basketId doğrulaması başarısız."
            )

        # ======================================================================
        # CURRENCY
        # ======================================================================

        response_currency = (
            cls._clean(
                response.get("currency")
            )
            or cls.CURRENCY
        ).upper()

        if response_currency != expected_currency:
            raise PaymentVerificationError(
                "iyzico ödeme para birimi "
                "sipariş para birimiyle eşleşmiyor."
            )

        # ======================================================================
        # PAID PRICE
        # ======================================================================

        response_paid_price = (
            cls._normalize_optional_money(
                response.get("paidPrice")
            )
        )

        if response_paid_price is None:
            raise PaymentVerificationError(
                "iyzico paidPrice bilgisi eksik."
            )

        if response_paid_price != expected_paid_price:
            raise PaymentVerificationError(
                "iyzico paidPrice doğrulaması başarısız."
            )

        # ======================================================================
        # PRICE
        # ======================================================================

        response_price = (
            cls._normalize_optional_money(
                response.get("price")
            )
        )

        if response_price is None:
            raise PaymentVerificationError(
                "iyzico price bilgisi eksik."
            )

        if response_price != expected_price:
            raise PaymentVerificationError(
                "iyzico price sipariş ürün toplamıyla "
                "eşleşmiyor."
            )

    # -------------------------------------------------------------------------
    # SIGNATURE
    # -------------------------------------------------------------------------

    @classmethod
    def _verify_initialize_signature(
        cls,
        *,
        payment_id,
        conversation_id,
        signature,
    ):
        """
        iyzico 3DS Initialize response signature doğrulaması.

        iyzico'nun güncel response signature sözleşmesine göre
        parametre sırası:

            paymentId, conversationId
        """

        if not payment_id:
            raise PaymentVerificationError(
                "iyzico paymentId eksik."
            )

        if not conversation_id:
            raise PaymentVerificationError(
                "iyzico conversationId eksik."
            )

        if not signature:
            raise PaymentVerificationError(
                "iyzico initialize signature bilgisi eksik."
            )

        try:
            threeds_initialize = (
                iyzipay.ThreedsInitialize()
            )
    
            calculated_signature = (
                threeds_initialize
                .calculate_hmac_sha256_signature(
                    [
                        payment_id,
                        conversation_id,
                    ],
                    settings.IYZICO_SECRET_KEY,
                )
            )
    
            verified = (
                signature
                == calculated_signature
            )
    
        except Exception as exc:
            raise PaymentVerificationError(
                "iyzico 3DS initialize signature "
                "doğrulaması sırasında hata oluştu."
            ) from exc
    
        if not verified:
            raise PaymentVerificationError(
                "iyzico 3DS initialize signature "
                "doğrulanamadı."
            )

    @classmethod
    def _verify_completion_signature(
        cls,
        *,
        response,
    ):
        """
        iyzico 3DS completion response signature doğrulaması.

        Parametre sırası:

            paymentId
            currency
            basketId
            conversationId
            paidPrice
            price
        """

        payment_id = cls._clean(
            response.get("paymentId")
        )

        currency = (
            cls._clean(
                response.get("currency")
            )
            or cls.CURRENCY
        ).upper()

        basket_id = cls._clean(
            response.get("basketId")
        )

        conversation_id = cls._clean(
            response.get("conversationId")
        )

        paid_price = (
            cls._normalize_optional_money(
                response.get("paidPrice")
            )
        )

        price = (
            cls._normalize_optional_money(
                response.get("price")
            )
        )

        signature = cls._clean(
            response.get("signature")
        )

        if not payment_id:
            raise PaymentVerificationError(
                "iyzico paymentId eksik."
            )

        if not basket_id:
            raise PaymentVerificationError(
                "iyzico basketId eksik."
            )

        if not conversation_id:
            raise PaymentVerificationError(
                "iyzico conversationId eksik."
            )

        if paid_price is None:
            raise PaymentVerificationError(
                "iyzico paidPrice eksik."
            )

        if price is None:
            raise PaymentVerificationError(
                "iyzico price eksik."
            )

        if not signature:
            raise PaymentVerificationError(
                "iyzico 3DS completion signature eksik."
            )

        try:
            threeds_payment = (
                iyzipay.ThreedsPayment()
            )

            threeds_payment.verify_signature(
                [
                    payment_id,
                    currency,
                    basket_id,
                    conversation_id,
                    threeds_payment.strip_zero(
                        str(paid_price)
                    ),
                    threeds_payment.strip_zero(
                        str(price)
                    ),
                ],
                settings.IYZICO_SECRET_KEY,
                signature,
            )

        except Exception as exc:
            logger.exception(
                "iyzico 3DS completion signature verification failed."
            )

            raise PaymentVerificationError(
                "iyzico 3DS completion signature "
                "doğrulanamadı."
            ) from exc

    # -------------------------------------------------------------------------
    # 3DS HTML
    # -------------------------------------------------------------------------

    @staticmethod
    def _decode_three_ds_html(value):
        if not value:
            return ""
    
        try:
            import base64
    
            return base64.b64decode(
                value
            ).decode("utf-8")
    
        except (
            ValueError,
            UnicodeDecodeError,
            TypeError,
        ) as exc:
            raise PaymentInitializationError(
                "iyzico 3DS HTML içeriği çözülemedi."
            ) from exc

    # -------------------------------------------------------------------------
    # VALIDATION
    # -------------------------------------------------------------------------

    @classmethod
    def _validate_buyer(
        cls,
        *,
        buyer,
    ):
        cls._require_length(
            value=buyer.name,
            field="buyer.name",
            min_length=2,
            max_length=100,
        )

        cls._require_length(
            value=buyer.surname,
            field="buyer.surname",
            min_length=1,
            max_length=100,
        )

        registration_address = cls._clean(
            buyer.registration_address
        )

        cls._require_length(
            value=registration_address,
            field="buyer.registration_address",
            min_length=5,
            max_length=255,
        )

        cls._validate_ip(
            buyer.ip
        )

    @classmethod
    def _get_order_buyer_identity_number(cls, *, order):
        identity_number = cls._clean(
            order.buyer_identity_number
        )

        # ---------------------------------------------------------
        # GERÇEK SİPARİŞ BİLGİSİ
        # ---------------------------------------------------------

        if identity_number:
            if not re.fullmatch(
                r"\d{11}",
                identity_number,
            ):
                raise PaymentValidationError(
                    "Müşteri kimlik numarası 11 haneli olmalıdır."
                )

            return identity_number

        # ---------------------------------------------------------
        # SANDBOX TEST DEĞERİ
        # ---------------------------------------------------------

        environment = str(
            getattr(
                settings,
                "IYZICO_ENV",
                "",
            )
            or ""
        ).strip().lower()

        if environment == "sandbox":
            test_identity_number = cls._clean(
                getattr(
                    settings,
                    "IYZICO_SANDBOX_IDENTITY_NUMBER",
                    "",
                )
            )

            if not re.fullmatch(
                r"\d{11}",
                test_identity_number,
            ):
                raise PaymentValidationError(
                    "IYZICO_SANDBOX_IDENTITY_NUMBER "
                    "geçerli bir 11 haneli değer olmalıdır."
                )

            return test_identity_number

        # ---------------------------------------------------------
        # PRODUCTION
        # ---------------------------------------------------------

        raise PaymentValidationError(
            "Ödeme için müşteri kimlik bilgisi gereklidir."
        )

    @staticmethod
    def _build_stored_card_payment_data(
        *,
        stored_card,
    ):
        card_user_key = str(
            stored_card.payment_customer.provider_customer_key or ""
        ).strip()

        card_token = str(
            stored_card.provider_card_token or ""
        ).strip()

        if not card_user_key or not card_token:
            raise PaymentValidationError(
                "Kayıtlı kart provider bilgileri eksik."
            )

        return StoredCardPaymentData(
            card_user_key=card_user_key,
            card_token=card_token,
        )

    @classmethod
    def _normalize_payment_method(
        cls,
        payment_method,
    ):
        payment_method = cls._clean(
            payment_method
        ).lower()

        if payment_method not in (
            cls.PAYMENT_METHOD_NEW_CARD,
            cls.PAYMENT_METHOD_STORED_CARD,
        ):
            raise PaymentValidationError(
                "Geçersiz ödeme yöntemi."
            )

        return payment_method

    @classmethod
    def _get_stored_card_for_order(
        cls,
        *,
        order,
        stored_card_id,
    ):
        if not order.user_id:
            raise PaymentValidationError(
                "Kayıtlı kart ile ödeme yapmak için giriş yapmalısınız."
            )

        if isinstance(
            stored_card_id,
            bool,
        ):
            raise PaymentValidationError(
                "Geçersiz kayıtlı kart ID."
            )

        try:
            stored_card_id = int(
                stored_card_id
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentValidationError(
                "Geçersiz kayıtlı kart ID."
            ) from exc

        if stored_card_id <= 0:
            raise PaymentValidationError(
                "Geçersiz kayıtlı kart ID."
            )

        card = (
            StoredCard.objects
            .select_related(
                "payment_customer",
            )
            .filter(
                pk=stored_card_id,
                payment_customer__user_id=order.user_id,
                payment_customer__provider=cls.PROVIDER,
                is_active=True,
            )
            .first()
        )

        if not card:
            raise StoredCardNotFoundError(
                "Aktif kayıtlı kart bulunamadı."
            )

        cls._validate_stored_card_expiration(
            card=card
        )

        return card

    @staticmethod
    def _validate_stored_card_expiration(
        *,
        card,
    ):
        expire_month = str(
            card.expire_month or ""
        ).strip()

        expire_year = str(
            card.expire_year or ""
        ).strip()

        if not expire_month or not expire_year:
            return

        try:
            month = int(
                expire_month
            )
            year = int(
                expire_year
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentValidationError(
                "Kayıtlı kartın son kullanma tarihi geçerli değil."
            ) from exc

        current_date = timezone.localdate()

        if (
            year < current_date.year
            or (
                year == current_date.year
                and month < current_date.month
            )
        ):
            raise PaymentValidationError(
                "Kayıtlı kartın son kullanma tarihi geçmiş."
            )

    @classmethod
    def _validate_card(
        cls,
        *,
        payment_card,
    ):
        cls._require_length(
            value=payment_card.card_holder_name,
            field="card_holder_name",
            min_length=2,
            max_length=255,
        )

        card_number = re.sub(
            r"\s+",
            "",
            cls._clean(
                payment_card.card_number
            ),
        )

        if not re.fullmatch(
            r"\d{15,16}",
            card_number,
        ):
            raise PaymentValidationError(
                "Kart numarası geçersiz."
            )

        if not cls._passes_luhn(
            card_number
        ):
            raise PaymentValidationError(
                "Kart numarası doğrulanamadı."
            )

        expire_month = cls._clean(
            payment_card.expire_month
        )

        if not re.fullmatch(
            r"\d{1,2}",
            expire_month,
        ):
            raise PaymentValidationError(
                "Kart son kullanma ayı geçersiz."
            )

        month = int(expire_month)

        if month < 1 or month > 12:
            raise PaymentValidationError(
                "Kart son kullanma ayı 1-12 arasında olmalıdır."
            )

        expire_year = cls._clean(
            payment_card.expire_year
        )

        if not re.fullmatch(
            r"\d{2,4}",
            expire_year,
        ):
            raise PaymentValidationError(
                "Kart son kullanma yılı geçersiz."
            )

        normalized_year = cls._normalize_expire_year(
            expire_year
        )

        current_date = timezone.localdate()

        if normalized_year < current_date.year:
            raise PaymentValidationError(
                "Kartın son kullanma tarihi geçmiş."
            )

        if (
            normalized_year == current_date.year
            and month < current_date.month
        ):
            raise PaymentValidationError(
                "Kartın son kullanma tarihi geçmiş."
            )

        cvc = cls._clean(
            payment_card.cvc
        )

        if not re.fullmatch(
            r"\d{3,4}",
            cvc,
        ):
            raise PaymentValidationError(
                "Kart güvenlik kodu geçersiz."
            )

    @classmethod
    def _normalize_installment(
        cls,
        installment,
    ):
        try:
            installment = int(
                installment
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentValidationError(
                "Geçersiz taksit sayısı."
            ) from exc

        if installment not in cls.SUPPORTED_INSTALLMENTS:
            raise PaymentValidationError(
                "Desteklenmeyen taksit seçildi."
            )

        return installment

    @staticmethod
    def _validate_ip(value):
        ip_value = str(
            value or ""
        ).strip()

        if not ip_value:
            raise PaymentValidationError(
                "Müşteri IP adresi gereklidir."
            )

        try:
            ipaddress.ip_address(
                ip_value
            )
        except ValueError as exc:
            raise PaymentValidationError(
                "Geçerli bir IP adresi gönderilmelidir."
            ) from exc

    @staticmethod
    def _require_length(
        *,
        value,
        field,
        min_length,
        max_length,
    ):
        value = str(
            value or ""
        ).strip()

        if not value:
            raise PaymentValidationError(
                f"{field} zorunludur."
            )

        if len(value) < min_length:
            raise PaymentValidationError(
                f"{field} en az "
                f"{min_length} karakter olmalıdır."
            )

        if len(value) > max_length:
            raise PaymentValidationError(
                f"{field} en fazla "
                f"{max_length} karakter olabilir."
            )

    # -------------------------------------------------------------------------
    # MONEY / IDENTIFIERS
    # -------------------------------------------------------------------------

    @classmethod
    def _money(cls, value):
        try:
            amount = Decimal(
                str(value)
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentValidationError(
                "Geçersiz para değeri."
            ) from exc

        return amount.quantize(
            cls.MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    @classmethod
    def _money_to_string(cls, value):
        return format(
            cls._money(value),
            ".2f",
        )

    @classmethod
    def _normalize_optional_money(
        cls,
        value,
    ):
        if value in (
            None,
            "",
        ):
            return None

        return cls._money(
            value
        )

    @staticmethod
    def _normalize_optional_int(value):
        if value in (
            None,
            "",
        ):
            return None

        try:
            return int(value)
        except (
            TypeError,
            ValueError,
        ):
            return None

    @staticmethod
    def _clean(value):
        return str(
            value or ""
        ).strip()

    @staticmethod
    def _format_datetime(value):
        if not isinstance(
            value,
            datetime,
        ):
            return str(value)

        return value.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    @staticmethod
    def _build_basket_id(*, order_id):
        return f"ORDER-{order_id}"

    @staticmethod
    def _build_buyer_id(*, order):
        if order.user_id:
            return f"USER-{order.user_id}"

        return f"GUEST-{order.id}"

    @staticmethod
    def _get_last_four_digits(card_number):
        digits = re.sub(
            r"\D",
            "",
            str(card_number or ""),
        )

        return (
            digits[-4:]
            if len(digits) >= 4
            else None
        )

    @staticmethod
    def _normalize_expire_year(value):
        year = int(value)

        if year < 100:
            return 2000 + year

        return year

    @staticmethod
    def _passes_luhn(card_number):
        digits = [
            int(char)
            for char in card_number
        ]

        checksum = 0

        parity = len(digits) % 2

        for index, digit in enumerate(
            digits
        ):
            if index % 2 == parity:
                digit *= 2

                if digit > 9:
                    digit -= 9

            checksum += digit

        return checksum % 10 == 0

    # -------------------------------------------------------------------------
    # SETTINGS
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_callback_url():
        callback_url = getattr(
            settings,
            "IYZICO_3DS_CALLBACK_URL",
            "",
        )

        callback_url = str(
            callback_url or ""
        ).strip()

        if not callback_url:
            raise PaymentValidationError(
                "IYZICO_3DS_CALLBACK_URL ayarı eksik."
            )

        if not callback_url.startswith(
            (
                "http://",
                "https://",
            )
        ):
            raise PaymentValidationError(
                "IYZICO_3DS_CALLBACK_URL geçerli "
                "bir URL olmalıdır."
            )

        return callback_url

    @staticmethod
    def _options():
        return {
            "api_key": settings.IYZICO_API_KEY,
            "secret_key": settings.IYZICO_SECRET_KEY,
            "base_url": settings.IYZICO_BASE_URL,
        }

    @staticmethod
    def _validate_settings():
        required_settings = (
            "IYZICO_API_KEY",
            "IYZICO_SECRET_KEY",
            "IYZICO_BASE_URL",
            "IYZICO_3DS_CALLBACK_URL",
        )

        missing = [
            setting_name
            for setting_name in required_settings
            if not getattr(
                settings,
                setting_name,
                None,
            )
        ]

        if missing:
            raise PaymentGatewayError(
                "Iyzico ayarları eksik: "
                + ", ".join(missing)
            )