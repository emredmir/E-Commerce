from decimal import Decimal

from django.db import IntegrityError, transaction

from orders.models import OrderItem
from cart.models import Cart, CartItem
from products.models import (
    ProductStatus,
    StoreProduct,
    StoreProductStatus,
)
from orders.services.stock_reservation import StockReservationService


# ============================================================================
# EXCEPTIONS
# ============================================================================


class CartError(Exception):
    """Cart domain içerisindeki temel exception."""


class CartItemNotFoundError(CartError):
    """Sepet ürünü bulunamadı."""


class ProductUnavailableError(CartError):
    """Ürün artık satışa uygun değil."""


class InsufficientStockError(CartError):
    """Yeterli stok bulunmuyor."""


class InvalidQuantityError(CartError):
    """Geçersiz ürün miktarı."""


class InvalidSelectionError(CartError):
    """Geçersiz cart item selection değeri."""


class EmptyCartError(CartError):
    """Sepette işlem yapılacak ürün bulunmuyor."""


class CartOperationError(CartError):
    """Genel cart operation hatası."""


# ============================================================================
# CART SERVICE
# ============================================================================


class CartService:
    """
    Cart domain/business logic'inin tamamını yöneten service layer.

    Bu sınıf:

        - HTTP response üretmez.
        - Template render etmez.
        - HTTP 404 üretmez.
        - Django messages kullanmaz.
        - Business/domain exception fırlatır.
        - Checkout sırasında stok lock'lamayı destekler.

    GLOBAL LOCK STANDARDI
    =====================

    Stock-sensitive işlemlerde:

        Cart → StoreProduct → CartItem

    Birden fazla Cart varsa:

        Cart ID ASC

    Birden fazla StoreProduct varsa:

        StoreProduct ID ASC

    Stock gerektirmeyen CartItem işlemlerinde:

        Cart → CartItem

    ÖNEMLİ:

        lock_stock_for_checkout() gerçek checkout transaction'ı
        içerisinde çağrılmalıdır.

        validate_cart_for_checkout() sonucu final checkout garantisi
        değildir.

        Gerçek checkout transaction'ında final validation ve lock
        tekrar yapılmalıdır.
    """

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================

    @staticmethod
    def _get_session_key(request):
        """
        Guest kullanıcı için session key'i garanti eder.
        """

        if not request.session.session_key:
            request.session.create()

        return request.session.session_key

    @staticmethod
    def _parse_quantity(quantity):
        """
        Quantity değerini integer'a çevirir.

        update_item_quantity() içerisinde:

            quantity <= 0

        değeri ürün silme olarak kullanılır.
        """

        try:
            return int(quantity)

        except (TypeError, ValueError) as exc:
            raise InvalidQuantityError(
                "Miktar geçerli bir tam sayı olmalıdır."
            ) from exc

    @staticmethod
    def _get_locked_cart(cart):
        """
        Cart'ı row-level lock ile getirir.

        Transaction.atomic() içerisinde çağrılmalıdır.
        """

        try:
            return (
                Cart.objects
                .select_for_update()
                .get(
                    id=cart.id,
                )
            )

        except Cart.DoesNotExist as exc:
            raise CartOperationError(
                "Sepet artık mevcut değil."
            ) from exc

    @staticmethod
    def _get_locked_store_product(store_product_id):
        """
        StoreProduct'ı row-level lock ile getirir.

        Transaction.atomic() içerisinde çağrılmalıdır.
        """

        return (
            StoreProduct.objects
            .select_for_update()
            .select_related(
                "store",
                "variant",
                "variant__product",
            )
            .filter(
                id=store_product_id,
            )
            .first()
        )

    @staticmethod
    def _get_cart_item(cart, item_id):
        """
        CartItem'ı READ ONLY olarak getirir.

        Lock uygulanmaz.
        """

        cart_item = (
            CartItem.objects
            .select_related(
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                id=item_id,
                cart=cart,
            )
            .first()
        )

        if not cart_item:
            raise CartItemNotFoundError(
                "Sepet ürünü bulunamadı."
            )

        return cart_item

    @staticmethod
    def _get_locked_cart_item(cart, item_id):
        """
        CartItem'ı row-level lock ile getirir.

        Transaction.atomic() içerisinde çağrılmalıdır.
        """

        cart_item = (
            CartItem.objects
            .select_for_update()
            .select_related(
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                id=item_id,
                cart=cart,
            )
            .first()
        )

        if not cart_item:
            raise CartItemNotFoundError(
                "Sepet ürünü bulunamadı."
            )

        return cart_item

    @staticmethod
    def _is_store_product_active(store_product):
        """
        StoreProduct'ın hâlâ satışta olup olmadığını kontrol eder.
        """

        return (
            store_product.status == StoreProductStatus.ACTIVE
            and store_product.store.is_active
            and store_product.variant.is_active
            and store_product.variant.product.status
            == ProductStatus.ACTIVE
        )

    @staticmethod
    def _is_cart_item_selectable(store_product):
        """
        CartItem'ın kullanıcı tarafından seçilebilir olup olmadığını
        belirler.

        Bir ürün:

            - StoreProduct aktif değilse
            - Store aktif değilse
            - Variant aktif değilse
            - Product aktif değilse
            - fiziksel stok 0 ise

        seçilebilir değildir.

        ÖNEMLİ:
            Bu method DB mutation yapmaz.
        """

        if not store_product:
            return False

        if not CartService._is_store_product_active(
            store_product
        ):
            return False

        if store_product.stock <= 0:
            return False

        return True

    # ========================================================================
    # CART
    # ========================================================================

    @classmethod
    def get_or_create_cart(cls, request):
        """
        Authenticated kullanıcı:

            user cart

        Guest kullanıcı:

            session cart
        """

        if request.user.is_authenticated:
            cart, _ = Cart.objects.get_or_create(
                user=request.user,
            )

            return cart

        session_key = cls._get_session_key(request)

        cart, _ = Cart.objects.get_or_create(
            session_key=session_key,
        )

        return cart

    # ========================================================================
    # ADD TO CART
    # ========================================================================

    @classmethod
    @transaction.atomic
    def add_to_cart(
        cls,
        cart,
        store_product_id,
        quantity=1,
    ):
        """
        Ürünü sepete ekler.

        Yeni ürün:

            last_seen_price = current StoreProduct.price

        Mevcut ürün:

            quantity artırılır.
            last_seen_price değiştirilmez.

        Lock sırası:

            Cart → StoreProduct → CartItem
        """

        quantity = cls._parse_quantity(quantity)

        if quantity < 1:
            raise InvalidQuantityError(
                "Miktar en az 1 olmalıdır."
            )

        # --------------------------------------------------------------------
        # LOCK CART FIRST
        # --------------------------------------------------------------------

        cart = cls._get_locked_cart(cart)

        # --------------------------------------------------------------------
        # LOCK STORE PRODUCT SECOND
        # --------------------------------------------------------------------

        store_product = cls._get_locked_store_product(
            store_product_id
        )

        if not store_product:
            raise ProductUnavailableError(
                "Bu ürün artık mevcut değil."
            )

        if not cls._is_store_product_active(store_product):
            raise ProductUnavailableError(
                "Bu ürün artık satışta değil."
            )

        if store_product.stock < 1:
            raise InsufficientStockError(
                "Bu ürünün stoğu tükenmiş."
            )

        # --------------------------------------------------------------------
        # LOCK CART ITEM THIRD
        # --------------------------------------------------------------------

        cart_item = (
            CartItem.objects
            .select_for_update()
            .filter(
                cart=cart,
                store_product_id=store_product.id,
            )
            .first()
        )

        # --------------------------------------------------------------------
        # EXISTING ITEM
        # --------------------------------------------------------------------

        if cart_item:

            new_quantity = (
                cart_item.quantity + quantity
            )

            if new_quantity > store_product.stock:
                raise InsufficientStockError(
                    f"Sepette zaten {cart_item.quantity} adet var. "
                    f"Toplamda en fazla "
                    f"{store_product.stock} adet alabilirsiniz."
                )

            cart_item.quantity = new_quantity

            # last_seen_price bilinçli olarak değiştirilmez.

            cart_item.save(
                update_fields=[
                    "quantity",
                    "updated_at",
                ]
            )

            return cart_item

        # --------------------------------------------------------------------
        # NEW ITEM
        # --------------------------------------------------------------------

        if quantity > store_product.stock:
            raise InsufficientStockError(
                f"Bu üründen en fazla "
                f"{store_product.stock} adet alabilirsiniz."
            )

        try:
            cart_item = CartItem.objects.create(
                cart=cart,
                store_product=store_product,
                quantity=quantity,
                is_selected=True,
                last_seen_price=store_product.price,
            )

        except IntegrityError as exc:
            raise CartOperationError(
                "Ürün sepete eklenirken aynı anda başka bir işlem gerçekleşti. "
                "Lütfen tekrar deneyin."
            ) from exc

        return cart_item

    # ========================================================================
    # UPDATE QUANTITY
    # ========================================================================

    @classmethod
    @transaction.atomic
    def update_item_quantity(
        cls,
        cart,
        item_id,
        quantity,
    ):
        """
        Sepetteki ürün miktarını günceller.

        quantity <= 0:

            ürün silinir.

        quantity > 0:

            miktar güncellenir.

        Stock-sensitive lock sırası:

            Cart → StoreProduct → CartItem
        """

        quantity = cls._parse_quantity(quantity)

        # --------------------------------------------------------------------
        # LOCK CART FIRST
        # --------------------------------------------------------------------

        cart = cls._get_locked_cart(cart)

        # --------------------------------------------------------------------
        # REMOVE
        # --------------------------------------------------------------------

        if quantity <= 0:

            cart_item = cls._get_locked_cart_item(
                cart,
                item_id,
            )

            cart_item.delete()

            return None

        # --------------------------------------------------------------------
        # READ CART ITEM
        # --------------------------------------------------------------------

        cart_item = cls._get_cart_item(
            cart,
            item_id,
        )

        # --------------------------------------------------------------------
        # LOCK STORE PRODUCT SECOND
        # --------------------------------------------------------------------

        store_product = cls._get_locked_store_product(
            cart_item.store_product_id
        )

        if not store_product:
            raise ProductUnavailableError(
                "Bu ürün artık mevcut değil."
            )

        if not cls._is_store_product_active(store_product):
            raise ProductUnavailableError(
                "Bu ürün artık satışta değil."
            )

        if store_product.stock < 1:
            raise InsufficientStockError(
                "Bu ürünün stoğu tükenmiş."
            )

        if quantity > store_product.stock:
            raise InsufficientStockError(
                f"En fazla {store_product.stock} adet alabilirsiniz."
            )

        # --------------------------------------------------------------------
        # LOCK CART ITEM THIRD
        # --------------------------------------------------------------------

        cart_item = cls._get_locked_cart_item(
            cart,
            item_id,
        )

        # --------------------------------------------------------------------
        # FINAL CONSISTENCY CHECK
        # --------------------------------------------------------------------

        if cart_item.store_product_id != store_product.id:
            raise CartOperationError(
                "Sepet ürünü güncellenirken beklenmeyen bir durum oluştu. "
                "Lütfen tekrar deneyin."
            )

        cart_item.quantity = quantity

        # last_seen_price değiştirilmez.

        cart_item.save(
            update_fields=[
                "quantity",
                "updated_at",
            ]
        )

        return cart_item

    # ========================================================================
    # REMOVE
    # ========================================================================

    @classmethod
    @transaction.atomic
    def remove_item(cls, cart, item_id):
        """
        Ürünü sepetten tamamen kaldırır.

        Stock-sensitive olmadığı için:

            Cart → CartItem
        """

        cart = cls._get_locked_cart(cart)

        cart_item = cls._get_locked_cart_item(
            cart,
            item_id,
        )

        cart_item.delete()

    # ========================================================================
    # SELECTION
    # ========================================================================

    @classmethod
    @transaction.atomic
    def toggle_item_selection(
        cls,
        cart,
        item_id,
        is_selected,
    ):
        """
        Checkout için CartItem selection state'ini değiştirir.

        Kullanıcı bir ürünü seçmek istediğinde ürün:

            - aktif olmalı
            - mağaza aktif olmalı
            - variant aktif olmalı
            - product aktif olmalı
            - stok > 0 olmalı

        olmalıdır.
        """

        if not isinstance(is_selected, bool):
            raise InvalidSelectionError(
                "is_selected boolean olmalıdır."
            )

        cart = cls._get_locked_cart(cart)

        cart_item = cls._get_locked_cart_item(
            cart,
            item_id,
        )

        store_product = cart_item.store_product

        # --------------------------------------------------------------
        # SELECT
        # --------------------------------------------------------------

        if is_selected:
            if not cls._is_cart_item_selectable(
                store_product
            ):
                if store_product.stock <= 0:
                    raise InsufficientStockError(
                        "Bu ürünün stoğu tükenmiş."
                    )

                raise ProductUnavailableError(
                    "Bu ürün artık satın alınabilir durumda değil."
                )

        # --------------------------------------------------------------
        # SAVE
        # --------------------------------------------------------------

        cart_item.is_selected = is_selected

        cart_item.save(
            update_fields=[
                "is_selected",
                "updated_at",
            ]
        )

        return cart_item

    # ========================================================================
    # MERGE GUEST CART
    # ========================================================================

    @classmethod
    @transaction.atomic
    def merge_guest_cart(cls, request, user):
        """
        Guest cart'ı authenticated user's cart'ı ile birleştirir.

        GLOBAL LOCK SIRASI:

            Cart ID ASC
                ↓
            StoreProduct ID ASC
                ↓
            CartItem

        Akış:

            1. Guest Cart READ ONLY bulunur.
            2. User Cart get/create edilir.
            3. Guest + User Cart ID'leri ASC sıralanır.
            4. Cart'lar ID ASC şeklinde lock'lanır.
            5. Guest CartItem'lar READ ONLY okunur.
            6. Gerekli StoreProduct'ların tamamı ID ASC lock'lanır.
            7. Guest CartItem'ların tamamı lock'lanır.
            8. User CartItem'ların tamamı lock'lanır.
            9. Merge gerçekleştirilir.

        Guest Cart ilk bulunurken lock'lanmaz.

        Asıl Cart lock acquisition işlemi deterministic olarak
        Cart ID ASC sırasıyla gerçekleştirilir.
        """

        session_key = request.session.session_key

        if not session_key:
            return None

        # --------------------------------------------------------------------
        # FIND GUEST CART WITHOUT LOCK
        # --------------------------------------------------------------------

        guest_cart = (
            Cart.objects
            .filter(
                session_key=session_key,
                user__isnull=True,
            )
            .first()
        )

        if not guest_cart:
            return None

        # --------------------------------------------------------------------
        # GET / CREATE USER CART
        # --------------------------------------------------------------------

        user_cart, _ = Cart.objects.get_or_create(
            user=user,
        )

        # --------------------------------------------------------------------
        # LOCK BOTH CARTS IN DETERMINISTIC ID ORDER
        # --------------------------------------------------------------------

        cart_ids = sorted({
            guest_cart.id,
            user_cart.id,
        })

        locked_carts = {
            cart.id: cart
            for cart in (
                Cart.objects
                .select_for_update()
                .filter(
                    id__in=cart_ids,
                )
                .order_by("id")
            )
        }

        locked_guest_cart = locked_carts.get(
            guest_cart.id
        )

        locked_user_cart = locked_carts.get(
            user_cart.id
        )

        if not locked_guest_cart or not locked_user_cart:
            raise CartOperationError(
                "Sepetler kilitlenirken beklenmeyen bir durum oluştu."
            )

        guest_cart = locked_guest_cart
        user_cart = locked_user_cart

        # --------------------------------------------------------------------
        # READ GUEST ITEMS WITHOUT LOCK
        # --------------------------------------------------------------------

        guest_items = list(
            CartItem.objects
            .select_related(
                "store_product",
            )
            .filter(
                cart=guest_cart,
            )
            .order_by(
                "store_product_id",
                "id",
            )
        )

        if not guest_items:
            guest_cart.delete()

            return user_cart

        # --------------------------------------------------------------------
        # COLLECT STORE PRODUCT IDS
        # --------------------------------------------------------------------

        store_product_ids = sorted({
            item.store_product_id
            for item in guest_items
        })

        # --------------------------------------------------------------------
        # LOCK ALL STORE PRODUCTS SECOND
        # --------------------------------------------------------------------

        locked_products = {
            product.id: product
            for product in (
                StoreProduct.objects
                .select_for_update()
                .select_related(
                    "store",
                    "variant",
                    "variant__product",
                )
                .filter(
                    id__in=store_product_ids,
                )
                .order_by("id")
            )
        }

        # --------------------------------------------------------------------
        # LOCK ALL GUEST CART ITEMS THIRD
        # --------------------------------------------------------------------

        guest_item_ids = [
            item.id
            for item in guest_items
        ]

        locked_guest_items = {
            item.id: item
            for item in (
                CartItem.objects
                .select_for_update()
                .filter(
                    id__in=guest_item_ids,
                    cart=guest_cart,
                )
                .order_by(
                    "store_product_id",
                    "id",
                )
            )
        }

        # --------------------------------------------------------------------
        # LOCK ALL USER CART ITEMS FOURTH
        # --------------------------------------------------------------------

        user_items = {
            item.store_product_id: item
            for item in (
                CartItem.objects
                .select_for_update()
                .filter(
                    cart=user_cart,
                    store_product_id__in=store_product_ids,
                )
                .order_by(
                    "store_product_id",
                    "id",
                )
            )
        }

        # --------------------------------------------------------------------
        # PROCESS ITEMS
        # --------------------------------------------------------------------

        for guest_item in guest_items:

            guest_item = locked_guest_items.get(
                guest_item.id
            )

            if not guest_item:
                continue

            store_product = locked_products.get(
                guest_item.store_product_id
            )

            # ---------------------------------------------------------------
            # PRODUCT DELETED
            # ---------------------------------------------------------------

            if not store_product:
                guest_item.delete()
                continue

            # ---------------------------------------------------------------
            # EXISTING USER ITEM
            # ---------------------------------------------------------------

            user_item = user_items.get(
                store_product.id
            )

            if user_item:

                new_quantity = (
                    user_item.quantity
                    + guest_item.quantity
                )

                user_item.quantity = min(
                    new_quantity,
                    max(store_product.stock, 0),
                )

                user_item.save(
                    update_fields=[
                        "quantity",
                        "updated_at",
                    ]
                )

                guest_item.delete()

                continue

            # ---------------------------------------------------------------
            # NEW USER ITEM
            # ---------------------------------------------------------------

            if (
                not cls._is_store_product_active(store_product)
                or store_product.stock <= 0
            ):
                guest_item.delete()
                continue

            guest_item.quantity = min(
                guest_item.quantity,
                store_product.stock,
            )

            guest_item.cart = user_cart

            guest_item.save(
                update_fields=[
                    "cart",
                    "quantity",
                    "updated_at",
                ]
            )

            user_items[store_product.id] = guest_item

        # --------------------------------------------------------------------
        # DELETE EMPTY GUEST CART
        # --------------------------------------------------------------------

        guest_cart.delete()

        return user_cart

    # ========================================================================
    # PRICE CHANGES
    # ========================================================================

    @classmethod
    def get_price_changes(cls, cart):
        """
        Kullanıcının henüz görmediği fiyat değişikliklerini döndürür.

        READ ONLY'dir.
        """

        items = (
            cart.items
            .select_related(
                "store_product",
                "store_product__variant",
                "store_product__variant__product",
            )
            .all()
        )

        changes = []

        for item in items:

            if not item.price_changed:
                continue

            changes.append({
                "item_id": item.id,
                "product_name": (
                    item.store_product
                    .variant
                    .product
                    .name
                ),
                "old_price": item.previous_price,
                "new_price": item.unit_price,
            })

        return changes

    # ========================================================================
    # MARK PRICE CHANGES AS SEEN
    # ========================================================================

    @classmethod
    @transaction.atomic
    def mark_price_changes_as_seen(cls, cart):
        """
        Kullanıcının gördüğü fiyat değişikliklerini current price ile
        eşitler.

        Bu işlemden sonra:

            item.price_changed == False

        olur.

        Lock sırası:

            Cart → CartItem
        """

        # --------------------------------------------------------------------
        # LOCK CART FIRST
        # --------------------------------------------------------------------

        cart = cls._get_locked_cart(cart)

        # --------------------------------------------------------------------
        # LOCK CART ITEMS SECOND
        # --------------------------------------------------------------------

        items = list(
            cart.items
            .select_for_update()
            .select_related(
                "store_product",
            )
            .all()
        )

        changed_items = []

        for item in items:

            if not item.price_changed:
                continue

            item.last_seen_price = item.unit_price

            changed_items.append(item)

        if changed_items:
            CartItem.objects.bulk_update(
                changed_items,
                fields=[
                    "last_seen_price",
                    "updated_at",
                ],
            )

        return changed_items

    # ========================================================================
    # CHECKOUT PRE-VALIDATION
    # ========================================================================

    @classmethod
    @transaction.atomic
    def validate_cart_for_checkout(cls, cart):
        """
        Checkout öncesi selected CartItem'ları kontrol eder.

        Kontroller:
            - StoreProduct mevcut mu?
            - StoreProduct aktif mi?
            - Store aktif mi?
            - Variant aktif mi?
            - Product aktif mi?
            - O anki alınabilir stok yeterli mi?
            - Price değişmiş mi?

        ÖNEMLİ:

            Bu method CartItem.quantity değerini değiştirmez.

            Çünkü CartItem müşterinin satın almak istediği miktarı temsil eder.
            Stok üzerindeki geçici kontrol StockReservation üzerinden yapılır.

            Final checkout garantisi değildir.

            Gerçek checkout transaction'ında:
                lock_stock_for_checkout()
            tekrar çağrılmalıdır.
        """

        # --------------------------------------------------------------------
        # LOCK CART
        # --------------------------------------------------------------------

        cart = cls._get_locked_cart(cart)

        # --------------------------------------------------------------------
        # SELECTED ITEM IDS
        # --------------------------------------------------------------------

        selected_item_data = list(
            CartItem.objects
            .filter(
                cart=cart,
                is_selected=True,
            )
            .order_by(
                "store_product_id",
                "id",
            )
            .values(
                "id",
                "store_product_id",
            )
        )

        if not selected_item_data:
            raise EmptyCartError(
                "Checkout için seçili ürün bulunmuyor."
            )

        selected_item_ids = [
            item["id"]
            for item in selected_item_data
        ]

        store_product_ids = sorted({
            item["store_product_id"]
            for item in selected_item_data
        })

        # --------------------------------------------------------------------
        # LOCK STORE PRODUCTS
        # --------------------------------------------------------------------

        locked_products = {
            product.id: product
            for product in (
                StoreProduct.objects
                .select_for_update()
                .select_related(
                    "store",
                    "variant",
                    "variant__product",
                )
                .filter(
                    id__in=store_product_ids,
                )
                .order_by("id")
            )
        }

        # --------------------------------------------------------------------
        # LOCK CART ITEMS
        # --------------------------------------------------------------------

        selected_items = list(
            CartItem.objects
            .select_for_update()
            .select_related(
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                id__in=selected_item_ids,
                cart=cart,
                is_selected=True,
            )
            .order_by(
                "store_product_id",
                "id",
            )
        )

        if not selected_items:
            raise EmptyCartError(
                "Checkout için seçili ürün bulunmuyor."
            )

        warnings = []
        price_changes = []
        is_valid = True

        # --------------------------------------------------------------------
        # VALIDATION
        # --------------------------------------------------------------------

        for item in selected_items:

            store_product = locked_products.get(
                item.store_product_id
            )

            # ---------------------------------------------------------------
            # STORE PRODUCT NOT FOUND
            # ---------------------------------------------------------------

            if not store_product:

                item.is_selected = False

                item.save(
                    update_fields=[
                        "is_selected",
                        "updated_at",
                    ]
                )

                warnings.append({
                    "code": "PRODUCT_UNAVAILABLE",
                    "item_id": item.id,
                    "product_name": "Ürün",
                    "message": (
                        "Sepetteki ürünlerden biri artık mevcut değil."
                    ),
                })

                is_valid = False
                continue

            variant = store_product.variant
            product = variant.product
            product_name = product.name

            # ---------------------------------------------------------------
            # PRODUCT ACTIVE
            # ---------------------------------------------------------------

            if not cls._is_store_product_active(store_product):

                item.is_selected = False

                item.save(
                    update_fields=[
                        "is_selected",
                        "updated_at",
                    ]
                )

                warnings.append({
                    "code": "PRODUCT_UNAVAILABLE",
                    "item_id": item.id,
                    "product_name": product_name,
                    "message": (
                        f"'{product_name}' artık satışta değil."
                    ),
                })

                is_valid = False
                continue

            # ---------------------------------------------------------------
            # AVAILABLE STOCK
            # ---------------------------------------------------------------

            available_stock = (
                StockReservationService.get_available_stock(
                    store_product=store_product,
                )
            )

            # ---------------------------------------------------------------
            # OUT OF STOCK
            # ---------------------------------------------------------------

            if available_stock <= 0:

                item.is_selected = False

                item.save(
                    update_fields=[
                        "is_selected",
                        "updated_at",
                    ]
                )

                warnings.append({
                    "code": "OUT_OF_STOCK",
                    "item_id": item.id,
                    "product_name": product_name,
                    "requested_quantity": item.quantity,
                    "available_quantity": 0,
                    "message": (
                        f"'{product_name}' şu anda satın alınabilir "
                        "stokta bulunmuyor."
                    ),
                })

                is_valid = False
                continue

            # ---------------------------------------------------------------
            # INSUFFICIENT AVAILABLE STOCK
            # ---------------------------------------------------------------

            if item.quantity > available_stock:

                warnings.append({
                    "code": "INSUFFICIENT_STOCK",
                    "item_id": item.id,
                    "product_name": product_name,
                    "requested_quantity": item.quantity,
                    "available_quantity": available_stock,
                    "message": (
                        f"'{product_name}' için yeterli "
                        f"satın alınabilir stok bulunmuyor. "
                        f"Mevcut alınabilir stok: "
                        f"{available_stock}, "
                        f"istenen: {item.quantity}."
                    ),
                })

                is_valid = False

            # ---------------------------------------------------------------
            # PRICE CHANGE
            # ---------------------------------------------------------------

            if item.price_changed:

                price_changes.append({
                    "code": "PRICE_CHANGED",
                    "item_id": item.id,
                    "product_name": product_name,
                    "old_price": item.previous_price,
                    "new_price": item.unit_price,
                })

        return {
            "is_valid": is_valid,
            "warnings": warnings,
            "price_changes": price_changes,
        }

    # ========================================================================
    # CART SUMMARY
    # ========================================================================

    @classmethod
    def get_cart_summary(cls, cart):
        """
        API response'ları için temel cart özetini döndürür.

        READ ONLY'dir.
        """

        if not cart:
            return {
                "total_items": 0,
                "selected_items_count": 0,
                "total_price": Decimal("0.00"),
            }

        context = cls.get_cart_context_data(
            cart
        )

        return {
            "total_items": context["total_items"],
            "selected_items_count": (
                context["selected_items_count"]
            ),
            "total_price": context["total_price"],
        }

    # ========================================================================
    # CART CONTEXT
    # ========================================================================

    @classmethod
    def get_cart_context_data(cls, cart):
        """
        Cart sayfası için gerekli veriyi hazırlar.

        READ ONLY'dir.

        DB mutation yapmaz.

        Fiyat değişikliklerini:

            price_changes

        içerisinde döndürür.
        """

        if not cart:
            return {
                "cart": None,
                "total_price": Decimal("0.00"),
                "total_items": 0,
                "selected_items_count": 0,
                "grouped_items": [],
                "price_changes": [],
            }

        items = list(
            cart.items
            .select_related(
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
                "store_product__variant__product__brand",
            )
            .prefetch_related(
                "store_product__variant__attribute_values__attribute",
                "store_product__variant__product__image_groups__images",
                "store_product__variant__product__image_groups__visual_attribute_values",
            )
            .order_by(
                "store_product__store__store_name",
                "-added_at",
            )
        )

        grouped_data = {}
        price_changes = []
        effective_selected_items_count = 0
        effective_total_price = Decimal("0.00")

        for item in items:

            # ----------------------------------------------------------
            # EFFECTIVE SELECTION
            # ----------------------------------------------------------
            #
            # DB'deki is_selected eski durumda True kalmış olabilir.
            #
            # Ancak ürün artık satın alınabilir değilse kullanıcıya
            # selected olarak gösterilmemeli ve toplam fiyata
            # dahil edilmemeli.
            #
            # Burada DB mutation yapılmaz.
            #

            effective_selected = item.is_selected

            if effective_selected:
                effective_selected = cls._is_cart_item_selectable(
                    item.store_product
                )

            # Template mevcut item.is_selected alanını kullanıyorsa
            # render sürecinde de doğru görünmesi için instance
            # üzerindeki değeri yalnızca memory'de güncelliyoruz.
            #
            # DB'ye save yapılmadığı için GET request mutation yapmaz.

            item.is_selected = effective_selected

            store = item.store_product.store

            if store.id not in grouped_data:
                grouped_data[store.id] = {
                    "store": store,
                    "items": [],
                    "store_total_price": Decimal("0.00"),
                    "has_price_change": False,
                }

            # ----------------------------------------------------------------
            # PRICE CHANGE
            # ----------------------------------------------------------------

            if item.price_changed:

                grouped_data[store.id][
                    "has_price_change"
                ] = True

                price_changes.append({
                    "item_id": item.id,
                    "product_name": (
                        item.store_product
                        .variant
                        .product
                        .name
                    ),
                    "old_price": item.previous_price,
                    "new_price": item.unit_price,
                })

            grouped_data[store.id]["items"].append(item)

            if effective_selected:
                line_total = item.total_price

                grouped_data[store.id][
                    "store_total_price"
                ] += line_total

                effective_selected_items_count += (
                    item.quantity
                )

                effective_total_price += line_total

        return {
            "cart": cart,
            "total_price": effective_total_price,
            "total_items": cart.total_items_count,
            "selected_items_count": effective_selected_items_count,
            "grouped_items": list(
                grouped_data.values()
            ),
            "price_changes": price_changes,
        }


    @classmethod
    @transaction.atomic
    def clear_order_items(cls, *, order):
        """
        Başarılı ödeme sonrası yalnızca checkout sırasında
        satın alınan ve checkout'tan sonra değiştirilmemiş
        CartItem kayıtlarını temizler.
    
        Güvenlik kuralı:
    
            source_cart_item_updated_at
                ==
            current CartItem.updated_at
    
        ise CartItem checkout snapshot'ı ile hâlâ aynıdır
        ve silinebilir.
    
        Değişmişse dokunulmaz.
    
        Return:
            int -> silinen CartItem sayısı
        """
    
        order_items = (
            OrderItem.objects
            .select_related("source_cart_item")
            .filter(
                sub_order__order_id=order.pk,
                source_cart_item__isnull=False,
            )
            .order_by(
                "source_cart_item_id",
                "id",
            )
        )
    
        deleted_count = 0
    
        for order_item in order_items:
            cart_item = order_item.source_cart_item
    
            if cart_item is None:
                continue
            
            # Checkout sırasında alınan snapshot yoksa
            # güvenli tarafta kalıp silme.
            if order_item.source_cart_item_updated_at is None:
                continue
            
            # CartItem checkout'tan sonra değiştirilmiş.
            if (
                cart_item.updated_at
                != order_item.source_cart_item_updated_at
            ):
                continue
            
            cart_item.delete()
            deleted_count += 1
    
        return deleted_count

    # ========================================================================
    # LOCK STOCK FOR CHECKOUT
    # ========================================================================

    @classmethod
    def lock_stock_for_checkout(cls, cart):
        """
        GERÇEK CHECKOUT TRANSACTION'I içerisinde çağrılmalıdır.

        Örnek:

            with transaction.atomic():
                result = CartService.lock_stock_for_checkout(cart)

                # Order oluştur
                # OrderItem oluştur
                # Stock düş
                # Cart temizle

        BU METHOD KENDİ TRANSACTION'INI AÇMAZ.

        Lock sırası:

            Cart → StoreProduct → CartItem

        Birden fazla StoreProduct:

            ID ASC

        Bu method:

            - stock düşmez
            - order oluşturmaz
            - ödeme işlemi yapmaz

        Sadece:

            - Cart lock
            - StoreProduct lock
            - CartItem lock
            - final validation

        yapar.

        Döndürülen Cart, StoreProduct ve CartItem instance'ları
        aynı checkout transaction'ı içerisinde kullanılmalıdır.
        """

        # --------------------------------------------------------------------
        # LOCK CART FIRST
        # --------------------------------------------------------------------

        cart = cls._get_locked_cart(cart)

        # --------------------------------------------------------------------
        # GET SELECTED ITEM DATA
        # --------------------------------------------------------------------

        selected_item_data = list(
            CartItem.objects
            .filter(
                cart=cart,
                is_selected=True,
            )
            .order_by(
                "store_product_id",
                "id",
            )
            .values(
                "id",
                "store_product_id",
            )
        )

        if not selected_item_data:
            raise EmptyCartError(
                "Checkout için seçili ürün bulunmuyor."
            )

        selected_item_ids = [
            item["id"]
            for item in selected_item_data
        ]

        store_product_ids = sorted({
            item["store_product_id"]
            for item in selected_item_data
        })

        # --------------------------------------------------------------------
        # LOCK ALL STORE PRODUCTS SECOND
        # --------------------------------------------------------------------

        locked_products = {
            product.id: product
            for product in (
                StoreProduct.objects
                .select_for_update()
                .select_related(
                    "store",
                    "variant",
                    "variant__product",
                )
                .filter(
                    id__in=store_product_ids,
                )
                .order_by("id")
            )
        }

        # --------------------------------------------------------------------
        # LOCK ALL CART ITEMS THIRD
        # --------------------------------------------------------------------

        selected_items = list(
            CartItem.objects
            .select_for_update()
            .select_related(
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                id__in=selected_item_ids,
                cart=cart,
                is_selected=True,
            )
            .order_by(
                "store_product_id",
                "id",
            )
        )

        if not selected_items:
            raise EmptyCartError(
                "Checkout için seçili ürün bulunmuyor."
            )

        # --------------------------------------------------------------------
        # FINAL VALIDATION
        # --------------------------------------------------------------------

        for item in selected_items:

            store_product = locked_products.get(
                item.store_product_id
            )

            if not store_product:
                raise ProductUnavailableError(
                    "Sepetteki ürünlerden biri artık mevcut değil."
                )

            variant = store_product.variant
            product = variant.product
            product_name = product.name

            # ----------------------------------------------------------------
            # ACTIVE
            # ----------------------------------------------------------------

            if not cls._is_store_product_active(store_product):
                raise ProductUnavailableError(
                    f"'{product_name}' artık satışta değil."
                )

            # ----------------------------------------------------------------
            # STOCK
            # ----------------------------------------------------------------

            available_stock = (
                StockReservationService.get_available_stock(
                    store_product=store_product,
                )
            )

            if available_stock < item.quantity:
                raise InsufficientStockError(
                    f"'{product_name}' için yeterli stok bulunmuyor. "
                    f"Mevcut alınabilir stok: {available_stock}, "
                    f"istenen: {item.quantity}"
                )

        return {
            "items": selected_items,
            "store_products": locked_products,
            "cart": cart,
        }
