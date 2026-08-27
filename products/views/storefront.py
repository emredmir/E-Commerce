import logging
from django.db.models import Min, Sum, Count, Q, Prefetch, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.views.generic import ListView, View
from django.shortcuts import get_object_or_404, render

from products.models import (
    Product,
    ProductStatus,
    ProductVariant,
    StoreProduct,
    StoreProductStatus,
    Category,
    ProductImageGroup,
    ProductImage,
)
from products.forms.storefront import ProductFilterForm
from products.services.storefront import ProductDetailService
from products.services.storefront_offers import StorefrontOfferService

logger = logging.getLogger(__name__)


class ProductListView(ListView):
    """
    Müşteri Vitrini (Tüm Ürünler).

    Sadece:
    - Aktif ürünleri,
    - Aktif varyantları,
    - Aktif mağazalardaki,
    - Aktif ve stoklu teklifleri
    bulunan ürünleri listeler.
    """

    model = Product
    template_name = "products/public/product_list.html"
    context_object_name = "products"
    paginate_by = 24

    ORDERING_MAP = {
        "min_price": "buybox_price",
        "-min_price": "-buybox_price",
        "-sold": "-total_sales",
        "-created": "-created_at",
    }

    # 1. SATIN ALINABİLİRLİK (BuyBox & Vitrin Görünürlüğü)
    AVAILABLE_OFFER_Q = Q(
        variants__store_offers__status=StoreProductStatus.ACTIVE,
        variants__store_offers__stock__gt=0,
        variants__store_offers__store__is_active=True,
    )

    def get_queryset(self):
        # -------------------------------------------------------------
        # 1. GÖRSEL PREFETCH (Veritabanında Sıralanmış Hali)
        # -------------------------------------------------------------
        main_images_prefetch = Prefetch(
            "images",
            queryset=ProductImage.objects.filter(is_main=True),
            to_attr="main_image_list"
        )

        image_groups_prefetch = Prefetch(
            "image_groups",
            queryset=(
                ProductImageGroup.objects
                .filter(is_active=True)
                .order_by("sort_order", "id")  # Sıralamayı DB'ye yıktık
                .prefetch_related(main_images_prefetch)
            ),
            to_attr="cached_image_groups"
        )

        # -------------------------------------------------------------
        # 2. SUBQUERY İLE GÜVENLİ HESAPLAMALAR (Join Çarpılmasını Önler)
        # -------------------------------------------------------------
        # A. Toplam Satış Hesaplaması Alt Sorgusu
        sales_sq = StoreProduct.objects.filter(
            variant__product=OuterRef('pk'),
            status__in=[StoreProductStatus.ACTIVE, StoreProductStatus.OUT_OF_STOCK],
            store__is_active=True,
        ).values('variant__product').annotate(
            total=Sum('sold_count')
        ).values('total')


        # Varsayılan Varyantın Fiyatı (Varsa)
        default_variant_price_sq = StorefrontOfferService.get_product_buybox_subquery(use_default_variant=True)
        # Herhangi Bir Varyantın En Düşük Fiyatı (Varsayılan yoksa veya tükendiyse Fallback olarak)
        fallback_price_sq = StorefrontOfferService.get_product_buybox_subquery(use_default_variant=False)

        default_variant_store_sq = StorefrontOfferService.get_product_buybox_store_subquery(use_default_variant=True)
        fallback_store_sq = StorefrontOfferService.get_product_buybox_store_subquery(use_default_variant=False)

        default_variant_id_sq = StorefrontOfferService.get_product_buybox_variant_subquery(use_default_variant=True)
        fallback_variant_id_sq = StorefrontOfferService.get_product_buybox_variant_subquery(use_default_variant=False)


        # -------------------------------------------------------------
        # 3. ANA SORGUMUZ
        # -------------------------------------------------------------
        queryset = (
            Product.objects
            .filter(
                self.AVAILABLE_OFFER_Q,
                status=ProductStatus.ACTIVE,
                variants__is_active=True,
                
            )
            .distinct()
            .select_related("category", "brand")
            .prefetch_related(image_groups_prefetch)
            .annotate(
                # Önce default varyantın fiyatını dene, null dönerse fallback (en ucuz) fiyatını al!
                buybox_price=Coalesce(default_variant_price_sq, fallback_price_sq),
                buybox_store_id=Coalesce(default_variant_store_sq, fallback_store_sq),
                buybox_variant_id=Coalesce(default_variant_id_sq, fallback_variant_id_sq),
                total_sales=Coalesce(Subquery(sales_sq, output_field=IntegerField()), 0),
            )
        )

        # -------------------------------------------------------------
        # 4. FİLTRELEME
        # -------------------------------------------------------------
        form = self.get_filter_form()

        if form.is_valid():
            category = form.cleaned_data.get("category")
            brands = form.cleaned_data.get("brand")
            min_price = form.cleaned_data.get("min_price")
            max_price = form.cleaned_data.get("max_price")
            price_ranges = form.cleaned_data.get("price_range")
            ordering = form.cleaned_data.get("ordering")

            if category:
                queryset = queryset.filter(
                    category_id__in=self._get_category_descendants(category)
                )

            if brands:
                queryset = queryset.filter(brand__in=brands)

            if price_ranges:
                range_q = Q()
                for pr in price_ranges:
                    if pr == '0-100': range_q |= Q(buybox_price__gte=0, buybox_price__lte=100)
                    elif pr == '100-500': range_q |= Q(buybox_price__gte=100, buybox_price__lte=500)
                    elif pr == '500-1000': range_q |= Q(buybox_price__gte=500, buybox_price__lte=1000)
                    elif pr == '1000+': range_q |= Q(buybox_price__gte=1000)
                
                if range_q:
                    queryset = queryset.filter(range_q)

            if min_price is not None:
                queryset = queryset.filter(buybox_price__gte=min_price)

            if max_price is not None:
                queryset = queryset.filter(buybox_price__lte=max_price)

            queryset = queryset.order_by(
                self.ORDERING_MAP.get(ordering, "-total_sales"),
                "-pk",
            )
        else:
            queryset = queryset.order_by("-total_sales", "-pk")

        colors = self.request.GET.getlist("color")
        if colors:
            queryset = queryset.filter(
                variants__is_active=True,
                variants__attribute_values__attribute__name__iexact='Renk', # Veritabanındaki adıyla eşleşmeli
                variants__attribute_values__value__in=colors
            )

        return queryset

    def get_filter_form(self):
        if not hasattr(self, "_filter_form"):
            self._filter_form = ProductFilterForm(self.request.GET or None)
        return self._filter_form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = self.get_filter_form()
        context["filter_form"] = self.get_filter_form()

        active_tags = []
        selected_brands = []
        selected_ranges = []

        if form.is_valid():
            # Kategori
            cat = form.cleaned_data.get("category")
            if cat:
                active_tags.append({"field": "category", "val": cat.id, "label": f"Kategori: {cat.name}"})
            
            # Markalar
            for b in form.cleaned_data.get("brand") or []:
                selected_brands.append(b.id)
                active_tags.append({"field": "brand", "val": b.id, "label": f"Marka: {b.name}"})
                
            # Fiyat Aralıkları
            for pr in form.cleaned_data.get("price_range") or []:
                selected_ranges.append(pr)
                active_tags.append({"field": "price_range", "val": pr, "label": f"Fiyat: {pr} TL"})

            # Manuel Min/Max
            min_p = form.cleaned_data.get("min_price")
            max_p = form.cleaned_data.get("max_price")
            if min_p or max_p:
                lbl = f"Fiyat: {min_p or 0} - {max_p or 'Üstü'} TL"
                active_tags.append({"field": "manual_price", "val": "manual", "label": lbl})

        # Renkler
        selected_colors = self.request.GET.getlist("color")
        for c in selected_colors:
            active_tags.append({"field": "color", "val": c, "label": f"Renk: {c.title()}"})

        context["active_tags"] = active_tags
        context["selected_brands"] = selected_brands
        context["selected_ranges"] = selected_ranges
        context["selected_colors"] = selected_colors

        # Görsel çözümlemesi
        for product in context["products"]:
            product.thumbnail_url = self._resolve_product_thumbnail(product)

        return context

    def _resolve_product_thumbnail(self, product):
        """
        Ürünün vitrin kapak fotoğrafını belirler.
        Prefetch içinde order_by("sort_order") yapıldığı için,
        Python'da tekrar sorted() yapılmasına gerek kalmamıştır.
        """
        for group in getattr(product, "cached_image_groups", []):
            images = getattr(group, "main_image_list", [])
            if images:
                return images[0].image.url
        return None

    def _get_category_descendants(self, category):
        ids = [category.pk]
        children = list(
            category.children
            .filter(is_active=True)
            .values_list("pk", flat=True)
        )

        while children:
            ids.extend(children)
            children = list(
                Category.objects
                .filter(parent_id__in=children, is_active=True)
                .values_list("pk", flat=True)
            )

        return ids


class CategoryProductListView(ProductListView):
    """
    Belirli bir kategoriye ait ürünleri listeler.
    Üst sınıfın tüm BuyBox, filtreleme ve sıralama mantığını miras alır.
    """
    template_name = "products/public/product_list.html"

    def get_category(self):
        if not hasattr(self, "_category"):
            self._category = get_object_or_404(
                Category.objects.select_related("parent"),
                slug=self.kwargs["slug"],
                is_active=True,
            )
        return self._category

    def get_queryset(self):
        category = self.get_category()
        return (
            super()
            .get_queryset()
            .filter(category_id__in=self._get_category_descendants(category))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category = self.get_category()

        context["category"] = category
        context["breadcrumbs"] = self._get_breadcrumbs(category)

        # -------------------------------------------------------------
        # ALT KATEGORİLER
        # (Aynı BuyBox ve satılabilirlik mantığı, products__ öneki ile)
        # -------------------------------------------------------------
        context["subcategories"] = (
            category.children
            .filter(is_active=True)
            .annotate(
                product_count=Count(
                    "products",
                    filter=(
                        Q(products__status=ProductStatus.ACTIVE)
                        & Q(products__variants__is_active=True)
                        & Q(products__variants__store_offers__status=StoreProductStatus.ACTIVE)
                        & Q(products__variants__store_offers__stock__gt=0)
                        & Q(products__variants__store_offers__store__is_active=True)
                    ),
                    distinct=True,
                )
            )
            .filter(product_count__gt=0)
            .order_by("name")
        )

        return context

    def _get_breadcrumbs(self, category):
        breadcrumbs = []
        current = category
        while current:
            breadcrumbs.append(current)
            current = current.parent
        return list(reversed(breadcrumbs))

class ProductDetailView(View):
    """
    Müşteri vitrini — Ürün Detay Sayfası.

    View'ın görevi:

    - Product'ı gerekli ilişkileriyle yüklemek
    - Request'ten variant bilgisini almak
    - ProductDetailService'e devretmek
    - Template'i render etmek

    Business logic burada bulunmaz.
    """

    template_name = "products/public/product_detail.html"

    def get_product(self, slug):

        # =====================================================
        # PRODUCT IMAGES
        # =====================================================

        images_prefetch = Prefetch(
            "images",
            queryset=(
                ProductImage.objects
                .order_by(
                    "sort_order",
                    "id",
                )
            ),
        )

        # =====================================================
        # IMAGE GROUPS
        # =====================================================

        image_groups_prefetch = Prefetch(
            "image_groups",
            queryset=(
                ProductImageGroup.objects
                .filter(is_active=True)
                .prefetch_related(
                    "visual_attribute_values",
                    images_prefetch,
                )
            ),
            to_attr="cached_image_groups",
        )

        # =====================================================
        # VARIANTS
        # =====================================================

        variants_prefetch = Prefetch(
            "variants",
            queryset=(
                ProductVariant.objects
                .filter(is_active=True)
                .prefetch_related(
                    "attribute_values__attribute",
                )
            ),
            to_attr="active_variants",
        )

        # =====================================================
        # PRODUCT
        # =====================================================

        return get_object_or_404(
            Product.objects
            .select_related(
                "category",
                "brand",
            )
            .prefetch_related(
                image_groups_prefetch,
                variants_prefetch,
            ),
            slug=slug,
            status=ProductStatus.ACTIVE,
        )

    def _get_breadcrumbs(self, category):
        """Kategoriden köke doğru breadcrumb zinciri oluşturur."""
        breadcrumbs = []
        current = category
        while current:
            breadcrumbs.append(current)
            current = current.parent
        return list(reversed(breadcrumbs))
        

    def get(self, request, slug, *args, **kwargs):

        product = self.get_product(slug)

        context = ProductDetailService.get_page_data(
            product=product,
            variant_id=request.GET.get("variant"),
            offer_id=request.GET.get("offer"),
        )

        # ----------------------------------------------------
        # BENZER VE MARKA ÜRÜNLERİ
        # (Sadece aktif ve BuyBox'ı olan ürünler getirilir)
        # ----------------------------------------------------
        base_qs = Product.objects.filter(
            ProductListView.AVAILABLE_OFFER_Q,
            status=ProductStatus.ACTIVE,
            variants__is_active=True
        ).distinct().exclude(id=product.id) # Kendisini hariç tut

        # Aynı Kategorideki Benzer Ürünler (Maks 10 tane)
        similar_products = base_qs.filter(category=product.category).select_related('brand').prefetch_related(
            Prefetch("image_groups", queryset=ProductImageGroup.objects.filter(is_active=True).order_by("sort_order").prefetch_related("images"))
        )[:10]

        # Kapak fotoğraflarını Python'da çözümle
        for sp in similar_products:
            # Ufak bir metod ile ilk resmi alıyoruz
            sp.thumbnail_url = None
            for group in sp.image_groups.all():
                images = list(group.images.all())

                main_img = next(
                    (img for img in images if img.is_main),
                    images[0] if images else None,
                )

                if main_img:
                    sp.thumbnail_url = main_img.image.url
                    break

        context["similar_products"] = similar_products

        # Aynı Markanın Ürünleri (Maks 10 tane) - Sadece ürünün markası varsa çalışır
        brand_products = []
        if product.brand:
            brand_products = base_qs.filter(brand=product.brand).select_related('brand').prefetch_related(
                Prefetch("image_groups", queryset=ProductImageGroup.objects.filter(is_active=True).order_by("sort_order").prefetch_related("images"))
            )[:10]
            
            for bp in brand_products:
                bp.thumbnail_url = None
                for group in bp.image_groups.all():
                    images = list(group.images.all())

                    main_img = next(
                        (img for img in images if img.is_main),
                        images[0] if images else None,
                    )
            
                    if main_img:
                        bp.thumbnail_url = main_img.image.url
                        break
                        
        context["brand_products"] = brand_products

        # BREADCRUMB İÇİN KATEGORİ AĞACINI CONTEXT'E EKLİYORUZ
        context["breadcrumbs"] = self._get_breadcrumbs(product.category)

        return render(
            request,
            self.template_name,
            context,
        )