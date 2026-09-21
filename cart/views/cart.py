import json
import logging

from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from django.db.models import (
    Prefetch,
    OuterRef,
    Subquery,
    DecimalField,
    Case,
    When,
    Value,
    IntegerField,
)

from cart.services.cart import (
    CartError,
    CartItemNotFoundError,
    CartService,
    EmptyCartError,
    InsufficientStockError,
    InvalidQuantityError,
    ProductUnavailableError,
)

from products.models import Product, ProductStatus, ProductCollectionItem, ProductImage, ProductImageGroup, StoreProduct, StoreProductStatus
from products.services.storefront_offers import StorefrontOfferService

from django.db.models.functions import Coalesce

logger = logging.getLogger(__name__)


# ==============================================================================
# CART DETAIL
# ==============================================================================

# TODO: ileride viewi servise taşı. viewi olabildiğince sadeleştir. son düzenlemelerde yorum satırları bozulmuş olabilir düzenle.
class CartDetailView(TemplateView):
    """
    Cart sayfasını render eder.

    Guest kullanıcılar da erişebilir.

    GET request'i:
        - Cart'ı getirir/oluşturur.
        - Cart context'ini hazırlar.
        - Price changes'ı otomatik olarak seen yapmaz.

    DB mutation:
        - Cart yoksa get_or_create_cart() nedeniyle Cart oluşturulabilir.
        - Price change state değiştirilmez.
    """

    template_name = "cart/cart_detail.html"

    @method_decorator(ensure_csrf_cookie)
    def get(self, request, *args, **kwargs):
        return super().get(
            request,
            *args,
            **kwargs,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # --------------------------------------------------------------
        # 1. Sepet
        # --------------------------------------------------------------

        cart = CartService.get_or_create_cart(self.request)

        context.update(
            CartService.get_cart_context_data(cart)
        )

        if context.get("price_changes"):
            CartService.mark_price_changes_as_seen(cart)

        # --------------------------------------------------------------
        # 2. Sepetteki ürün / kategori ID'leri
        # --------------------------------------------------------------

        cart_product_ids = set()
        cart_category_ids = set()

        if cart:
            cart_product_ids = set(
                cart.items.values_list(
                    "store_product__variant__product_id",
                    flat=True,
                )
            )

            cart_category_ids = set(
                cart.items
                .filter(
                    store_product__variant__product__category_id__isnull=False
                )
                .values_list(
                    "store_product__variant__product__category_id",
                    flat=True,
                )
            )

        # --------------------------------------------------------------
        # 3. Ortak Product QuerySet
        # --------------------------------------------------------------
        #
        # Recommendation'lar Product seviyesinde tutuluyor.
        #
        # Favoriler:
        #
        #   ProductCollectionItem
        #       -> variant
        #           -> product
        #
        # Aynı Product'ın farklı variantları favorilenebilir.
        # Bu nedenle favoriler aşağıda Product ID bazında
        # duplicate'lerden arındırılıyor.
        #
        # offer (StoreProduct), favorinin ana referansı değildir.
        # Favorilendiği andaki teklif bilgisidir ve nullable'dır.
        #
        # Image yapısı:
        #
        #   Product
        #       └── image_groups
        #               └── images
        #
        # Image group sırası:
        #   1. sort_order
        #   2. id
        #
        # Image sırası:
        #   1. is_main=True
        #   2. sort_order
        #   3. id
        #
        # Böylece her group'un ilk image'ı thumbnail adayıdır.
        # --------------------------------------------------------------

        image_qs = (
            ProductImage.objects
            .order_by(
                "-is_main",
                "sort_order",
                "id",
            )
        )

        image_group_qs = (
            ProductImageGroup.objects
            .filter(is_active=True)
            .order_by(
                "sort_order",
                "id",
            )
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=image_qs,
                    to_attr="prefetched_images",
                )
            )
        )

        #!!!!
        # buybox_price_subquery = (
        #     StoreProduct.objects
        #     .purchasable()
        #     .filter(
        #         variant__product=OuterRef("pk"),
        #     )
        #     .annotate(
        #         owner_priority=Case(
        #             When(
        #                 store_id=OuterRef("created_by_store_id"),
        #                 then=Value(0),
        #             ),
        #             default=Value(1),
        #             output_field=IntegerField(),
        #         )
        #     )
        #     .order_by(
        #         "owner_priority",
        #         "created_at",
        #         "id",
        #     )
        #     .values("price")[:1]
        # )

        default_variant_price_sq = (
            StorefrontOfferService
            .get_product_buybox_subquery(
                use_default_variant=True
            )
        )

        fallback_price_sq = (
            StorefrontOfferService
            .get_product_buybox_subquery(
                use_default_variant=False
            )
        )

        # owner_price_subquery = (
        #     StoreProduct.objects
        #     .purchasable()
        #     .filter(
        #         variant__product=OuterRef("pk"),
        #         variant_id=OuterRef("default_variant_id"),
        #         store_id=OuterRef("created_by_store_id"),
        #     )
        #     .order_by(
        #         "created_at",
        #         "id",
        #     )
        #     .values("price")[:1]
        # )

        base_qs = (
            Product.objects
            .filter(
                status=ProductStatus.ACTIVE,
                variants__is_active=True,
            )
            .exclude(
                id__in=cart_product_ids
            )
            .distinct()
            .select_related("brand")
            #!!!
            .annotate(
                buybox_price=Coalesce(
                    default_variant_price_sq,
                    fallback_price_sq,
                )
            )

            .prefetch_related(
                Prefetch(
                    "image_groups",
                    queryset=image_group_qs,
                    to_attr="prefetched_image_groups",
                )
            )
        )

        # --------------------------------------------------------------
        # 4. Thumbnail helper
        # --------------------------------------------------------------

        def attach_thumbnails(products):
            for product in products:
                product.thumbnail_url = None

                for group in product.prefetched_image_groups:
                    if group.prefetched_images:
                        product.thumbnail_url = (
                            group.prefetched_images[0].image.url
                        )
                        break

            return products

        # --------------------------------------------------------------
        # 5. Favoriler
        # --------------------------------------------------------------
        #
        # Aynı Product'ın farklı variantları favorilenebilir.
        #
        # Örneğin:
        #
        #   Variant A -> Product 1 -> 10 Eylül
        #   Variant B -> Product 1 -> 12 Eylül
        #
        # Product 1 sadece bir kez gösterilir.
        #
        # Favoriler önce added_at'e göre sıralanır.
        # Product ID'leri çıkarıldıktan sonra Product queryset'i
        # IN sorgusu kullandığı için DB sırasına güvenilmez.
        #
        # Bu nedenle Product'lar alındıktan sonra fav_product_ids
        # sırasına göre tekrar sıralanır.
        # --------------------------------------------------------------

        favorite_products = []

        if self.request.user.is_authenticated:

            favorite_items = (
                ProductCollectionItem.objects
                .filter(
                    collection__user=self.request.user,
                    variant__product__status=ProductStatus.ACTIVE,
                )
                .exclude(
                    variant__product_id__in=cart_product_ids
                )
                .select_related(
                    "variant",
                    "variant__product",
                    "offer",
                    "offer__store",
                )
                .order_by(
                    "-added_at",
                    "-id",
                )
            )

        #     fav_product_ids = []
            

        #     for item in favorite_items:
        #         product_id = item.variant.product_id

        #         if product_id not in fav_product_ids:
        #             fav_product_ids.append(product_id)

        #         if len(fav_product_ids) == 10:
        #             break

        #     if fav_product_ids:
        #         products = attach_thumbnails(
        #             list(
        #                 base_qs.filter(
        #                     id__in=fav_product_ids
        #                 )
        #             )
        #         )

        #         product_map = {
        #             product.id: product
        #             for product in products
        #         }

        #         favorite_products = [
        #             product_map[product_id]
        #             for product_id in fav_product_ids
        #             if product_id in product_map
        #         ]

        # context["favorite_products"] = favorite_products

            favorite_items_data = []

            seen_product_ids = set()

            for item in favorite_items:
            
                product = item.variant.product

                # Aynı Product farklı varyantlarla favorilenmiş olabilir.
                # Cart'ta aynı Product'ı yalnızca bir kere göster.
                if product.id in seen_product_ids:
                    continue
                
                seen_product_ids.add(product.id)

                favorite_items_data.append(item)

                if len(favorite_items_data) == 10:
                    break

            if favorite_items_data:
                favorite_product_ids = [
                    item.variant.product_id
                    for item in favorite_items_data
                ]

                # favorite_base_products = list(
                #     base_qs.filter(
                #         id__in=favorite_product_ids,
                #     )
                # )

                # favorite_product_map = {
                #     product.id: product
                #     for product in favorite_base_products
                # }

                favorite_base_products = attach_thumbnails(
                    list(
                        base_qs.filter(
                            id__in=favorite_product_ids,
                        )
                    )
                )

                favorite_product_map = {
                    product.id: product
                    for product in favorite_base_products
                }

                for item in favorite_items_data:
                
                    product = favorite_product_map.get(
                        item.variant.product_id
                    )

                    if not product:
                        continue
                    
                    product.favorite_offer = item.offer
                    product.favorite_variant = item.variant

                    favorite_products.append(product)
        context["favorite_products"] = favorite_products

        # --------------------------------------------------------------
        # 6. Benzer ürünler
        # --------------------------------------------------------------
        #
        # Şimdilik aynı kategorilerden rastgele ürünler.
        #
        # order_by("?") geçici olarak kullanılmaktadır.
        # Product sayısı ciddi şekilde büyüdüğünde değiştirilebilir.
        # --------------------------------------------------------------

        similar_products = []

        if cart_category_ids:
            similar_products = attach_thumbnails(
                list(
                    base_qs
                    .filter(
                        category_id__in=cart_category_ids,
                        buybox_price__isnull=False,
                    )
                    .order_by("id")[:25]
                )
            )

        #     owner_price_subquery = (
        #         StoreProduct.objects
        #         .filter(
        #             variant__product=OuterRef("pk"),
        #             variant_id=OuterRef("default_variant_id"),
        #             store_id=OuterRef("created_by_store_id"),
        #             status=StoreProductStatus.ACTIVE,
        #             stock__gt=0,
        #         )
        #         .order_by(
        #             "created_at",
        #             "id",
        #         )
        #         .values("price")[:1]
        #     )

        #     similar_products = attach_thumbnails(
        #         list(
        #             base_qs
        #             .filter(
        #                 category_id__in=cart_category_ids
        #             )
        #             .annotate(
        #                 owner_price=Subquery(
        #                     owner_price_subquery,
        #                     output_field=DecimalField(
        #                         max_digits=10,
        #                         decimal_places=2,
        #                     ),
        #                 )
        #             )
        #             .order_by("id")[:25]
        #         )
        #     )

        context["similar_products"] = similar_products




        # --------------------------------------------------------------
        # 7. Popüler ürünler
        # --------------------------------------------------------------
        #
        # Henüz Product seviyesinde popularity sistemi olmadığı için
        # şimdilik rastgele ürünler gösteriliyor.
        #
        # İleride örneğin:
        #
        #     .order_by("-popularity_score", "-id")
        #
        # kullanılabilir.
        # --------------------------------------------------------------

        popular_products = attach_thumbnails(
            list(
                base_qs
                .filter(
                    buybox_price__isnull=False,
                )
                .order_by("id")[:25]
            )
        )

        context["popular_products"] = popular_products

        return context


# ==============================================================================
# BASE CART API VIEW
# ==============================================================================


class BaseCartAPIView(View):
    """
    Cart JSON endpoint'leri için ortak base view.

    Sorumlulukları:

        - JSON body parse etmek
        - Standart success response üretmek
        - Standart error response üretmek
        - Domain exception'larını HTTP response'a çevirmek
        - Beklenmeyen exception'ları loglamak

    Business logic içermez.
    """

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(
                request,
                *args,
                **kwargs,
            )

        # ------------------------------------------------------------------
        # CART ITEM NOT FOUND
        # ------------------------------------------------------------------

        except CartItemNotFoundError as exc:
            logger.info(
                "Cart item not found. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                status=404,
            )

        # ------------------------------------------------------------------
        # EXPECTED CART VALIDATION ERRORS
        # ------------------------------------------------------------------

        except (
            InvalidQuantityError,
            ProductUnavailableError,
            InsufficientStockError,
            EmptyCartError,
        ) as exc:
            logger.info(
                "Cart validation failed. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
            )

            return self.error_response(
                message=str(exc),
                status=400,
            )

        # ------------------------------------------------------------------
        # OTHER CART ERRORS
        # ------------------------------------------------------------------

        except CartError as exc:
            logger.warning(
                "Cart operation failed. "
                "method=%s path=%s error=%s",
                request.method,
                request.path,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message="Sepet işlemi gerçekleştirilemedi.",
                status=400,
            )

        # ------------------------------------------------------------------
        # UNEXPECTED ERRORS
        # ------------------------------------------------------------------

        except Exception:
            logger.exception(
                "Unexpected cart view error. "
                "method=%s path=%s",
                request.method,
                request.path,
            )

            return self.error_response(
                message=(
                    "Sistemsel bir hata oluştu. "
                    "Lütfen tekrar deneyin."
                ),
                status=500,
            )

    # ==========================================================================
    # JSON PARSING
    # ==========================================================================

    @staticmethod
    def get_json_body(request):
        """
        Request body'yi JSON object olarak parse eder.

        Geçersiz JSON:
            None

        JSON object değilse:
            None
        """

        if not request.body:
            return None

        try:
            data = json.loads(request.body)

        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        if not isinstance(data, dict):
            return None

        return data

    def require_json(self, request):
        """
        JSON body zorunlu olan endpoint'ler için helper.
        """

        data = self.get_json_body(request)

        if data is None:
            return None, self.error_response(
                message="Geçersiz JSON.",
                status=400,
            )

        return data, None

    # ==========================================================================
    # JSON RESPONSE
    # ==========================================================================

    @staticmethod
    def success_response(data=None, status=200):
        """
        Standart başarılı JSON response.
        """

        response_data = {
            "success": True,
        }

        if data is not None:
            response_data.update(data)

        return JsonResponse(
            response_data,
            status=status,
        )

    @staticmethod
    def error_response(message, status=400):
        """
        Standart hata JSON response.
        """

        return JsonResponse(
            {
                "success": False,
                "error": message,
            },
            status=status,
        )

    # ==========================================================================
    # CART TOTALS
    # ==========================================================================

    @staticmethod
    def get_cart_totals(cart):
        """
        Frontend'in cart state'ini güncellemesi için
        gereken temel değerleri döndürür.

        CartService'te ayrıca summary methodu olmadığı için
        mevcut get_cart_context_data() kullanılır.
        """

        context = CartService.get_cart_context_data(
            cart
        )

        return {
            "total_items": context["total_items"],
            "selected_items_count": (
                context["selected_items_count"]
            ),
            "total_price": str(
                context["total_price"]
            ),
        }


# ==============================================================================
# CART DATA / MINI CART
# ==============================================================================


class CartDataAPIView(BaseCartAPIView):
    """
    Güncel cart verisini döndürür.

    GET:

        - total_items
        - selected_items_count
        - total_price
        - price_changes
        - mini-cart HTML
    """

    http_method_names = ["get", "options"]

    def get(self, request, *args, **kwargs):
        cart = CartService.get_or_create_cart(
            request
        )

        context = CartService.get_cart_context_data(
            cart
        )

        mini_cart_html = render_to_string(
            "cart/partials/mini_cart.html",
            context,
            request=request,
        )

        return self.success_response({
            "total_items": context["total_items"],
            "selected_items_count": (
                context["selected_items_count"]
            ),
            "total_price": str(
                context["total_price"]
            ),
            "price_changes": context["price_changes"],
            "html": mini_cart_html,
        })


# ==============================================================================
# ADD TO CART
# ==============================================================================


class AddToCartAPIView(BaseCartAPIView):
    """
    Ürünü sepete ekler.

    POST JSON:

        {
            "store_product_id": 123,
            "quantity": 2
        }
    """

    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        data, error = self.require_json(request)

        if error:
            return error

        store_product_id = data.get(
            "store_product_id"
        )

        if store_product_id is None:
            return self.error_response(
                message="Ürün ID eksik.",
                status=400,
            )

        quantity = data.get(
            "quantity",
            1,
        )

        cart = CartService.get_or_create_cart(
            request
        )

        CartService.add_to_cart(
            cart=cart,
            store_product_id=store_product_id,
            quantity=quantity,
        )

        return self.success_response({
            "message": "Ürün sepete eklendi.",
            **self.get_cart_totals(cart),
        })


# ==============================================================================
# UPDATE CART ITEM QUANTITY
# ==============================================================================


class UpdateCartItemQuantityAPIView(BaseCartAPIView):
    """
    CartItem miktarını günceller.

    quantity <= 0:
        Service contract gereği CartItem silinir.

    POST JSON:

        {
            "quantity": 3
        }
    """

    http_method_names = ["post", "options"]

    def post(self, request, item_id, *args, **kwargs):
        data, error = self.require_json(request)

        if error:
            return error

        if "quantity" not in data:
            return self.error_response(
                message="Miktar gönderilmedi.",
                status=400,
            )

        cart = CartService.get_or_create_cart(
            request
        )

        CartService.update_item_quantity(
            cart=cart,
            item_id=item_id,
            quantity=data["quantity"],
        )

        return self.success_response({
            "message": "Sepet güncellendi.",
            **self.get_cart_totals(cart),
        })


# ==============================================================================
# REMOVE CART ITEM
# ==============================================================================


class RemoveCartItemAPIView(BaseCartAPIView):
    """
    CartItem'ı tamamen siler.

    DELETE:

        /cart/items/<item_id>/
    """

    http_method_names = ["delete", "options"]

    def delete(self, request, item_id, *args, **kwargs):
        cart = CartService.get_or_create_cart(
            request
        )

        CartService.remove_item(
            cart=cart,
            item_id=item_id,
        )

        return self.success_response({
            "message": "Ürün sepetten kaldırıldı.",
            **self.get_cart_totals(cart),
        })


# ==============================================================================
# ITEM SELECTION
# ==============================================================================


class SetCartItemSelectionAPIView(BaseCartAPIView):
    """
    CartItem'ın checkout selection state'ini değiştirir.

    POST JSON:

        {
            "is_selected": true
        }
    """

    http_method_names = ["post", "options"]

    def post(self, request, item_id, *args, **kwargs):
        data, error = self.require_json(request)

        if error:
            return error

        if "is_selected" not in data:
            return self.error_response(
                message="is_selected alanı eksik.",
                status=400,
            )

        is_selected = data["is_selected"]

        if not isinstance(is_selected, bool):
            return self.error_response(
                message="is_selected boolean olmalıdır.",
                status=400,
            )

        cart = CartService.get_or_create_cart(
            request
        )

        CartService.toggle_item_selection(
            cart=cart,
            item_id=item_id,
            is_selected=is_selected,
        )

        return self.success_response({
            "message": "Ürün seçimi güncellendi.",
            **self.get_cart_totals(cart),
        })


# ==============================================================================
# ACKNOWLEDGE PRICE CHANGES
# ==============================================================================


class AcknowledgePriceChangesAPIView(BaseCartAPIView):
    """
    Kullanıcının gördüğü fiyat değişikliklerini
    acknowledge eder.

    POST:

        /cart/price-changes/acknowledge/
    """

    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        cart = CartService.get_or_create_cart(
            request
        )

        CartService.mark_price_changes_as_seen(
            cart=cart,
        )

        return self.success_response({
            "message": (
                "Fiyat değişiklikleri "
                "okundu olarak işaretlendi."
            ),
        })


# ==============================================================================
# CHECKOUT VALIDATION
# ==============================================================================


class CheckoutValidationAPIView(BaseCartAPIView):
    """
    Checkout öncesi selected CartItem'ları validate eder.

    POST:

        /cart/checkout/validate/

    Bu endpoint:

        - Order oluşturmaz.
        - Stock düşmez.
        - Payment yapmaz.

    Ancak mevcut CartService contract'ına göre
    cart state değişebilir:

        - unavailable item deselect edilir.
        - quantity stock miktarına düşürülebilir.
    """

    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        cart = CartService.get_or_create_cart(
            request
        )

        result = CartService.validate_cart_for_checkout(
            cart=cart,
        )

        # Validation sırasında cart state değişebildiği
        # için işlemden sonra context tekrar alınır.
        context = CartService.get_cart_context_data(
            cart
        )

        return self.success_response({
            "is_valid": result["is_valid"],
            "warnings": result["warnings"],
            "price_changes": result["price_changes"],
            "total_items": context["total_items"],
            "selected_items_count": (
                context["selected_items_count"]
            ),
            "total_price": str(
                context["total_price"]
            ),
        })
