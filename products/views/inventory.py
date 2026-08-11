import logging
from django.db.models import Count, Q, Prefetch
from django.views.generic import ListView
from django.contrib import messages
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from products.forms import StoreProductUpdateForm

from products.mixins import SellerRequiredMixin, StoreOwnerMixin
from products.models import (
    StoreProduct, 
    StoreProductStatus, 
    ProductImageGroup, 
    ProductImage
)






logger = logging.getLogger(__name__)

class StoreProductListView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    ListView,
):
    """
    Satıcının mağazasındaki satış tekliflerini listeler.
    N+1 sorgu problemi önlenmiş ve varyant özelliklerine göre 
    kapak fotoğrafları (thumbnail) Python belleğinde (Cache) çözümlenmiştir.
    """
    model = StoreProduct
    template_name = "products/seller/store_product_list.html"
    context_object_name = "store_products"
    paginate_by = 20

    def get_queryset(self):
        store = self.get_store()

        # 1. GÖRSEL PREFETCH (N+1 Problemini Önleme)
        main_images_prefetch = Prefetch(
            "images",
            queryset=ProductImage.objects.filter(is_main=True),
            to_attr="main_image_list"
        )

        image_groups_prefetch = Prefetch(
            "variant__product__image_groups",
            queryset=ProductImageGroup.objects.filter(is_active=True).prefetch_related(
                "visual_attribute_values",
                main_images_prefetch
            ),
            to_attr="cached_image_groups"
        )

        # 2. ANA SORGUMUZ
        queryset = (
            StoreProduct.objects
            .filter(store=store)
            .select_related(
                "variant__product",
                "variant__product__category",
                "variant__product__brand",
            )
            .prefetch_related(
                "variant__attribute_values__attribute",
                image_groups_prefetch
            )
            .order_by("-updated_at", "-pk")
        )

        # 3. Status filtresi
        status = self.request.GET.get("status", "").strip()
        valid_statuses = {value for value, _ in StoreProductStatus.choices}

        if status in valid_statuses:
            queryset = queryset.filter(status=status)

        # 4. Arama
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(variant__product__name__icontains=query)
                | Q(sku__icontains=query)  # StoreProduct'ın kendi SKU'su aranmalı
                | Q(variant__barcode__icontains=query)
                | Q(variant__product__brand__name__icontains=query)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        store = self.get_store()

        context["store"] = store
        context["status_choices"] = StoreProductStatus.choices
        context["current_status"] = self.request.GET.get("status", "").strip()
        context["current_q"] = self.request.GET.get("q", "").strip()
        context["status_counts"] = self._get_status_counts(store)

        # 5. GÖRSEL EŞLEŞTİRMESİ (PYTHON BELLEĞİNDE)
        for sp in context["store_products"]:
            variant_img, common_img = self._resolve_images(sp)
            sp.thumbnail_url = variant_img
            sp.common_image_url = common_img # Ana ürün resmi için

        return context

    def _get_status_counts(self, store):
        """Durum sayılarını tek sorguda hesaplar."""
        counts = {value: 0 for value, _ in StoreProductStatus.choices}

        queryset = (
            StoreProduct.objects
            .filter(store=store)
            .values("status")
            .annotate(count=Count("id"))
        )

        for item in queryset:
            counts[item["status"]] = item["count"]

        counts["all"] = sum(counts.values())
        return counts

    def _resolve_images(self, store_product):
        """
        Varyantın özellikleriyle ürünün görsel gruplarını eşleştirerek
        en doğru kapak fotoğrafını bulur.
        """
        variant = store_product.variant
        variant_attr_ids = {val.pk for val in variant.attribute_values.all()}
        
        best_group = None
        best_match_count = -1
        common_group = None
        
        # hasattr kontrolü (Eğer veritabanında hiç görsel grubu yoksa patlamaması için)
        if not hasattr(variant.product, 'cached_image_groups'):
            return None

        for group in variant.product.cached_image_groups:
            group_attr_ids = {val.pk for val in group.visual_attribute_values.all()}
            
            # Ortak grup
            if len(group_attr_ids) == 0:
                common_group = group
                continue
                
            if group_attr_ids.issubset(variant_attr_ids):
                match_count = len(group_attr_ids)
                if match_count > best_match_count:
                    best_match_count = match_count
                    best_group = group
        
        variant_img = None
        common_img = None

        if common_group and common_group.main_image_list:
            common_img = common_group.main_image_list[0].image.url
            
        if best_group and best_group.main_image_list:
            variant_img = best_group.main_image_list[0].image.url
            
        # Eğer varyantın özel resmi yoksa, ortak resmi varyant resmi gibi kullan
        if not variant_img and common_img:
            variant_img = common_img
            
        return variant_img, common_img
            


class StoreProductUpdateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    template_name = "products/seller/inventory/offer_update.html"

    # ------------------------------------------------------------------
    # Data Fetching
    # ------------------------------------------------------------------

    def _get_offer(self):
        """
        Güncellenecek ve ekranda gösterilecek StoreProduct kaydını getirir.
        İhtiyaç duyulan Thumbnail ilişkileri de tek seferde yüklenir (Prefetch).
        Böylece hatalı POST işlemlerinde DB'ye 2. kez sorgu atılmaz.
        """
        if hasattr(self, "_offer"):
            return self._offer

        store = self.get_store()

        self._offer = get_object_or_404(
            StoreProduct.objects
            .select_related(
                "variant__product",
                "variant__product__category",
                "variant__product__brand",
            )
            .prefetch_related(
                "variant__attribute_values__attribute",
                self._get_thumbnail_prefetch(),
            ),
            pk=self.kwargs["pk"],
            store=store,
        )

        return self._offer

    # ------------------------------------------------------------------
    # Form
    # ------------------------------------------------------------------

    def _get_form(self, *, data=None):
        return StoreProductUpdateForm(
            data=data,
            instance=self._get_offer(),
        )

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------

    @staticmethod
    def _get_thumbnail_prefetch():
        main_images_prefetch = Prefetch(
            "images",
            queryset=ProductImage.objects.filter(is_active=True, is_main=True),
            to_attr="main_image_list",
        )

        return Prefetch(
            "variant__product__image_groups",
            queryset=(
                ProductImageGroup.objects
                .filter(is_active=True)
                .prefetch_related(
                    "visual_attribute_values",
                    main_images_prefetch,
                )
            ),
            to_attr="cached_image_groups",
        )

    @staticmethod
    def _resolve_thumbnail(offer):
        variant = offer.variant
        product = variant.product

        variant_attribute_ids = {val.pk for val in variant.attribute_values.all()}
        image_groups = getattr(product, "cached_image_groups", ())

        best_group = None
        best_match_count = -1
        common_group = None

        for group in image_groups:
            group_attribute_ids = {val.pk for val in group.visual_attribute_values.all()}

            if not group_attribute_ids:
                common_group = group
                continue

            if not group_attribute_ids.issubset(variant_attribute_ids):
                continue

            match_count = len(group_attribute_ids)
            if match_count > best_match_count:
                best_match_count = match_count
                best_group = group

        target_group = best_group or common_group

        if not target_group:
            return None

        main_images = getattr(target_group, "main_image_list", ())
        if not main_images or not main_images[0].image:
            return None

        return main_images[0].image.url

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_form(self, request, store, offer, form, *, status=200):
        offer.thumbnail_url = self._resolve_thumbnail(offer)

        return render(
            request,
            self.template_name,
            {
                "store": store,
                "offer": offer,  # Context'i teke düşürdük
                "form": form,
            },
            status=status,
        )

    # ------------------------------------------------------------------
    # GET & POST
    # ------------------------------------------------------------------

    def get(self, request, *args, **kwargs):
        store = self.get_store()
        offer = self._get_offer()
        form = self._get_form()

        return self._render_form(request, store, offer, form)

    def post(self, request, *args, **kwargs):
        store = self.get_store()
        offer = self._get_offer()
        form = self._get_form(data=request.POST)

        if not form.is_valid():
            messages.error(request, "Lütfen formdaki hataları düzeltin.")
            return self._render_form(request, store, offer, form)

        try:
            with transaction.atomic():
                updated_offer = form.save()

        except IntegrityError:
            logger.warning(
                "StoreProduct integrity conflict during update. offer_id=%s store_id=%s user_id=%s",
                offer.pk, store.pk, request.user.pk, exc_info=True
            )
            messages.error(
                request,
                "Ürün bilgileri güncellenirken bir çakışma oluştu. "
                "SKU veya benzeri benzersiz alanları kontrol edin."
            )
            return self._render_form(request, store, offer, form, status=409)

        except DatabaseError:
            logger.exception(
                "Database error while updating StoreProduct. offer_id=%s store_id=%s user_id=%s",
                offer.pk, store.pk, request.user.pk
            )
            messages.error(
                request,
                "Şu anda teklif güncellenemiyor. Lütfen birkaç dakika sonra tekrar deneyin."
            )
            return self._render_form(request, store, offer, form, status=500)

        messages.success(
            request,
            f'"{updated_offer.variant.product.name}" için satış teklifiniz başarıyla güncellendi.'
        )

        return redirect("products:store_product_list", store_slug=store.slug)