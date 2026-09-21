from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence

from django.db import transaction

from accounts.models import Address
from cart.models import Cart, CartItem
from products.models import (
    ProductStatus,
    StoreProduct,
    StoreProductStatus,
)

from ..exceptions import (
    CartAccessError,
    CartItemSelectionError,
    EmptyOrderError,
    InvalidAddressError,
    InvalidCurrencyError,
    OrderNotFoundError,
    ProductUnavailableError,
)
from ..models import (
    Order,
    OrderItem,
    OrderStatus,
    SubOrder,
    SubOrderStatus,
)

from ..services.stock_reservation import StockReservationService
from ..services.shipping import ShippingService


@dataclass(frozen=True)
class AddressData:
    """
    Checkout sırasında kullanılacak immutable adres snapshot verisi.

    Address modeline bağımlılığı azaltır ve ileride
    guest checkout için de kullanılabilir.
    """

    full_name: str
    phone_number: str
    address_line1: str
    address_line2: str
    city: str
    state: str
    postal_code: str

    @classmethod
    def from_model(cls, address: Address) -> "AddressData":
        return cls(
            full_name=(address.full_name or "").strip(),
            phone_number=(address.phone_number or "").strip(),
            address_line1=(address.address_line1 or "").strip(),
            address_line2=(address.address_line2 or "").strip(),
            city=(address.city or "").strip(),
            state=(address.state or "").strip(),
            postal_code=(address.postal_code or "").strip(),
        )


class OrderService:
    """
    Order domain business logic.

    Sorumlulukları:
        - Cart -> Order dönüşümü
        - Cart ownership kontrolü
        - Checkout item validation
        - StoreProduct locking
        - Store bazında SubOrder oluşturma
        - OrderItem snapshot oluşturma
        - Order / SubOrder toplamlarını oluşturma
        - Order shipping calculation
        - Customer / address snapshot oluşturma
        - Customer / seller erişim kontrolü


    Sorumlu olmadığı işler:
        - Payment provider iletişimi
        - PaymentTransaction oluşturma
        - Stock reservation
        - Fiziksel stok düşme
        - Cart temizleme
        - HTTP response / redirect / message

    Checkout akışı:

        OrderService
              ↓
        StockReservationService
              ↓
        PaymentService
              ↓
        Payment / Order completion

    NOT:
        Bu servis stok rezerve etmez.

        Buradaki stock kontrolü yalnızca sipariş oluşturulduğu
        andaki mevcut stok durumunu doğrular.

        Reservation lifecycle:
            StockReservationService
    """

    DEFAULT_CURRENCY = "TRY"

    # Şimdilik sistem yalnızca TRY destekliyor.
    # Multi-currency geldiğinde buraya yeni currency'ler eklenebilir.
    SUPPORTED_CURRENCIES = {
        "TRY",
    }

    # ======================================================================
    # CREATE ORDER
    # ======================================================================

    @classmethod
    @transaction.atomic
    def create_from_cart(
        cls,
        *,
        cart: Cart,
        user=None,
        session_key: str | None = None,
        shipping_address: Address | AddressData,
        billing_address: Address | AddressData | None = None,
        cart_item_ids: Sequence[int] | None = None,
        customer_email: str | None = None,
        customer_phone: str | None = None,
        currency: str = DEFAULT_CURRENCY,
    ) -> Order:
        """
        Sepetteki seçili ürünlerden PENDING_PAYMENT durumunda
        yeni bir Order oluşturur.

        Flow:

            1. Currency normalize / validate
            2. Cart ownership validate
            3. Address normalize / validate
            4. Customer contact normalize
            5. CartItem kayıtlarını lock et
            6. StoreProduct availability validate et
            7. Store bazında grupla
            8. Order totals hesapla
            9. Order oluştur
            10. SubOrder oluştur
            11. OrderItem snapshot oluştur
            12. StockReservationService.reserve_order()
            13. Order döndür

        Kritik:

            Bu method stok miktarı hakkında nihai karar vermez.

            Reservation oluşturma işlemi başarısız olursa
            transaction rollback olur ve Order da oluşturulmamış
            hale gelir.
        """

        # ------------------------------------------------------------------
        # 1. CURRENCY
        # ------------------------------------------------------------------

        currency = cls._normalize_currency(currency)
        cls._validate_currency(currency)

        # ------------------------------------------------------------------
        # 2. CART ACCESS
        # ------------------------------------------------------------------

        cls._validate_cart_access(
            cart=cart,
            user=user,
            session_key=session_key,
        )

        # ------------------------------------------------------------------
        # 3. ADDRESS
        # ------------------------------------------------------------------

        shipping = cls._normalize_address(
            address=shipping_address,
            user=user,
        )

        billing = cls._normalize_address(
            address=billing_address or shipping_address,
            user=user,
        )

        cls._validate_address(shipping)
        cls._validate_address(billing)

        # ------------------------------------------------------------------
        # 4. CUSTOMER CONTACT
        # ------------------------------------------------------------------

        normalized_email = cls._normalize_email(
            customer_email
            or getattr(user, "email", None)
        )

        normalized_phone = cls._normalize_phone(
            customer_phone
            or getattr(user, "phone_number", None)
            or shipping.phone_number
        )

        # ------------------------------------------------------------------
        # 5. CART ITEMS LOCK
        # ------------------------------------------------------------------

        cart_items = cls._get_selected_cart_items(
            cart=cart,
            cart_item_ids=cart_item_ids,
        )

        # ------------------------------------------------------------------
        # 6. STORE PRODUCT READ / AVAILABILITY
        # ------------------------------------------------------------------

        store_products = cls._get_locked_store_products(
            cart_items=cart_items,
        )

        # ------------------------------------------------------------------
        # 7. PRODUCT VALIDATION
        # ------------------------------------------------------------------

        cls._validate_cart_items(
            cart_items=cart_items,
            store_products=store_products,
        )

        # ------------------------------------------------------------------
        # 8. GROUP BY STORE
        # ------------------------------------------------------------------

        grouped_items = cls._group_items_by_store(
            cart_items=cart_items,
            store_products=store_products,
        )

        # ------------------------------------------------------------------
        # 9. CALCULATE ORDER TOTALS
        # ------------------------------------------------------------------

        order_totals = cls._calculate_order_totals(
            grouped_items=grouped_items,
        )

        # ------------------------------------------------------------------
        # 10. CREATE MAIN ORDER
        # ------------------------------------------------------------------

        order = Order.objects.create(
            user=(
                user
                if getattr(user, "is_authenticated", False)
                else None
            ),

            customer_email=normalized_email,
            customer_phone=normalized_phone,

            subtotal=order_totals["subtotal"],
            discount_amount=order_totals["discount_amount"],
            shipping_amount=order_totals["shipping_amount"],
            tax_amount=order_totals["tax_amount"],
            total_amount=order_totals["total_amount"],

            currency=currency,
            status=OrderStatus.PENDING_PAYMENT,

            # Shipping snapshot
            shipping_full_name=shipping.full_name,
            shipping_phone=shipping.phone_number,
            shipping_address_line1=shipping.address_line1,
            shipping_address_line2=shipping.address_line2,
            shipping_city=shipping.city,
            shipping_state=shipping.state,
            shipping_postal_code=shipping.postal_code,

            # Billing snapshot
            billing_full_name=billing.full_name,
            billing_phone=billing.phone_number,
            billing_address_line1=billing.address_line1,
            billing_address_line2=billing.address_line2,
            billing_city=billing.city,
            billing_state=billing.state,
            billing_postal_code=billing.postal_code,
        )

        # ------------------------------------------------------------------
        # 11. CREATE SUBORDERS + ORDER ITEMS
        # ------------------------------------------------------------------

        cls._create_suborders(
            order=order,
            grouped_items=grouped_items,
        )

        # ------------------------------------------------------------------
        # 12. RESERVE STOCK
        # ------------------------------------------------------------------
        #
        # Buradaki stok kararı tamamen StockReservationService'e aittir.
        #
        # reserve_order():
        #
        #     Order lock
        #         ↓
        #     StoreProduct lock
        #         ↓
        #     Active reservation lock
        #         ↓
        #     Available stock calculation
        #         ↓
        #     Reservation creation
        #
        # Eğer reservation başarısız olursa exception dış transaction'ı
        # rollback eder ve Order / SubOrder / OrderItem kayıtları da
        # geri alınır.

        StockReservationService.reserve_order(
            order=order,
        )

        # ------------------------------------------------------------------
        # 13. RETURN
        # ------------------------------------------------------------------

        return order

    # ======================================================================
    # CART ACCESS
    # ======================================================================

    @staticmethod
    def _validate_cart_access(
        *,
        cart: Cart,
        user,
        session_key: str | None,
    ) -> None:
        """
        Cart'ın checkout yapan kullanıcıya ait olduğunu doğrular.

        Authenticated user:
            cart.user == user

        Guest:
            cart.session_key == session_key
        """

        if getattr(user, "is_authenticated", False):
            if cart.user_id != user.pk:
                raise CartAccessError(
                    "Bu sepete erişim yetkiniz bulunmuyor."
                )

            return

        if (
            not session_key
            or cart.user_id is not None
            or cart.session_key != session_key
        ):
            raise CartAccessError(
                "Misafir sepetine erişim doğrulanamadı."
            )

    # ======================================================================
    # CART ITEMS
    # ======================================================================

    @staticmethod
    def _get_selected_cart_items(
        *,
        cart: Cart,
        cart_item_ids: Sequence[int] | None,
    ) -> list[CartItem]:
        """
        Checkout sırasında kullanılacak CartItem kayıtlarını lock eder.

        cart_item_ids verilmezse sepetteki tüm selected item'lar alınır.

        Lock sırası deterministik olarak:

            store_product_id -> id
        """

        queryset = (
            CartItem.objects
            .select_for_update(of=("self",))
            .select_related(
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                cart=cart,
                is_selected=True,
            )
        )

        # --------------------------------------------------------------
        # Belirli item'lar checkout ediliyorsa
        # --------------------------------------------------------------

        if cart_item_ids is not None:
            try:
                requested_ids = {
                    int(item_id)
                    for item_id in cart_item_ids
                }
            except (TypeError, ValueError) as exc:
                raise CartItemSelectionError(
                    "Geçersiz checkout ürünleri."
                ) from exc

            if not requested_ids:
                raise EmptyOrderError(
                    "Checkout için ürün seçilmemiş."
                )

            queryset = queryset.filter(
                id__in=requested_ids
            )

        items = list(
            queryset.order_by(
                "store_product_id",
                "id",
            )
        )

        if not items:
            raise EmptyOrderError(
                "Sipariş oluşturmak için seçili ürün bulunamadı."
            )

        # --------------------------------------------------------------
        # Gönderilen item ID'lerinin tamamı bulundu mu?
        # --------------------------------------------------------------

        if cart_item_ids is not None:
            requested_count = len(
                {
                    int(item_id)
                    for item_id in cart_item_ids
                }
            )

            if len(items) != requested_count:
                raise CartItemSelectionError(
                    "Checkout ürünlerinden biri artık geçerli değil."
                )

        return items

    # ======================================================================
    # STORE PRODUCT LOCK
    # ======================================================================

    # @staticmethod
    # def _get_locked_store_products(
    #     *,
    #     cart_items: Iterable[CartItem],
    # ) -> dict[int, StoreProduct]:
    #     """
    #     Siparişte kullanılacak StoreProduct kayıtlarını lock eder.

    #     Lock ID sırasına göre deterministiktir.

    #     Böylece concurrent checkout sırasında ters lock sırasından
    #     kaynaklanan deadlock ihtimali azaltılır.

    #     NOT:

    #         Bu lock reservation değildir.

    #         Reservation oluşturma ve lifecycle yönetimi:
    #             StockReservationService
    #     """

    #     store_product_ids = sorted(
    #         {
    #             item.store_product_id
    #             for item in cart_items
    #         }
    #     )

    #     queryset = (
    #         StoreProduct.objects
    #         .select_for_update()
    #         .select_related(
    #             "store",
    #             "variant",
    #             "variant__product",
    #         )
    #         .prefetch_related(
    #             "variant__attribute_values__attribute",
    #         )
    #         .filter(
    #             id__in=store_product_ids,
    #         )
    #         .order_by("id")
    #     )

    #     store_products = list(queryset)

    #     result = {
    #         store_product.id: store_product
    #         for store_product in store_products
    #     }

    #     if len(result) != len(store_product_ids):
    #         raise ProductUnavailableError(
    #             "Sepetteki ürünlerden biri artık mevcut değil."
    #         )

    #     return result

    # ======================================================================
    # STORE PRODUCT READ
    # ======================================================================

    # @staticmethod
    # def _get_store_products(
    #     *,
    #     cart_items: Iterable[CartItem],
    # ) -> dict[int, StoreProduct]:
    #     """
    #     Checkout'ta kullanılacak StoreProduct kayıtlarını getirir.

    #     ÖNEMLİ:

    #         Bu method StoreProduct lock etmez.

    #         OrderService'in görevi ürün bilgilerini ve
    #         satın alınabilirlik durumunu doğrulamaktır.

    #         Stok availability kararı ve reservation lock'u:

    #             StockReservationService

    #         tarafından transaction içerisinde yapılır.
    #     """

    #     store_product_ids = sorted(
    #         {
    #             item.store_product_id
    #             for item in cart_items
    #         }
    #     )

    #     if not store_product_ids:
    #         return {}

    #     store_products = list(
    #         StoreProduct.objects
    #         .select_related(
    #             "store",
    #             "variant",
    #             "variant__product",
    #         )
    #         .prefetch_related(
    #             "variant__attribute_values__attribute",
    #         )
    #         .filter(
    #             id__in=store_product_ids,
    #         )
    #         .order_by(
    #             "id",
    #         )
    #     )

    #     result = {
    #         store_product.id: store_product
    #         for store_product in store_products
    #     }

    #     if len(result) != len(store_product_ids):
    #         raise ProductUnavailableError(
    #             "Sepetteki ürünlerden biri artık mevcut değil."
    #         )

    #     return result

    @staticmethod
    def _get_locked_store_products(
        *,
        cart_items: Iterable[CartItem],
    ) -> dict[int, StoreProduct]:
        """
        Checkout'ta kullanılacak StoreProduct kayıtlarını getirir
        ve transaction boyunca lock eder.

        Bu lock reservation değildir.

        Amaç:
            - checkout sırasında StoreProduct verisinin değişmesini engellemek,
            - OrderItem fiyat / SKU / varyant snapshot'ının
              tutarlı olmasını sağlamak.

        Nihai stock availability kararı:

            StockReservationService

        tarafından verilir.
        """

        store_product_ids = sorted(
            {
                item.store_product_id
                for item in cart_items
            }
        )

        if not store_product_ids:
            return {}

        store_products = list(
            StoreProduct.objects
            .select_for_update(of=("self",))
            .select_related(
                "store",
                "variant",
                "variant__product",
            )
            .prefetch_related(
                "variant__attribute_values__attribute",
            )
            .filter(
                id__in=store_product_ids,
            )
            .order_by(
                "id",
            )
        )

        result = {
            store_product.id: store_product
            for store_product in store_products
        }

        if len(result) != len(store_product_ids):
            raise ProductUnavailableError(
                "Sepetteki ürünlerden biri artık mevcut değil."
            )

        return result

    # ======================================================================
    # PRODUCT VALIDATION
    # ======================================================================

    @staticmethod
    def _validate_cart_items(
        *,
        cart_items: Iterable[CartItem],
        store_products: dict[int, StoreProduct],
    ) -> None:
        """
        Kontroller:
        
            - StoreProduct mevcut mu?
            - Store aktif mi?
            - StoreProduct aktif mi?
            - Variant aktif mi?
            - Product aktif mi?
            - Quantity geçerli mi?
        
        NOT:
        
            Fiziksel veya rezerve edilebilir stok kontrolü
            burada yapılmaz.
        
            Gerçek stock availability ve reservation kararı:
        
                StockReservationService
        """

        for cart_item in cart_items:
            store_product = store_products.get(
                cart_item.store_product_id
            )

            if not store_product:
                raise ProductUnavailableError(
                    "Sepetteki ürün artık mevcut değil."
                )

            product = store_product.variant.product

            # ----------------------------------------------------------
            # Product availability
            # ----------------------------------------------------------

            if (
                store_product.status
                != StoreProductStatus.ACTIVE
                or not store_product.store.is_active
                or not store_product.variant.is_active
                or product.status != ProductStatus.ACTIVE
            ):
                raise ProductUnavailableError(
                    f"'{product.name}' "
                    "artık satın alınabilir durumda değil."
                )

            # ----------------------------------------------------------
            # Quantity validation
            # ----------------------------------------------------------

            if cart_item.quantity <= 0:
                raise CartItemSelectionError(
                    f"'{product.name}' için geçersiz ürün miktarı."
                )


    # ======================================================================
    # GROUPING
    # ======================================================================

    @staticmethod
    def _group_items_by_store(
        *,
        cart_items: Iterable[CartItem],
        store_products: dict[int, StoreProduct],
    ) -> dict[
        int,
        list[tuple[CartItem, StoreProduct]]
    ]:
        """
        CartItem'ları Store bazında gruplar.

        Örnek:

            Store A
                item 1
                item 2

            Store B
                item 3
        """

        grouped: dict[
            int,
            list[tuple[CartItem, StoreProduct]]
        ] = {}

        for cart_item in cart_items:
            store_product = store_products[
                cart_item.store_product_id
            ]

            grouped.setdefault(
                store_product.store_id,
                [],
            ).append(
                (cart_item, store_product)
            )

        return grouped

    # ======================================================================
    # TOTALS
    # ======================================================================

    @classmethod
    def _calculate_order_totals(
        cls,
        *,
        grouped_items: dict[
            int,
            list[tuple[CartItem, StoreProduct]]
        ],
    ) -> dict[str, Decimal]:
        """
        Sipariş finansal toplamlarını hesaplar.

        Mevcut kurallar:

            discount = 0
            tax = 0

        ShippingService:

            subtotal >= 750.00 TL
                -> ücretsiz kargo

            subtotal < 750.00 TL
                -> 99.99 TL kargo

        Toplam:

            subtotal
            - discount
            + shipping
            + tax
            = total
        """

        subtotal = Decimal("0.00")

        for items in grouped_items.values():
            for cart_item, store_product in items:
                line_total = cls._money(
                    store_product.price
                    * cart_item.quantity
                )

                subtotal += line_total

        subtotal = cls._money(subtotal)

        # ------------------------------------------------------------------
        # DISCOUNT
        # ------------------------------------------------------------------

        discount_amount = Decimal("0.00")

        # ------------------------------------------------------------------
        # SHIPPING
        # ------------------------------------------------------------------

        shipping_amount = ShippingService.calculate_shipping(
            subtotal=subtotal,
        )

        shipping_amount = cls._money(
            shipping_amount
        )

        # ------------------------------------------------------------------
        # TAX
        # ------------------------------------------------------------------

        tax_amount = Decimal("0.00")

        # ------------------------------------------------------------------
        # TOTAL
        # ------------------------------------------------------------------

        total_amount = cls._money(
            subtotal
            - discount_amount
            + shipping_amount
            + tax_amount
        )

        return {
            "subtotal": subtotal,
            "discount_amount": discount_amount,
            "shipping_amount": shipping_amount,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
        }

    # ======================================================================
    # SUBORDERS + ORDER ITEMS
    # ======================================================================

    @classmethod
    def _create_suborders(
        cls,
        *,
        order: Order,
        grouped_items: dict[
            int,
            list[tuple[CartItem, StoreProduct]]
        ],
    ) -> None:
        """
        Her Store için bir SubOrder oluşturur.

        Order
            ├── SubOrder A
            │     ├── OrderItem
            │     └── OrderItem
            │
            └── SubOrder B
                  └── OrderItem
        """

        # Store ID'leri deterministic sırada işlenir.
        for store_id in sorted(grouped_items):
            grouped_store_items = grouped_items[store_id]

            store = grouped_store_items[0][1].store

            subtotal = Decimal("0.00")

            line_data = []

            # ----------------------------------------------------------
            # Line calculations
            # ----------------------------------------------------------

            for cart_item, store_product in grouped_store_items:
                unit_price = cls._money(
                    store_product.price
                )

                line_total = cls._money(
                    unit_price * cart_item.quantity
                )

                (
                    variant_snapshot,
                    variant_display,
                ) = cls._build_variant_snapshot(
                    store_product=store_product,
                )

                line_data.append(
                    {
                        "cart_item": cart_item,
                        "store_product": store_product,
                        "unit_price": unit_price,
                        "total_amount": line_total,
                        "variant_snapshot": variant_snapshot,
                        "variant_display": variant_display,
                    }
                )

                subtotal += line_total

            subtotal = cls._money(subtotal)

            # ----------------------------------------------------------
            # SubOrder
            # ----------------------------------------------------------

            suborder = SubOrder.objects.create(
                order=order,
                store=store,
                store_name_snapshot=store.store_name,

                subtotal=subtotal,
                discount_amount=Decimal("0.00"),
                shipping_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=subtotal,

                status=SubOrderStatus.PENDING,
            )

            # ----------------------------------------------------------
            # Order Items
            # ----------------------------------------------------------

            for data in line_data:
                cart_item = data["cart_item"]
                store_product = data["store_product"]

                OrderItem.objects.create(
                    sub_order=suborder,
                    store_product=store_product,

                    product_name_snapshot=(
                        store_product.variant.product.name
                    ),

                    variant_snapshot=data["variant_snapshot"],
                    variant_display=data["variant_display"],

                    sku_snapshot=store_product.sku or "",
                    barcode_snapshot=(
                        store_product.variant.barcode or ""
                    ),

                    quantity=cart_item.quantity,

                    unit_price=data["unit_price"],
                    discount_amount=Decimal("0.00"),
                    tax_amount=Decimal("0.00"),
                    total_amount=data["total_amount"],
                )

    # ======================================================================
    # VARIANT SNAPSHOT
    # ======================================================================

    @staticmethod
    def _build_variant_snapshot(
        *,
        store_product: StoreProduct,
    ) -> tuple[dict, str]:
        """
        ProductVariant'ın sipariş anındaki özelliklerini snapshot olarak
        kaydeder.

        Örnek:

        {
            "variant_id": 15,
            "attributes": [
                {
                    "attribute_id": 2,
                    "attribute": "Renk",
                    "value_id": 8,
                    "value": "Kırmızı"
                },
                {
                    "attribute_id": 3,
                    "attribute": "Beden",
                    "value_id": 12,
                    "value": "L"
                }
            ]
        }
        """

        variant = store_product.variant

        attributes = []

        for attribute_value in variant.attribute_values.all():
            attributes.append(
                {
                    "attribute_id": attribute_value.attribute_id,
                    "attribute": attribute_value.attribute.name,
                    "value_id": attribute_value.pk,
                    "value": attribute_value.value,
                }
            )

        # Snapshot'ın sırası her zaman deterministic olsun.
        attributes.sort(
            key=lambda item: (
                item["attribute"],
                item["value"],
            )
        )

        snapshot = {
            "variant_id": variant.pk,
            "attributes": attributes,
        }

        variant_display = ", ".join(
            f'{item["attribute"]}: {item["value"]}'
            for item in attributes
        )

        return snapshot, variant_display

    # ======================================================================
    # ADDRESS
    # ======================================================================

    @staticmethod
    def _normalize_address(
        *,
        address: Address | AddressData,
        user,
    ) -> AddressData:
        """
        Address modeli geldiyse ownership kontrolü yapar.

        AddressData geldiyse guest checkout dahil doğrudan
        snapshot verisi olarak kabul edilir.
        """

        if isinstance(address, AddressData):
            return address

        if isinstance(address, Address):
            if not getattr(user, "is_authenticated", False):
                raise InvalidAddressError(
                    "Kayıtlı adres kullanmak için kullanıcı "
                    "doğrulanmalıdır."
                )

            if address.user_id != user.pk:
                raise InvalidAddressError(
                    "Bu adresi kullanma yetkiniz bulunmuyor."
                )

            return AddressData.from_model(address)

        raise InvalidAddressError(
            "Geçersiz adres verisi."
        )

    @staticmethod
    def _validate_address(
        address: AddressData,
    ) -> None:
        """
        Checkout adresinin minimum zorunlu alanlarını doğrular.
        """

        required_fields = {
            "full_name": address.full_name,
            "phone_number": address.phone_number,
            "address_line1": address.address_line1,
            "city": address.city,
            "state": address.state,
            "postal_code": address.postal_code,
        }

        for field_name, value in required_fields.items():
            if not value or not value.strip():
                raise InvalidAddressError(
                    f"Adres alanı zorunludur: {field_name}"
                )

    # ======================================================================
    # CUSTOMER CONTACT
    # ======================================================================

    @staticmethod
    def _normalize_email(
        email: str | None,
    ) -> str | None:
        if not email:
            return None

        email = email.strip().lower()

        return email or None

    @staticmethod
    def _normalize_phone(
        phone: str | None,
    ) -> str | None:
        if not phone:
            return None

        phone = phone.strip()

        return phone or None

    # ======================================================================
    # CURRENCY
    # ======================================================================

    @staticmethod
    def _normalize_currency(
        currency: str,
    ) -> str:
        """
        Currency değerini canonical forma getirir.

        Örnek:

            " try " -> "TRY"
            "TRY"   -> "TRY"
        """

        if not isinstance(currency, str):
            raise InvalidCurrencyError(
                "Currency string olmalıdır."
            )

        return currency.strip().upper()

    @classmethod
    def _validate_currency(
        cls,
        currency: str,
    ) -> None:
        """
        Currency'nin sistem tarafından desteklenip desteklenmediğini
        kontrol eder.
        """

        if not currency:
            raise InvalidCurrencyError(
                "Currency boş olamaz."
            )

        if currency not in cls.SUPPORTED_CURRENCIES:
            raise InvalidCurrencyError(
                f"Desteklenmeyen currency: {currency}"
            )

    # ======================================================================
    # MONEY
    # ======================================================================

    @staticmethod
    def _money(
        value: Decimal,
    ) -> Decimal:
        """
        Tüm parasal hesaplamalarda ortak rounding politikası.
        """

        return value.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    # ======================================================================
    # CUSTOMER ORDER ACCESS
    # ======================================================================

    @staticmethod
    def get_order_for_user(
        *,
        user,
        order_number: str,
    ) -> Order:
        """
        Müşteri yalnızca kendi Order'ını görebilir.

        Yetkisiz veya bulunamayan siparişte aynı exception döndürülür.
        Böylece order existence information leakage azaltılır.
        """

        order = (
            Order.objects
            .filter(
                order_number=order_number,
                user=user,
            )
            .prefetch_related(
                "sub_orders",
                "sub_orders__store",
                "sub_orders__items",
                "sub_orders__items__store_product",
            )
            .first()
        )

        if not order:
            raise OrderNotFoundError(
                "Sipariş bulunamadı."
            )

        return order

    # ======================================================================
    # SELLER SUBORDER ACCESS
    # ======================================================================

    @staticmethod
    def get_suborder_for_store(
        *,
        store,
        suborder_number: str,
    ) -> SubOrder:
        """
        Seller yalnızca kendi Store'una ait SubOrder'ı görebilir.

        Ownership boundary:
            SubOrder.store
        """

        suborder = (
            SubOrder.objects
            .select_related(
                "order",
                "store",
            )
            .prefetch_related(
                "items",
                "items__store_product",
                "items__store_product__variant",
                "items__store_product__variant__product",
            )
            .filter(
                suborder_number=suborder_number,
                store=store,
            )
            .first()
        )

        if not suborder:
            raise OrderNotFoundError(
                "Sipariş bulunamadı."
            )

        return suborder
