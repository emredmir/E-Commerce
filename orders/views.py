import json
import logging
from decimal import Decimal
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from accounts.models import Address
from cart.services.cart import CartService

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
    ProductUnavailableError,
    ReservationError,
)
from .services.order import AddressData, OrderService
from .services.shipping import ShippingService
from .services.stock_reservation import StockReservationService


logger = logging.getLogger(__name__)


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
            len(group["items"])
            for group in self.selected_grouped_items
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

        self.shipping_total = (
            ShippingService.calculate_shipping(
                subtotal=self.selected_total_price,
            )
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

        return context

    def _build_selected_grouped_items(self):
        """
        Checkout ekranında gösterilecek ürünleri hazırlar.

        Cart context içerisindeki grouped_items'dan yalnızca:

            item.is_selected == True

        olan CartItem'lar alınır.

        Ardından mağaza bazında tekrar gruplanır ve her mağazanın
        seçili ürünlerden oluşan ara toplamı hesaplanır.

        Bu yalnızca presentation/context hazırlığıdır.
        Sipariş oluşturma ve fiyat doğrulama OrderService tarafındadır.
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

            selected_groups.append(
                {
                    "store": group.get("store"),
                    "items": selected_items,
                    "store_total_price": store_total_price,
                }
            )

        return selected_groups


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

        except (
            InsufficientStockError,
            InvalidOrderStateError,
            ProductUnavailableError,
            ReservationError,
        ) as exc:
            logger.info(
                "Order conflict. "
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
        ) as exc:
            logger.info(
                "Order validation error. "
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