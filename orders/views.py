import json
import logging
from decimal import Decimal
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.generic import TemplateView

from accounts.models import Address
from cart.services.cart import CartService

from .models import Order, PaymentTransaction, PaymentStatus

from .exceptions import (
    CartAccessError,
    CartItemSelectionError,
    EmptyOrderError,
    InsufficientStockError,
    InvalidAddressError,
    InvalidCurrencyError,
    InvalidOrderStateError,
    OrderDomainError,
    OrderNotFoundError,
    PaymentAlreadyInProgressError,
    PaymentAlreadyProcessedError,
    PaymentError,
    PaymentGatewayError,
    PaymentInitializationError,
    PaymentValidationError,
    PaymentVerificationError,
    ProductUnavailableError,
    ReservationError,
    ReservationExpiredError,
    StoredCardNotFoundError,
    CardStorageOperationInProgressError,
    CardStorageConsistencyError,
    CardStorageGatewayError,
)
from .services.order import AddressData, OrderService
from .services.shipping import ShippingService
from .services.stock_reservation import StockReservationService
from .services.payment import PaymentService, PaymentCardData, PaymentBuyerData
from .services.card_storage import CardStorageService, StoredCardCreateData


logger = logging.getLogger(__name__)

#TODO: view sayfası çok büyüdü. düzenlemede ayır.

# =============================================================================
# CHECKOUT PAGE
# =============================================================================

class CheckoutPageView(TemplateView):
    """
    Checkout HTML sayfasını render eder.

    GET /checkout/

    Sorumlulukları:
        - Kullanıcının cart'ını almak
        - Seçili ürün olup olmadığını kontrol etmek
        - Checkout context'ini hazırlamak
        - Authenticated kullanıcı için kayıtlı adresleri göndermek

    Business logic içermez.

    NOT:

        GET request'i reservation oluşturmaz.

        Gerçek checkout işlemi:

            POST /orders/checkout/create/

        sırasında gerçekleştirilir.
    """

    template_name = "orders/checkout.html"

    @method_decorator(ensure_csrf_cookie)
    def get(
        self,
        request,
        *args,
        **kwargs,
    ):
        self.cart = CartService.get_or_create_cart(
            request
        )

        self.cart_context = (
            CartService.get_cart_context_data(
                self.cart
            )
        )

        # ------------------------------------------------------------------
        # Selected item yoksa checkout yapılamaz.
        # ------------------------------------------------------------------

        if not self.cart_context.get(
            "selected_items_count"
        ):
            return redirect(
                "cart:detail"
            )

        # ------------------------------------------------------------------
        # Checkout'a özel seçili ürün gruplarını hazırla.
        # ------------------------------------------------------------------

        self.selected_grouped_items = (
            self._build_selected_grouped_items()
        )

        # ------------------------------------------------------------------
        # SEÇİLİ ÜRÜN TOPLAMLARI
        # ------------------------------------------------------------------

        self.selected_items_count = sum(
            (
                item.quantity
                for group in self.selected_grouped_items
                for item in group["items"]
            )
        )

        self.selected_total_price = sum(
            (
                group["store_total_price"]
                for group in self.selected_grouped_items
            ),
            Decimal("0.00"),
        )

        # ------------------------------------------------------------------
        # SHIPPING
        # ------------------------------------------------------------------

        self.shipping_total = sum(
            (
                group["shipping_total"]
                for group in self.selected_grouped_items
            ),
            Decimal("0.00"),
        )

        self.shipping_total = self.shipping_total.quantize(
            Decimal("0.01"),
        )

        self.is_free_shipping = (
            self.shipping_total == Decimal("0.00")
        )


        # ------------------------------------------------------------------
        # CHECKOUT TOTAL
        # ------------------------------------------------------------------

        self.checkout_total = (
            self.selected_total_price
            + self.shipping_total
        )

        self.checkout_total = self.checkout_total.quantize(
            Decimal("0.01"),
        )


        return super().get(
            request,
            *args,
            **kwargs,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(
            **kwargs
        )

        # get() içerisinde hazırlanmış read-only cart context.
        context.update(
            self.cart_context
        )

        # ------------------------------------------------------------------
        # CHECKOUT'A ÖZEL VERİLER
        # ------------------------------------------------------------------

        context["selected_grouped_items"] = (
            self.selected_grouped_items
        )

        context["selected_items_count"] = (
            self.selected_items_count
        )

        context["selected_total_price"] = (
            self.selected_total_price
        )

        context["shipping_total"] = (
            self.shipping_total
        )

        context["is_free_shipping"] = (
            self.is_free_shipping
        )

        context["checkout_total"] = (
            self.checkout_total
        )

        # ------------------------------------------------------------------
        # SAVED ADDRESSES
        # ------------------------------------------------------------------

        if self.request.user.is_authenticated:
            context["addresses"] = (
                self.request.user.addresses
                .order_by(
                    "-is_default",
                    "-id",
                )
            )
        else:
            context["addresses"] = []

         # ------------------------------------------------------------------
        # SAVED CARDS
        # ------------------------------------------------------------------

        if self.request.user.is_authenticated:
            context["stored_cards"] = (
                CardStorageService.list_cards(
                    user=self.request.user,
                    active_only=True,
                )
            )
        else:
            context["stored_cards"] = []

        return context

    def _build_selected_grouped_items(self):
        """
        Checkout ekranında gösterilecek ürünleri hazırlar.

        Her mağaza için:

            - seçili ürünler
            - mağaza ara toplamı
            - mağaza kargo ücreti
            - mağaza toplamı
            - ücretsiz kargo durumu

        hazırlanır.

        Bu method yalnızca checkout ekranı için presentation/context
        verisi üretir.

        Gerçek sipariş finansalları:

            OrderService._calculate_order_totals()

        tarafından yeniden hesaplanır.
        """

        grouped_items = self.cart_context.get(
            "grouped_items",
            []
        )

        selected_groups = []

        for group in grouped_items:

            # group bir dict olduğu için key erişimi kullanılmalı.
            items = group.get("items", [])

            selected_items = [
                item
                for item in items
                if item.is_selected
            ]

            # Bu mağazada seçili ürün yoksa checkout'ta gösterme.
            if not selected_items:
                continue

            store_total_price = sum(
                (
                    item.unit_price * item.quantity
                    for item in selected_items
                ),
                Decimal("0.00"),
            )

            store_total_price = store_total_price.quantize(
                Decimal("0.01")
            )

            # --------------------------------------------------------------
            # STORE SHIPPING
            # --------------------------------------------------------------
    
            shipping_total = (
                ShippingService.calculate_shipping(
                    subtotal=store_total_price,
                )
            )
    
            shipping_total = shipping_total.quantize(
                Decimal("0.01")
            )
    
            # --------------------------------------------------------------
            # STORE TOTAL
            # --------------------------------------------------------------
    
            store_checkout_total = (
                store_total_price
                + shipping_total
            )
    
            store_checkout_total = (
                store_checkout_total.quantize(
                    Decimal("0.01")
                )
            )
    
            # --------------------------------------------------------------
            # GROUP
            # --------------------------------------------------------------
    
            selected_groups.append(
                {
                    "store": group.get("store"),
                    "items": selected_items,

                    "store_item_count": sum(
                        item.quantity
                        for item in selected_items
                    ),
    
                    "store_total_price": (
                        store_total_price
                    ),
    
                    "shipping_total": (
                        shipping_total
                    ),
    
                    "store_checkout_total": (
                        store_checkout_total
                    ),
    
                    "is_free_shipping": (
                        shipping_total == Decimal("0.00")
                        and store_total_price > Decimal("0.00")
                    ),
                }
            )
    
        return selected_groups

# =============================================================================
# ORDER SUCCESS PAGE
# =============================================================================

class OrderSuccessView(TemplateView):
    """
    Başarılı ödeme sonrasında gösterilen sipariş onay sayfası.

    GET:
        /orders/checkout/<order_number>/success/

    Sorumlulukları:
        - Order erişimini doğrulamak
        - Kullanıcının / guest session'ın sipariş sahibi olduğunu kontrol etmek
        - Sipariş için gerçekten başarılı PaymentTransaction bulunduğunu kontrol etmek
        - Başarı sayfasını render etmek

    Business logic içermez.
    """

    template_name = "orders/order_success.html"

    def get(
        self,
        request,
        order_number,
        *args,
        **kwargs,
    ):
        self.order = self._get_order(
            request=request,
            order_number=order_number,
        )

        self.payment_transaction = self._get_successful_payment(
            order=self.order,
        )

        return super().get(
            request,
            *args,
            **kwargs,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(
            **kwargs,
        )

        context["order"] = self.order
        context["payment_transaction"] = (
            self.payment_transaction
        )

        return context

    # =========================================================================
    # ORDER ACCESS
    # =========================================================================

    @staticmethod
    def _get_order(
        *,
        request,
        order_number,
    ):
        order = get_object_or_404(
            Order.objects.select_related("user"),
            order_number=order_number,
        )

        # ---------------------------------------------------------------------
        # AUTHENTICATED USER
        # ---------------------------------------------------------------------

        if request.user.is_authenticated:
            if order.user_id != request.user.id:
                raise Http404

            return order

        # ---------------------------------------------------------------------
        # GUEST
        # ---------------------------------------------------------------------

        guest_order_number = (
            request.session.get(
                "checkout_order_number",
            )
        )

        if guest_order_number != order.order_number:
            raise Http404

        return order

    # =========================================================================
    # PAYMENT
    # =========================================================================

    @staticmethod
    def _get_successful_payment(
        *,
        order,
    ):
        payment_transaction = (
            PaymentTransaction.objects
            .filter(
                order=order,
                status=PaymentStatus.SUCCESS,
            )
            .order_by("-id")
            .first()
        )

        if not payment_transaction:
            raise Http404

        return payment_transaction

# =============================================================================
# BASE ORDER API VIEW
# =============================================================================

class BaseOrderAPIView(View):
    """
    Order / Checkout JSON endpoint'leri için ortak base view.

    Sorumlulukları:
        - JSON body parse etmek
        - Standart success response üretmek
        - Standart error response üretmek
        - Domain exception'larını HTTP response'a çevirmek
        - Beklenmeyen exception'ları loglamak

    Business logic içermez.
    """

    # -------------------------------------------------------------------------
    # DISPATCH / EXCEPTION HANDLING
    # -------------------------------------------------------------------------

    def dispatch(
        self,
        request,
        *args,
        **kwargs,
    ):
        try:
            return super().dispatch(
                request,
                *args,
                **kwargs,
            )

        # ---------------------------------------------------------------------
        # 404 - NOT FOUND
        # ---------------------------------------------------------------------

        except StoredCardNotFoundError as exc:
            logger.info(
                "Stored card not found. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code="STORED_CARD_NOT_FOUND",
                status=404,
            )

        except OrderNotFoundError as exc:
            logger.info(
                "Order not found. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code="ORDER_NOT_FOUND",
                status=404,
            )

        # ---------------------------------------------------------------------
        # 403 - ACCESS DENIED
        # ---------------------------------------------------------------------

        except CartAccessError as exc:
            logger.info(
                "Cart access denied. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code="CART_ACCESS_DENIED",
                status=403,
            )

        # ---------------------------------------------------------------------
        # 409 - CONFLICT
        # ---------------------------------------------------------------------

        except CardStorageOperationInProgressError as exc:
            logger.info(
                "Stored card operation in progress. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code="CARD_STORAGE_OPERATION_IN_PROGRESS",
                status=409,
            )

        except (
            InsufficientStockError,
            InvalidOrderStateError,
            ReservationError,
            ReservationExpiredError,
            PaymentAlreadyInProgressError,
            PaymentAlreadyProcessedError,
        ) as exc:
            logger.info(
                "Order/payment conflict. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code=self._get_error_code(exc),
                status=409,
            )

        # ---------------------------------------------------------------------
        # 400 - BAD REQUEST / VALIDATION
        # ---------------------------------------------------------------------

        except (
            CartItemSelectionError,
            EmptyOrderError,
            InvalidAddressError,
            InvalidCurrencyError,
            ProductUnavailableError,
            PaymentValidationError,
        ) as exc:
            logger.info(
                "Order/payment validation error. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code=self._get_error_code(exc),
                status=400,
            )

        # ---------------------------------------------------------------------
        # 502 - PAYMENT GATEWAY
        # ---------------------------------------------------------------------

        except CardStorageConsistencyError as exc:
            logger.error(
                "Stored card consistency error. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "Kayıtlı kart işlemi doğrulanamadı. "
                    "Lütfen daha sonra tekrar deneyin."
                ),
                code="CARD_STORAGE_CONSISTENCY_ERROR",
                status=502,
            )

        except CardStorageGatewayError as exc:
            logger.warning(
                "Stored card gateway error. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "Kayıtlı kart servisiyle iletişim "
                    "kurulurken bir sorun oluştu."
                ),
                code="CARD_STORAGE_GATEWAY_ERROR",
                status=502,
            )

        except (
            PaymentGatewayError,
            PaymentInitializationError,
        ) as exc:
            logger.warning(
                "Payment gateway error. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "Ödeme sağlayıcısı ile iletişim kurulurken "
                    "bir sorun oluştu. Lütfen tekrar deneyin."
                ),
                code=self._get_error_code(exc),
                status=502,
            )

        # ---------------------------------------------------------------------
        # 502 - PAYMENT VERIFICATION
        # ---------------------------------------------------------------------

        except PaymentVerificationError as exc:
            logger.error(
                "Payment verification failed. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "Ödeme doğrulanamadı. "
                    "Lütfen işlemi tekrar deneyin."
                ),
                code="PAYMENT_VERIFICATION_ERROR",
                status=502,
            )

        # ---------------------------------------------------------------------
        # OTHER DOMAIN ERRORS
        # ---------------------------------------------------------------------

        except OrderDomainError as exc:
            logger.warning(
                "Order domain operation failed. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "İşlem gerçekleştirilemedi. "
                    "Lütfen tekrar deneyin."
                ),
                code="ORDER_ERROR",
                status=400,
            )

        # ---------------------------------------------------------------------
        # UNEXPECTED SYSTEM ERROR
        # ---------------------------------------------------------------------

        except Exception:
            logger.exception(
                "Unexpected order error. "
                "method=%s path=%s",
                request.method,
                request.path,
            )

            return self.error_response(
                message=(
                    "Sistemsel bir hata oluştu. "
                    "Lütfen tekrar deneyin."
                ),
                code="INTERNAL_ERROR",
                status=500,
            )

    # -------------------------------------------------------------------------
    # ERROR CODE
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_error_code(exc):
        """
        Exception class isminden API error code üretir.

        Örnek:

            InvalidAddressError
            ->
            INVALID_ADDRESS
        """

        name = exc.__class__.__name__

        if name.endswith("Error"):
            name = name[:-5]

        result = []

        for char in name:
            if char.isupper() and result:
                result.append("_")

            result.append(
                char.upper()
            )

        return "".join(result)

    # -------------------------------------------------------------------------
    # JSON PARSE
    # -------------------------------------------------------------------------

    @staticmethod
    def require_json(request):
        """
        JSON request body parse eder.

        Başarılı:

            (data, None)

        Hatalı:

            (None, JsonResponse)
        """

        if not request.body:
            return None, BaseOrderAPIView.error_response(
                message="Request body boş olamaz.",
                code="EMPTY_REQUEST_BODY",
                status=400,
            )

        try:
            data = json.loads(
                request.body.decode("utf-8")
            )

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            TypeError,
            ValueError,
        ):
            return None, BaseOrderAPIView.error_response(
                message="Geçersiz JSON.",
                code="INVALID_JSON",
                status=400,
            )

        if not isinstance(data, dict):
            return None, BaseOrderAPIView.error_response(
                message="JSON object gönderilmelidir.",
                code="INVALID_JSON_OBJECT",
                status=400,
            )

        return data, None

    # -------------------------------------------------------------------------
    # SUCCESS RESPONSE
    # -------------------------------------------------------------------------

    @staticmethod
    def success_response(
        data=None,
        status=200,
    ):
        response_data = {
            "success": True,
        }

        if data is not None:
            response_data.update(
                data
            )

        return JsonResponse(
            response_data,
            status=status,
        )

    # -------------------------------------------------------------------------
    # ERROR RESPONSE
    # -------------------------------------------------------------------------

    @staticmethod
    def error_response(
        message,
        code=None,
        status=400,
    ):
        response_data = {
            "success": False,
            "error": message,
        }

        if code is not None:
            response_data["code"] = code

        return JsonResponse(
            response_data,
            status=status,
        )

    # -------------------------------------------------------------------------
    # STRING VALUE
    # -------------------------------------------------------------------------

    @staticmethod
    def _string_value(value):
        """
        Inline address alanını güvenli biçimde normalize eder.

        None:
            ""

        string:
            strip edilmiş hali

        Diğer tipler:
            InvalidAddressError
        """

        if value is None:
            return ""

        if not isinstance(
            value,
            str,
        ):
            raise InvalidAddressError(
                "Adres alanları string olmalıdır."
            )

        return value.strip()

    # -------------------------------------------------------------------------
    # ADDRESS
    # -------------------------------------------------------------------------

    @classmethod
    def _get_address(
        cls,
        *,
        request,
        data,
        prefix,
        required=True,
    ):
        """
        Authenticated kullanıcı:

            {
                "shipping_address_id": 12
            }

        Guest:

            {
                "shipping_address": {
                    "full_name": "...",
                    "phone_number": "...",
                    "address_line1": "...",
                    "address_line2": "...",
                    "city": "...",
                    "state": "...",
                    "postal_code": "..."
                }
            }

        Dönen değer:

            Address
            AddressData
            None
        """

        address_id_key = (
            f"{prefix}_address_id"
        )

        address_data_key = (
            f"{prefix}_address"
        )

        has_saved_address = (
            address_id_key in data
        )

        has_inline_address = (
            address_data_key in data
        )

        # ---------------------------------------------------------------------
        # ADDRESS SOURCE CONFLICT
        # ---------------------------------------------------------------------

        if (
            has_saved_address
            and has_inline_address
        ):
            raise InvalidAddressError(
                f"{address_id_key} ve "
                f"{address_data_key} "
                "aynı anda gönderilemez."
            )

        # ---------------------------------------------------------------------
        # SAVED ADDRESS
        # ---------------------------------------------------------------------

        if has_saved_address:
            raw_address_id = data.get(
                address_id_key
            )

            if not request.user.is_authenticated:
                raise InvalidAddressError(
                    "Kayıtlı adres kullanmak için "
                    "giriş yapmalısınız."
                )

            # bool -> int dönüşmesini engelle.
            if isinstance(
                raw_address_id,
                bool,
            ):
                raise InvalidAddressError(
                    "Geçersiz adres ID."
                )

            try:
                address_id = int(
                    raw_address_id
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise InvalidAddressError(
                    "Geçersiz adres ID."
                ) from exc

            if address_id <= 0:
                raise InvalidAddressError(
                    "Geçersiz adres ID."
                )

            address = (
                Address.objects
                .filter(
                    pk=address_id,
                    user=request.user,
                )
                .first()
            )

            if not address:
                raise InvalidAddressError(
                    "Adres bulunamadı."
                )

            return address

        # ---------------------------------------------------------------------
        # INLINE ADDRESS
        # ---------------------------------------------------------------------

        raw_address = data.get(
            address_data_key
        )

        if raw_address is None:
            if required:
                raise InvalidAddressError(
                    f"{address_data_key} veya "
                    f"{address_id_key} "
                    "gönderilmelidir."
                )

            return None

        if not isinstance(
            raw_address,
            dict,
        ):
            raise InvalidAddressError(
                f"{address_data_key} "
                "object olmalıdır."
            )

        return AddressData(
            full_name=cls._string_value(
                raw_address.get(
                    "full_name"
                )
            ),
            phone_number=cls._string_value(
                raw_address.get(
                    "phone_number"
                )
            ),
            address_line1=cls._string_value(
                raw_address.get(
                    "address_line1"
                )
            ),
            address_line2=cls._string_value(
                raw_address.get(
                    "address_line2"
                )
            ),
            city=cls._string_value(
                raw_address.get(
                    "city"
                )
            ),
            state=cls._string_value(
                raw_address.get(
                    "state"
                )
            ),
            postal_code=cls._string_value(
                raw_address.get(
                    "postal_code"
                )
            ),
        )

    # -------------------------------------------------------------------------
    # CART ITEM IDS
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_cart_item_ids(data):
        """
        Optional:

            {
                "cart_item_ids": [1, 2, 5]
            }

        Gönderilmezse OrderService bütün selected item'ları kullanır.
        """

        if "cart_item_ids" not in data:
            return None

        value = data["cart_item_ids"]

        if not isinstance(
            value,
            list,
        ):
            raise CartItemSelectionError(
                "cart_item_ids liste olmalıdır."
            )

        normalized_ids = []

        for item_id in value:

            # bool -> int dönüşmesini engelle.
            if isinstance(
                item_id,
                bool,
            ):
                raise CartItemSelectionError(
                    "Geçersiz cart item ID."
                )

            try:
                parsed_id = int(
                    item_id
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise CartItemSelectionError(
                    "Geçersiz cart item ID."
                ) from exc

            if parsed_id <= 0:
                raise CartItemSelectionError(
                    "Geçersiz cart item ID."
                )

            normalized_ids.append(
                parsed_id
            )

        return normalized_ids


# ==============================================================================
# BASE STORED CARD API VIEW
# ==============================================================================


class BaseStoredCardAPIView(BaseOrderAPIView):
    """
    Stored Card endpoint'leri için ortak API base view.

    Stored Card yalnızca authenticated kullanıcılar içindir.

    Sorumluluk:
        - authentication kontrolü
        - standart BaseOrderAPIView response yapısını kullanmak

    Business logic içermez.
    """

    def dispatch(
        self,
        request,
        *args,
        **kwargs,
    ):
        if not request.user.is_authenticated:
            return self.error_response(
                message=(
                    "Kayıtlı kart işlemleri için "
                    "giriş yapmalısınız."
                ),
                code="AUTHENTICATION_REQUIRED",
                status=401,
            )

        return super().dispatch(
            request,
            *args,
            **kwargs,
        )

# ==============================================================================
# STORED CARD LIST / CREATE API
# ==============================================================================


class StoredCardListCreateAPIView(
    BaseStoredCardAPIView
):
    """
    Kullanıcının kayıtlı kartlarını listeler
    veya yeni kart kaydeder.

    GET:
        /orders/payment/cards/

    POST:
        /orders/payment/cards/

    POST Header:
        Idempotency-Key: <unique-key>
    """

    http_method_names = [
        "get",
        "post",
        "options",
    ]

    # --------------------------------------------------------------------------
    # GET
    # --------------------------------------------------------------------------

    def get(
        self,
        request,
        *args,
        **kwargs,
    ):
        cards = CardStorageService.list_cards(
            user=request.user,
            active_only=True,
        )

        return self.success_response(
            {
                "cards": [
                    self._serialize_card(card)
                    for card in cards
                ],
            }
        )

    # --------------------------------------------------------------------------
    # POST
    # --------------------------------------------------------------------------

    def post(
        self,
        request,
        *args,
        **kwargs,
    ):
        data, error = self.require_json(request)

        if error:
            return error

        idempotency_key = (
            request.headers.get(
                "Idempotency-Key"
            )
            or ""
        ).strip()

        card = StoredCardCreateData(
            card_holder_name=str(
                data.get(
                    "card_holder_name",
                    "",
                )
            ).strip(),

            card_number=str(
                data.get(
                    "card_number",
                    "",
                )
            ).strip(),

            expire_month=str(
                data.get(
                    "expire_month",
                    "",
                )
            ).strip(),

            expire_year=str(
                data.get(
                    "expire_year",
                    "",
                )
            ).strip(),

            card_alias=str(
                data.get(
                    "card_alias",
                    "",
                )
            ).strip(),
        )

        make_default = data.get(
            "make_default",
            False,
        )

        if not isinstance(
            make_default,
            bool,
        ):
            raise PaymentValidationError(
                "make_default boolean olmalıdır."
            )

        stored_card = (
            CardStorageService.create_card(
                user=request.user,
                card=card,
                idempotency_key=idempotency_key,
                make_default=make_default,
            )
        )

        return self.success_response(
            {
                "message": (
                    "Kart başarıyla kaydedildi."
                ),
                "card": self._serialize_card(
                    stored_card
                ),
            },
            status=201,
        )

    # --------------------------------------------------------------------------
    # SERIALIZER
    # --------------------------------------------------------------------------

    @staticmethod
    def _serialize_card(
        card,
    ):
        return {
            "id": card.id,
            "card_alias": card.card_alias,
            "bin_number": card.bin_number,
            "last_four_digits": (
                card.last_four_digits
            ),
            "card_type": card.card_type,
            "card_association": (
                card.card_association
            ),
            "card_family": card.card_family,
            "card_bank_code": (
                card.card_bank_code
            ),
            "card_bank_name": (
                card.card_bank_name
            ),
            "expire_month": (
                card.expire_month
            ),
            "expire_year": (
                card.expire_year
            ),
            "is_default": card.is_default,
            "is_active": card.is_active,
        }

# ==============================================================================
# STORED CARD DELETE API
# ==============================================================================


class StoredCardDeleteAPIView(
    BaseStoredCardAPIView
):
    """
    Kullanıcının kayıtlı kartını siler.

    DELETE:
        /orders/payment/cards/<card_id>/

    Header:
        Idempotency-Key: <unique-key>
    """

    http_method_names = [
        "delete",
        "options",
    ]

    def delete(
        self,
        request,
        card_id,
        *args,
        **kwargs,
    ):
        idempotency_key = (
            request.headers.get(
                "Idempotency-Key"
            )
            or ""
        ).strip()

        card = (
            CardStorageService.delete_card(
                user=request.user,
                card_id=card_id,
                idempotency_key=idempotency_key,
            )
        )

        return self.success_response(
            {
                "message": (
                    "Kayıtlı kart başarıyla silindi."
                ),
                "card": (
                    StoredCardListCreateAPIView
                    ._serialize_card(card)
                ),
            }
        )

# ==============================================================================
# STORED CARD DEFAULT API
# ==============================================================================


class StoredCardDefaultAPIView(
    BaseStoredCardAPIView
):
    """
    Kullanıcının aktif kayıtlı kartını
    default kart olarak seçer.

    POST:
        /orders/payment/cards/<card_id>/default/
    """

    http_method_names = [
        "post",
        "options",
    ]

    def post(
        self,
        request,
        card_id,
        *args,
        **kwargs,
    ):
        card = (
            CardStorageService.set_default_card(
                user=request.user,
                card_id=card_id,
            )
        )

        return self.success_response(
            {
                "message": (
                    "Varsayılan kart güncellendi."
                ),
                "card": (
                    StoredCardListCreateAPIView
                    ._serialize_card(card)
                ),
            }
        )

# =============================================================================
# CREATE ORDER API
# =============================================================================

class CheckoutCreateOrderAPIView(BaseOrderAPIView):
    """
    Checkout submit endpoint'i.

    POST /orders/checkout/create/

    Authenticated örnek:

        {
            "shipping_address_id": 12,
            "billing_address_id": 12,
            "cart_item_ids": [1, 2],
            "currency": "TRY"
        }

    Guest örnek:

        {
            "shipping_address": {
                "full_name": "John Doe",
                "phone_number": "+905...",
                "address_line1": "Example Street 1",
                "address_line2": "",
                "city": "Istanbul",
                "state": "Istanbul",
                "postal_code": "34000"
            },

            "customer_email": "customer@example.com",
            "customer_phone": "+905...",
            "currency": "TRY"
        }

    Bu endpoint payment başlatmaz.

    Gerçekleştirdiği flow:

        Cart
          ↓
        Order
          ↓
        SubOrder
          ↓
        OrderItem
          ↓
        StockReservation

    Başarılı olduğunda:

        Order.status = PENDING_PAYMENT
        Reservation.status = ACTIVE

    olur.
    """

    http_method_names = [
        "post",
        "options",
    ]

    def post(
        self,
        request,
        *args,
        **kwargs,
    ):
        # ---------------------------------------------------------------------
        # 1. JSON
        # ---------------------------------------------------------------------

        data, error = self.require_json(
            request
        )

        if error:
            return error

        # ---------------------------------------------------------------------
        # 2. CART
        # ---------------------------------------------------------------------

        cart = CartService.get_or_create_cart(
            request
        )

        # ---------------------------------------------------------------------
        # 3. SHIPPING ADDRESS
        # ---------------------------------------------------------------------

        shipping_address = self._get_address(
            request=request,
            data=data,
            prefix="shipping",
            required=True,
        )

        # ---------------------------------------------------------------------
        # 4. BILLING ADDRESS
        # ---------------------------------------------------------------------

        billing_address = self._get_address(
            request=request,
            data=data,
            prefix="billing",
            required=False,
        )

        # Billing adresi gönderilmezse shipping kullanılır.
        if billing_address is None:
            billing_address = shipping_address

        # ---------------------------------------------------------------------
        # 5. CUSTOMER CONTACT
        # ---------------------------------------------------------------------

        customer_email = data.get(
            "customer_email"
        )

        customer_phone = data.get(
            "customer_phone"
        )

        # ---------------------------------------------------------------------
        # 6. CART ITEMS
        # ---------------------------------------------------------------------

        cart_item_ids = (
            self._get_cart_item_ids(
                data
            )
        )

        # ---------------------------------------------------------------------
        # 7. CURRENCY
        # ---------------------------------------------------------------------

        currency = data.get(
            "currency",
            OrderService.DEFAULT_CURRENCY,
        )

        # ---------------------------------------------------------------------
        # 8. USER / SESSION
        # ---------------------------------------------------------------------

        if request.user.is_authenticated:
            user = request.user
            session_key = None

        else:
            user = None
            session_key = (
                request.session.session_key
            )

        # ---------------------------------------------------------------------
        # 9. CREATE ORDER + RESERVE STOCK
        # ---------------------------------------------------------------------
        #
        # OrderService içerisinde:
        #
        #     Order
        #       ↓
        #     SubOrder
        #       ↓
        #     OrderItem
        #       ↓
        #     StockReservation
        #
        # aynı transaction içerisinde oluşturulur.
        # ---------------------------------------------------------------------

        order = OrderService.create_from_cart(
            cart=cart,
            user=user,
            session_key=session_key,
            shipping_address=shipping_address,
            billing_address=billing_address,
            cart_item_ids=cart_item_ids,
            customer_email=customer_email,
            customer_phone=customer_phone,
            currency=currency,
        )

        if not request.user.is_authenticated:
            request.session["checkout_order_number"] = (
                order.order_number
            )
            request.session.modified = True

        # ---------------------------------------------------------------------
        # 10. RESPONSE
        # ---------------------------------------------------------------------

        return self.success_response(
            {
                "message": (
                    "Sipariş oluşturuldu. "
                    "Ödeme adımına geçebilirsiniz."
                ),
                "order_number": (
                    order.order_number
                ),
                "status": order.status,
                "currency": order.currency,
                "subtotal": str(
                    order.subtotal
                ),
                "discount_amount": str(
                    order.discount_amount
                ),
                "shipping_amount": str(
                    order.shipping_amount
                ),
                "tax_amount": str(
                    order.tax_amount
                ),
                "total_amount": str(
                    order.total_amount
                ),
            },
            status=201,
        )

# =============================================================================
# CHECKOUT PAYMENT API
# =============================================================================

class CheckoutPaymentAPIView(BaseOrderAPIView):
    """
    Mevcut PENDING_PAYMENT Order için iyzico 3DS initialize başlatır.

    POST /orders/checkout/<order_number>/payment/

    Yeni kart örneği:

        {
            "payment_method": "new_card",
            "card_holder_name": "John Doe",
            "card_number": "5528790000000008",
            "expire_month": "12",
            "expire_year": "2030",
            "cvc": "123",
            "installment": 1
        }

    Kayıtlı kart örneği:

        {
            "payment_method": "stored_card",
            "stored_card_id": 15,
            "installment": 1
        }

    Bu endpoint:

        Order
            ↓
        PaymentService.initialize_3ds()
            ↓
        PaymentTransaction(INITIATED)
            ↓
        iyzico
            ↓
        PaymentTransaction(PENDING)
            ↓
        threeDSHtmlContent

    döngüsünü başlatır.

    Bu view:
        - PaymentTransaction oluşturmaz.
        - Order status değiştirmez.
        - Reservation consume etmez.
        - Stok düşmez.
        - Cart temizlemez.
        - Kart bilgilerini DB'ye yazmaz.

    Yeni kart:
        - PAN / CVC yalnızca request yaşam döngüsü boyunca memory'de bulunur.

    Kayıtlı kart:
        - PAN / CVC request'te bulunmaz.
        - Yalnızca stored_card_id gönderilir.
        - Gerçek cardUserKey / cardToken PaymentService tarafından
          kullanıcının sahip olduğu aktif StoredCard kaydından alınır.
    """

    http_method_names = [
        "post",
        "options",
    ]

    def post(
        self,
        request,
        order_number,
        *args,
        **kwargs,
    ):
        # ---------------------------------------------------------------------
        # 1. JSON
        # ---------------------------------------------------------------------

        data, error = self.require_json(request)

        if error:
            return error

        # ---------------------------------------------------------------------
        # 2. ORDER
        # ---------------------------------------------------------------------

        order = self._get_order(
            request=request,
            order_number=order_number,
        )

        # ---------------------------------------------------------------------
        # 3. PAYMENT METHOD
        # ---------------------------------------------------------------------

        payment_method = self._get_payment_method(
            data=data,
        )

        # ---------------------------------------------------------------------
        # 4. CARD
        # ---------------------------------------------------------------------
        #
        # Yeni kart:
        #
        #     Request'ten PaymentCardData oluşturulur.
        #
        # Kayıtlı kart:
        #
        #     Kart numarası / CVC alınmaz.
        #     stored_card_id PaymentService'e gönderilir.
        # ---------------------------------------------------------------------

        payment_card = None
        stored_card_id = None

        if payment_method == PaymentService.PAYMENT_METHOD_NEW_CARD:

            payment_card = self._build_payment_card(
                data=data,
            )

        else:

            stored_card_id = (
                self._get_stored_card_id(
                    data=data,
                )
            )

        # ---------------------------------------------------------------------
        # 5. BUYER
        # ---------------------------------------------------------------------

        buyer = self._build_buyer(
            request=request,
            order=order,
        )

        # ---------------------------------------------------------------------
        # 6. INSTALLMENT
        # ---------------------------------------------------------------------

        installment = data.get(
            "installment",
            1,
        )

        # ---------------------------------------------------------------------
        # 7. INITIALIZE 3DS
        # ---------------------------------------------------------------------

        result = PaymentService.initialize_3ds(
            order_id=order.id,
            buyer=buyer,
            payment_method=payment_method,
            payment_card=payment_card,
            stored_card_id=stored_card_id,
            installment=installment,
        )

        # ---------------------------------------------------------------------
        # 8. RESPONSE
        # ---------------------------------------------------------------------

        return self.success_response(
            {
                "payment_transaction_id": (
                    result.payment_transaction_id
                ),
                "payment_id": result.payment_id,
                "conversation_id": result.conversation_id,
                "three_ds_html_content": (
                    result.three_ds_html_content
                ),
            }
        )

    # =========================================================================
    # ORDER ACCESS
    # =========================================================================

    @staticmethod
    def _get_order(
        *,
        request,
        order_number,
    ):
        """
        Order erişimini HTTP katmanında kontrol eder.

        Authenticated:
            order.user == request.user

        Guest:
            create-order aşamasında session'a yazılmış
            order_number ile eşleşme aranır.

        Guest checkout için Order modelinde session_key tutulmadığı
        için session binding kullanıyoruz.
        """

        order = (
            Order.objects
            .select_related("user")
            .filter(
                order_number=order_number,
            )
            .first()
        )

        if not order:
            raise OrderNotFoundError(
                "Sipariş bulunamadı."
            )

        # ---------------------------------------------------------------------
        # AUTHENTICATED USER
        # ---------------------------------------------------------------------

        if request.user.is_authenticated:

            if order.user_id != request.user.id:
                raise CartAccessError(
                    "Bu siparişe erişim yetkiniz bulunmuyor."
                )

            return order

        # ---------------------------------------------------------------------
        # GUEST
        # ---------------------------------------------------------------------

        guest_order_number = request.session.get(
            "checkout_order_number"
        )

        if guest_order_number != order.order_number:
            raise CartAccessError(
                "Bu siparişe erişim doğrulanamadı."
            )

        return order

    # =========================================================================
    # PAYMENT METHOD
    # =========================================================================

    @staticmethod
    def _get_payment_method(*, data):
        """
        Request body'den payment method okur.

        Varsayılan:

            new_card

        Desteklenen yöntemler:

            new_card
            stored_card
        """

        payment_method = data.get(
            "payment_method",
            PaymentService.PAYMENT_METHOD_NEW_CARD,
        )

        if not isinstance(
            payment_method,
            str,
        ):
            raise PaymentValidationError(
                "Geçersiz ödeme yöntemi."
            )

        payment_method = payment_method.strip().lower()

        if payment_method not in {
            PaymentService.PAYMENT_METHOD_NEW_CARD,
            PaymentService.PAYMENT_METHOD_STORED_CARD,
        }:
            raise PaymentValidationError(
                "Geçersiz ödeme yöntemi."
            )

        return payment_method

    # =========================================================================
    # STORED CARD ID
    # =========================================================================

    @staticmethod
    def _get_stored_card_id(*, data):
        """
        Request body'den stored_card_id okur.

        Örnek:

            {
                "payment_method": "stored_card",
                "stored_card_id": 15
            }

        Kart sahipliği / aktiflik / provider kontrolü
        PaymentService tarafından yapılır.
        """

        if "stored_card_id" not in data:
            raise PaymentValidationError(
                "Kayıtlı kart seçilmelidir."
            )

        raw_stored_card_id = data.get(
            "stored_card_id"
        )

        # bool -> int dönüşmesini engelle.
        if isinstance(
            raw_stored_card_id,
            bool,
        ):
            raise PaymentValidationError(
                "Geçersiz kayıtlı kart ID."
            )

        try:
            stored_card_id = int(
                raw_stored_card_id
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

        return stored_card_id

    # =========================================================================
    # PAYMENT CARD
    # =========================================================================

    @staticmethod
    def _build_payment_card(*, data):
        """
        Request body -> PaymentCardData

        Kart numarası / CVC burada yalnızca request yaşam döngüsü boyunca
        memory'de bulunur.

        DB'ye kaydedilmez.

        Bu method yalnızca:

            payment_method == new_card

        olduğunda çağrılır.
        """

        return PaymentCardData(
            card_holder_name=str(
                data.get(
                    "card_holder_name",
                    "",
                )
            ).strip(),

            card_number=str(
                data.get(
                    "card_number",
                    "",
                )
            ).strip(),

            expire_month=str(
                data.get(
                    "expire_month",
                    "",
                )
            ).strip(),

            expire_year=str(
                data.get(
                    "expire_year",
                    "",
                )
            ).strip(),

            cvc=str(
                data.get(
                    "cvc",
                    "",
                )
            ).strip(),
        )

    # =========================================================================
    # BUYER
    # =========================================================================

    @staticmethod
    def _build_buyer(
        *,
        request,
        order,
    ):
        """
        Checkout'ta zaten alınmış sipariş snapshot'larından
        iyzico buyer DTO'su oluşturur.

        name / surname:

            billing_full_name

        registration_address:

            billing address snapshot

        ip:

            request.META üzerinden alınır.
        """

        full_name = (
            str(
                order.billing_full_name
                or ""
            )
            .strip()
        )

        name, surname = (
            CheckoutPaymentAPIView
            ._split_full_name(
                full_name
            )
        )

        registration_address = (
            " ".join(
                part
                for part in [
                    order.billing_address_line1,
                    order.billing_address_line2,
                    order.billing_state,
                ]
                if str(
                    part or ""
                ).strip()
            )
        )

        return PaymentBuyerData(
            name=name,
            surname=surname,
            registration_address=registration_address,
            ip=CheckoutPaymentAPIView
            ._get_client_ip(
                request
            ),
        )

    # =========================================================================
    # NAME
    # =========================================================================

    @staticmethod
    def _split_full_name(full_name):
        full_name = " ".join(
            str(full_name or "").split()
        )

        if not full_name:
            raise PaymentValidationError(
                "Ad soyad bilgisi boş olamaz."
            )

        parts = full_name.split()

        if len(parts) < 2:
            raise PaymentValidationError(
                "Ad ve soyad bilgisi birlikte girilmelidir."
            )

        name = " ".join(
            parts[:-1]
        )

        surname = parts[-1]

        return name, surname

    # =========================================================================
    # IP
    # =========================================================================

    @staticmethod
    def _get_client_ip(request):
        """
        Reverse proxy arkasında gerçek client IP'sini almayı dener.

        NOT:
            X-Forwarded-For yalnızca uygulamanın güvenilir bir proxy
            arkasında çalıştığı sistemlerde güvenilir kabul edilmelidir.
        """

        forwarded_for = (
            request.META.get(
                "HTTP_X_FORWARDED_FOR"
            )
        )

        if forwarded_for:
            return (
                forwarded_for
                .split(",")[0]
                .strip()
            )

        return (
            request.META.get(
                "REMOTE_ADDR",
                "",
            )
            .strip()
        )

@method_decorator(csrf_exempt, name="dispatch")
class Iyzico3DSCallbackAPIView(View):
    """
    iyzico 3DS callback endpoint.

    POST:
        /payments/iyzico/3ds/callback/

    iyzico, 3DS doğrulaması sonrasında
    callbackUrl adresine form-urlencoded POST gönderir.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        *args,
        **kwargs,
    ):
        payment_id = (
            request.POST.get("paymentId") or ""
        ).strip()

        conversation_id = (
            request.POST.get("conversationId") or ""
        ).strip()

        conversation_data = (
            request.POST.get("conversationData") or ""
        ).strip()

        status = (
            request.POST.get("status") or ""
        ).strip().lower()

        md_status = (
            request.POST.get("mdStatus") or ""
        ).strip()

        # ==================================================================
        # CALLBACK INPUT VALIDATION
        # ==================================================================

        if not payment_id:
            return JsonResponse(
                {
                    "message": (
                        "3DS callback paymentId bilgisi eksik."
                    ),
                },
                status=400,
            )

        if not conversation_id:
            return JsonResponse(
                {
                    "message": (
                        "3DS callback conversationId bilgisi eksik."
                    ),
                },
                status=400,
            )

        # ==================================================================
        # 3DS AUTH FAILURE
        # ==================================================================

        if (
            status != "success"
            or md_status != "1"
        ):
            with transaction.atomic():
                payment_tx = (
                    PaymentTransaction.objects
                    .select_for_update()
                    .filter(
                        provider=PaymentService.PROVIDER,
                        conversation_id=conversation_id,
                    )
                    .first()
                )

                if payment_tx:
                    # Mevcut payment_id varsa farklı bir ID ile ezme.
                    if (
                        payment_tx.payment_id
                        and payment_tx.payment_id != payment_id
                    ):
                        return JsonResponse(
                            {
                                "message": (
                                    "3DS callback paymentId "
                                    "doğrulaması başarısız."
                                ),
                            },
                            status=400,
                        )

                    if payment_tx.status != PaymentStatus.SUCCESS:
                        payment_tx.payment_id = (
                            payment_id
                        )
                        payment_tx.status = (
                            PaymentStatus.FAILED
                        )

                        payment_tx.save(
                            update_fields=[
                                "payment_id",
                                "status",
                                "updated_at",
                            ]
                        )

            return JsonResponse(
                {
                    "message": "3DS doğrulaması başarısız.",
                    "status": status,
                    "md_status": md_status,
                },
                status=400,
            )

        # ==================================================================
        # SUCCESS → PAYMENT COMPLETION
        # ==================================================================

        try:
            result = PaymentService.complete_3ds(
                payment_id=payment_id,
                conversation_id=conversation_id,
                conversation_data=conversation_data,
            )

        except PaymentGatewayError as exc:
            logger.exception(
                "iyzico 3DS completion gateway error. "
                "payment_id=%s conversation_id=%s",
                payment_id,
                conversation_id,
            )

            return JsonResponse(
                {
                    "message": str(exc),
                },
                status=502,
            )

        except PaymentVerificationError as exc:
            logger.exception(
                "iyzico 3DS completion verification error. "
                "payment_id=%s conversation_id=%s",
                payment_id,
                conversation_id,
            )

            return JsonResponse(
                {
                    "message": str(exc),
                },
                status=400,
            )

        except PaymentError as exc:
            logger.exception(
                "iyzico 3DS completion payment error. "
                "payment_id=%s conversation_id=%s",
                payment_id,
                conversation_id,
            )

            return JsonResponse(
                {
                    "message": str(exc),
                },
                status=400,
            )

        # ==================================================================
        # SUCCESS
        # ==================================================================

        return redirect(
            "orders:order_success",
            order_number=result.order_number,
        )


@method_decorator(csrf_exempt, name="dispatch")
class IyzicoPaymentWebhookAPIView(View):
    """
    iyzico Direct Format webhook endpoint.

    Bu endpoint:
        - kullanıcı/session/auth gerektirmez.
        - CSRF token gerektirmez.
        - yalnızca X-IYZ-SIGNATURE-V3 doğrulanmış bildirimleri işler.
        - webhook SUCCESS için ikinci kez 3DS completion çağırmaz.
        - doğrulanmış SUCCESS sonucunu PaymentService üzerinden finalize eder.
        - webhook FAILURE'i local PaymentTransaction üzerinde FAILED yapar.
    """

    http_method_names = [
        "post",
        "options",
    ]

    MAX_BODY_BYTES = 64 * 1024

    def post(self, request, *args, **kwargs):
        # ------------------------------------------------------------------
        # BODY SIZE
        # ------------------------------------------------------------------

        content_length = request.META.get(
            "CONTENT_LENGTH"
        )

        if content_length:
            try:
                if int(content_length) > self.MAX_BODY_BYTES:
                    return JsonResponse(
                        {
                            "success": False,
                            "error": "Webhook payload too large.",
                        },
                        status=413,
                    )
            except (TypeError, ValueError):
                return JsonResponse(
                    {
                        "success": False,
                        "error": "Invalid Content-Length.",
                    },
                    status=400,
                )

        # ------------------------------------------------------------------
        # JSON
        # ------------------------------------------------------------------

        try:
            payload = json.loads(
                request.body.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Invalid JSON payload.",
                },
                status=400,
            )

        if not isinstance(payload, dict):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Webhook payload must be a JSON object.",
                },
                status=400,
            )

        # Django request.META header mapping:
        # X-IYZ-SIGNATURE-V3 -> HTTP_X_IYZ_SIGNATURE_V3
        signature = str(
            request.META.get(
                "HTTP_X_IYZ_SIGNATURE_V3",
                "",
            )
            or ""
        ).strip()

        payment_id = str(
            payload.get(
                "paymentId",
                "",
            )
            or ""
        ).strip()

        conversation_id = str(
            payload.get(
                "paymentConversationId",
                "",
            )
            or ""
        ).strip()

        event_type = str(
            payload.get(
                "iyziEventType",
                "",
            )
            or ""
        ).strip()

        status = str(
            payload.get(
                "status",
                "",
            )
            or ""
        ).strip().upper()

        # ------------------------------------------------------------------
        # SAFE LOGGING
        # ------------------------------------------------------------------

        logger.info(
            "iyzico webhook received. "
            "event_type=%s payment_id=%s conversation_id=%s status=%s "
            "iyzi_reference_code=%s",
            event_type,
            payment_id,
            conversation_id,
            status,
            str(
                payload.get(
                    "iyziReferenceCode",
                    "",
                )
                or ""
            ).strip(),
        )

        # ------------------------------------------------------------------
        # SERVICE
        # ------------------------------------------------------------------

        try:
            result = PaymentService.handle_webhook(
                payload=payload,
                signature=signature,
            )

        except PaymentVerificationError as exc:
            logger.warning(
                "iyzico webhook verification failed. "
                "event_type=%s payment_id=%s conversation_id=%s status=%s "
                "error=%s",
                event_type,
                payment_id,
                conversation_id,
                status,
                exc,
            )

            return JsonResponse(
                {
                    "success": False,
                    "error": "Webhook verification failed.",
                },
                status=400,
            )

        except PaymentValidationError as exc:
            logger.warning(
                "iyzico webhook validation failed. "
                "event_type=%s payment_id=%s conversation_id=%s status=%s "
                "error=%s",
                event_type,
                payment_id,
                conversation_id,
                status,
                exc,
            )

            return JsonResponse(
                {
                    "success": False,
                    "error": "Invalid webhook payload.",
                },
                status=400,
            )

        except PaymentGatewayError as exc:
            # 5xx => provider retry mekanizmasının çalışabilmesi için.
            logger.exception(
                "iyzico webhook provider/reconciliation error. "
                "event_type=%s payment_id=%s conversation_id=%s status=%s "
                "error=%s",
                event_type,
                payment_id,
                conversation_id,
                status,
                exc,
            )

            return JsonResponse(
                {
                    "success": False,
                    "error": "Webhook temporarily unavailable.",
                },
                status=502,
            )

        except Exception:
            # Beklenmeyen exception -> 5xx.
            # Böylece başarılı bir webhook'u sessizce kaybetmeyiz.
            logger.exception(
                "Unexpected iyzico webhook error. "
                "event_type=%s payment_id=%s conversation_id=%s status=%s",
                event_type,
                payment_id,
                conversation_id,
                status,
            )

            return JsonResponse(
                {
                    "success": False,
                    "error": "Webhook processing failed.",
                },
                status=500,
            )

        # ------------------------------------------------------------------
        # ALWAYS 2xx AFTER SUCCESSFUL PROCESSING
        # ------------------------------------------------------------------

        return JsonResponse(
            {
                "success": True,
                "handled": bool(
                    result.get(
                        "handled",
                        True,
                    )
                ),
            },
            status=200,
        )
