import logging
from django.db.models import Count, Q, Prefetch
from django.views.generic import ListView
from django.contrib import messages
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views import View
from django.http import JsonResponse
import json
from django.urls import reverse

from products.forms.inventory import StoreProductUpdateForm, ProductUpdateForm

from products.mixins import SellerRequiredMixin, StoreOwnerMixin
from products.models import (
    StoreProduct, 
    StoreProductStatus, 
    ProductImageGroup,
    ProductImage,
    Product,
    ProductDraft,
    ProductDraftImage,
    ProductDraftImageGroup,
    ProductStatus,
    ProductVariant,
    CategoryAttribute,
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
        status = self.request.GET.get("status", "").strip()


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
        query = self.request.GET.get("q", "").strip()

        # 1. TASLAK RESİMLERİNİ N+1 OLMADAN ÇEKMEK İÇİN PREFETCH YAZIYORUZ
        draft_images_prefetch = Prefetch(
            "image_groups",
            queryset=ProductDraftImageGroup.objects.filter(is_active=True).prefetch_related(
                Prefetch("images", queryset=ProductDraftImage.objects.filter(is_main=True))
            ),
            to_attr="cached_image_groups"
        )

        # 2. TASLAKLARI ÇEK
        # TASLAK (YARIM KALMIŞ) ÜRÜNLERİ ProductDraft TABLOSUNDAN ÇEK
        draft_products = ProductDraft.objects.filter(
            store=store,
            status=ProductDraft.Status.DRAFT
        ).select_related("category", "brand").prefetch_related(draft_images_prefetch)

        if query:
            draft_products = draft_products.filter(
                Q(name__icontains=query) | Q(brand__name__icontains=query)
            )

        # 3. TASLAKLAR İÇİN DİNAMİK URL VE RESİM AYARLAMA
        for draft in draft_products:
            # Sihirbaza Devam Et Linki (Hangi adımda kaldıysa o adıma gider)
            step = draft.current_step or 1
            # EĞER 1. ADIMDAYSA (wizard_step1 draft_id parametresi kabul etmez)
            if step == 1:
                draft.resume_url = reverse(
                    "products:wizard_step1_edit", 
                    kwargs={"store_slug": store.slug, "draft_id": draft.pk}
                )
            
            # EĞER 2, 3 VEYA 4. ADIMDAYSA (URL'de draft_id zorunludur)
            else:
                draft.resume_url = reverse(
                    f"products:wizard_step{step}", 
                    kwargs={"store_slug": store.slug, "draft_id": draft.pk}
                )

            # Kapak Resmi Bulma
            draft.cover_image_url = ""
            if hasattr(draft, "cached_image_groups") and draft.cached_image_groups:
                first_group = draft.cached_image_groups[0]
                # Prefetch ile getirdiğimiz ana resimlere bakıyoruz
                main_images = [img for img in first_group.images.all() if img.is_main]
                if main_images and main_images[0].image:
                    draft.cover_image_url = main_images[0].image.url
                elif first_group.images.all() and first_group.images.all()[0].image:
                    # is_main işaretlenmemiş ama resim varsa ilkini kapak yap
                    draft.cover_image_url = first_group.images.all()[0].image.url

        context["draft_products"] = draft_products # HTML'e gönder
        context["store"] = store
        context["status_choices"] = StoreProductStatus.choices
        context["current_status"] = self.request.GET.get("status", "").strip()
        context["current_q"] = self.request.GET.get("q", "").strip()
        context["status_counts"] = self._get_status_counts(store, draft_count=draft_products.count())

        # 5. GÖRSEL EŞLEŞTİRMESİ (PYTHON BELLEĞİNDE)
        for sp in context["store_products"]:
            variant_img, common_img = self._resolve_images(sp)
            sp.thumbnail_url = variant_img
            sp.common_image_url = common_img # Ana ürün resmi için

        return context

    def _get_status_counts(self, store, draft_count=0):
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

        counts["draft"] = draft_count
        counts["all"] = sum(counts.values()) # Taslaklar dahil genel toplam
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
    template_name = "products/seller/offer_update.html"

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
            queryset=ProductImage.objects.filter(is_main=True),
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


class ProductUpdateView(SellerRequiredMixin, StoreOwnerMixin, View):
    """
    Global katalog bilgilerini (Açıklama) günceller.

    GÜVENLİK: Yalnızca ürünü kataloğa İLK EKLEYEN Mağaza (Store) bu işlemleri yapabilir.

    """
    template_name = "products/seller/product_update.html"


    def get_product(self):

        if hasattr(self, "_product"):
            return self._product

        store = self.get_store()

        # GÜVENLİK: created_by_store=store kuralı ile IDOR (Yetkisiz Erişim) tamamen engellenir. boş grupları engellemek için distinct() eklendi
        self._product = get_object_or_404(
            Product.objects.select_related("category", "brand").prefetch_related(
                Prefetch(
                    "image_groups",
                    queryset=ProductImageGroup.objects.filter(is_active=True).distinct().prefetch_related(
                        "visual_attribute_values",
                        Prefetch("images", queryset=ProductImage.objects.order_by("sort_order"))
                    )
                ),
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("attribute_values")
                )
            ),
            slug=self.kwargs["product_slug"],
            status=ProductStatus.ACTIVE,
            created_by_store=store 
        )
        return self._product



    def get_context_data(self, request, product, form):
        # A. Ortak grubun (Varyant bağımsız) her zaman TEK BİR tane olduğundan emin ol (Tekrar edenleri Temizle)
        common_groups = list(ProductImageGroup.objects.filter(product=product, visual_attribute_values__isnull=True).order_by('id'))
        if not common_groups:
            common_group = ProductImageGroup.objects.create(product=product, sort_order=0)
        else:
            common_group = common_groups[0]
            # Eğer hata sonucu birden fazla ortak grup oluşmuşsa, hepsinin resimlerini ana gruba aktar ve çöp grupları sil.
            if len(common_groups) > 1:
                for duplicate in common_groups[1:]:
                    duplicate.images.all().update(is_main=False, group=common_group)
                    duplicate.delete()

        # Bu temizlikten sonra kapak resmini veritabanında garantiye alalım (does not exist hatasını engeller)
        self._sync_product_main_image(product)

        # B. Kategoriye ait "Görsel Özellikleri" (is_visual=True olanları) tespit et
        visual_attr_ids = set(
            CategoryAttribute.objects.filter(
                category=product.category, 
                is_visual=True
            ).values_list('attribute_id', flat=True)
        )

        # TEKRAR EDEN RESİM GRUPLARINI ENGELLEME (DEDUPLICATION)
        unique_groups = []
        seen_signatures = set()

        # Mevcut tüm grupları belleğe al
        for group in product.image_groups.all():
            sig = tuple(sorted(v.pk for v in group.visual_attribute_values.all()))
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                unique_groups.append(group)
            elif group.pk != common_group.pk:
                # EĞER aynı varyanta ait 2. bir görünmez grup varsa, onun da resimlerini kurtar ve kendisini sil.
                master_group = next(g for g in unique_groups if tuple(sorted(v.pk for v in g.visual_attribute_values.all())) == sig)
                group.images.all().update(is_main=False, group=master_group)
                group.delete()

        # Ürünün TÜM aktif varyantlarını kontrol et ve görsel grubu yoksa YARAT!
        for variant in product.variants.all():
            # Bu varyantın sadece "görsel" olan özelliklerini ayıkla
            visual_vals = [
                val for val in variant.attribute_values.all() 
                if val.attribute_id in visual_attr_ids
            ]
            
            if visual_vals:
                sig = tuple(sorted(v.pk for v in visual_vals))
                if sig not in seen_signatures:
                    # DİKKAT: Başka satıcı yeni varyant eklemiş ama görsel grubu oluşmamış.
                    # Katalog sahibi fotoğraf yükleyebilsin diye anında oluşturuyoruz!
                    new_group = ProductImageGroup.objects.create(
                        product=product, 
                        sort_order=len(unique_groups) + 1
                    )
                    new_group.visual_attribute_values.set(visual_vals)
                    
                    seen_signatures.add(sig)
                    unique_groups.append(new_group)

        return {
            "store": self.get_store(),
            "product": product,
            "form": form,
            "unique_image_groups": unique_groups,
            "source_offer_id": request.GET.get("offer_id") or request.POST.get("source_offer_id", ""),
        }

    # ANA VİTRİN RESMİNİ SENKRONİZE EDEN YARDIMCI METOD
    def _sync_product_main_image(self, product):
        """Ortak gruptaki kapak resmini, ürünün vitrin resmi (product.image) ile kusursuz senkronize eder"""
        if hasattr(product, 'image'):
            # En güncel ortak grubu al
            common_group = ProductImageGroup.objects.filter(product=product, visual_attribute_values__isnull=True).order_by('id').first()
            if common_group:
                main_img = common_group.images.filter(is_main=True).first()
                if main_img and main_img.image:
                    product.image = main_img.image.name
                else:
                    # Eğer kapak resmi belirtilmemişse veya silinmişse, var olan ilk resmi vitrin resmi yap
                    first_img = common_group.images.order_by('sort_order', 'id').first()
                    product.image = first_img.image.name if (first_img and first_img.image) else None
            else:
                product.image = None
                
            product.save(update_fields=['image'])

    def get(self, request, *args, **kwargs):

        product = self.get_product()

        form = ProductUpdateForm(instance=product)


        context = self.get_context_data(request, product, form)
        return render(request, self.template_name, context)


    def post(self, request, *args, **kwargs):
        store = self.get_store()
        product = self.get_product()

        # --------------------------------------------------------------------
        # AJAX İŞLEMLERİ (GÖRSEL YÖNETİMİ: Yükle, Sil, Sırala, Kapak Yap)
        # --------------------------------------------------------------------
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            action = request.POST.get("action")

            try:
                with transaction.atomic():
                    # 1. RESİM YÜKLEME
                    if action == "upload":
                        group_id = request.POST.get("group_id")
                        group = get_object_or_404(ProductImageGroup, pk=group_id, product=product)
                        images = request.FILES.getlist("images")
                        
                        if not images:
                            return JsonResponse({"success": False, "message": "Resim bulunamadı."})

                        # Gruptaki mevcut dosya isimlerini al
                        existing_filenames = {
                            img.image.name.rsplit("/", 1)[-1]
                            for img in group.images.all()
                            if img.image
                        }

                        # Sadece gerçekten yeni olan dosyaları belirle
                        new_images = [
                            img_file
                            for img_file in images
                            if img_file.name not in existing_filenames
                        ]

                        if group.images.count() + len(images) > 20:
                            return JsonResponse({"success": False, "message": "Bir gruba en fazla 20 görsel yükleyebilirsiniz."})

                        # Hepsi zaten mevcutsa
                        if not new_images:
                            return JsonResponse({
                                "success": False,
                                "message": "Seçilen görseller bu grupta zaten mevcut."
                            })

                        current_count = group.images.count()
                        has_main = group.images.filter(is_main=True).exists()
                        last_order = current_count

                        for idx, img_file in enumerate(new_images):
                            # Eğer grupta hiç ana resim kalmamışsa, yüklenen İLK resmi kapak yap
                            is_main_flag = True if (not has_main and idx == 0) else False
                            ProductImage.objects.create(group=group, image=img_file, sort_order=last_order, is_main=is_main_flag)
                            last_order += 1

                        # Gruba ilk resim yüklendiğinde otomatik kapak olacağı için asıl resmi güncelle!
                        self._sync_product_main_image(product)
                        
                        return JsonResponse({"success": True, "message": "Görseller eklendi.", "html": self._render_group_html(group)})

                    # 2. RESİM SİLME
                    elif action == "delete":
                        image_id = request.POST.get("image_id")
                        image = get_object_or_404(ProductImage, pk=image_id, group__product=product)
                        group = image.group
                        was_main = image.is_main

                        
                        image.delete()

                        # Kapak silindiyse veya geride TEK BİR resim kaldıysa onu otomatik kapak yap!
                        remaining_images = group.images.order_by('sort_order', 'id')
                        if remaining_images.exists():
                            # Kapak silindiyse VEYA grupta kapak resmi tanımlı değilse, ilk resmi otomatik kapak yap
                            if was_main or not remaining_images.filter(is_main=True).exists():
                                first_img = remaining_images.first()
                                first_img.is_main = True
                                first_img.save(update_fields=['is_main'])

                        # Silinen resim asıl ürün resmi (kapak) olabileceği için hemen senkronize et!
                        self._sync_product_main_image(product)
                        
                        return JsonResponse({"success": True, "message": "Görsel silindi.", "html": self._render_group_html(group)})

                    # 3. KAPAK YAPMA
                    elif action == "make_main":
                        image_id = request.POST.get("image_id")
                        image = get_object_or_404(ProductImage, pk=image_id, group__product=product)
                        group = image.group

                        group.images.update(is_main=False)
                        image.is_main = True
                        image.save()

                        # Asıl vitrin resmini (Product.image) yardımcı metodla temiz şekilde güncelle
                        self._sync_product_main_image(product)
                        
                        return JsonResponse({"success": True, "message": "Kapak güncellendi.", "html": self._render_group_html(group)})

                    elif action == "edit_alt":
                        image_id = request.POST.get("image_id")
                        alt_text = request.POST.get("alt_text", "")
                        image = get_object_or_404(ProductImage, pk=image_id, group__product=product)
                        image.alt_text = alt_text
                        image.save(update_fields=["alt_text"])
                        return JsonResponse({"success": True, "message": "Alt metin kaydedildi.", "html": self._render_group_html(image.group)})

                    # 4. SÜRÜKLE BIRAK SIRALAMA (HTML Dönmesine Gerek Yok, Sessiz Kayıt)
                    elif action == "reorder":
                        image_ids = json.loads(request.POST.get("image_ids", "[]"))
                        images = ProductImage.objects.filter(group__product=product, pk__in=image_ids)
                        img_dict = {str(img.pk): img for img in images}
                        
                        to_update = []
                        for idx, img_id in enumerate(image_ids):
                            img = img_dict.get(str(img_id))
                            if img:
                                img.sort_order = idx
                                to_update.append(img)
                        
                        if to_update:
                            ProductImage.objects.bulk_update(to_update, ["sort_order"])
                        return JsonResponse({"success": True})
                    
                    # 5. BARKOD GÜNCELLEME (Bunda da HTML dönmez)
                    elif action == "update_barcodes_bulk":
                        return self._handle_update_barcode(request, product)

            except Exception:
                logger.exception("Katalog görseli güncellenirken hata oluştu.")
                return JsonResponse({"success": False, "message": "İşlem sırasında bir hata oluştu."}, status=400)

            return JsonResponse({"success": False, "message": "Geçersiz işlem."}, status=400)

        # --------------------------------------------------------------------
        # NORMAL FORM İŞLEMİ (AÇIKLAMA GÜNCELLEME)
        # --------------------------------------------------------------------
        form = ProductUpdateForm(request.POST, instance=product)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, f'"{product.name}" katalog açıklaması başarıyla güncellendi.')
                # Nereden geldiyse oraya geri döndür
                source_offer_id = request.POST.get("source_offer_id")
                if source_offer_id:
                    return redirect("products:offer_update", store_slug=self.get_store().slug, pk=source_offer_id)
                return redirect("products:store_product_list", store_slug=self.get_store().slug)
            except DatabaseError:
                messages.error(request, "Açıklama güncellenirken bir hata oluştu. Lütfen tekrar deneyin.")

        else:
            messages.error(request, "Lütfen formdaki hataları düzeltin.")

        context = self.get_context_data(request, product, form)    
        return render(request, self.template_name, context)

    def _render_group_html(self, group):
        """AJAX Response için güncel görsel grubunu render eder."""
        # Yeni sıralamayı ve kapak durumunu taze çekiyoruz
        group = ProductImageGroup.objects.prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.order_by("sort_order"))
        ).get(pk=group.pk)
        
        return render_to_string("products/seller/image_grid.html", {"group": group})

    def _handle_update_barcode(self, request, product):
        try:
            barcodes_json = request.POST.get("barcodes_data", "[]")
            data = json.loads(barcodes_json)
            variants_to_update = []

            # 1. Tüm varyantları bir defada RAM'e al
            variants_map = {str(v.id): v for v in product.variants.all()}
            
            with transaction.atomic():
                for item in data:
                    variant_id = item.get("variant_id")
                    # Boş string gelirse NULL (None) yap ki Unique Constraint patlamasın
                    barcode_val = item.get("barcode", "").strip() or None
                    
                    # 2. Döngü içinde DB'ye gitmek yerine RAM'den (dict) oku
                    variant = variants_map.get(variant_id)
                    if variant:
                        variant.barcode = barcode_val
                        variants_to_update.append(variant)

                # Veritabanında toplu güncelleme
                if variants_to_update:
                    ProductVariant.objects.bulk_update(variants_to_update, ["barcode"])
            return JsonResponse({"success": True, "message": "Barkodlar başarıyla güncellendi."})
        except IntegrityError:
            return JsonResponse({
                "success": False, 
                "message": "Girdiğiniz barkodlardan biri sistemdeki başka bir üründe zaten kullanılıyor!"
            }, status=400)
        except Exception as e:
            logger.exception("Barkod toplu güncelleme hatası")
            return JsonResponse({"success": False, "message": "Barkodlar güncellenirken bir hata oluştu."})


class StoreProductArchiveView(SellerRequiredMixin, StoreOwnerMixin, View):
    """
    Satıcının satış teklifini arşivler.

    Kayıt veritabanından silinmez; yalnızca durumu ARCHIVED olarak güncellenir.

    Güvenlik:
        - Sadece teklifin sahibi olan mağaza arşivleme yapabilir.
        - GET isteği desteklenmez.

    URL:
        /stores/<store_slug>/products/offer/<pk>/archive/
    """

    def get(self, request, *args, **kwargs):
        return redirect(
            "products:store_product_list",
            store_slug=self.get_store().slug,
        )

    def post(self, request, *args, **kwargs):
        store = self.get_store()
        offer = self._get_offer()

        if offer.status == StoreProductStatus.ARCHIVED:
            messages.info(
                request,
                "Bu teklif zaten arşivlenmiş."
            )
            return redirect(
                "products:store_product_list",
                store_slug=store.slug,
            )

        try:
            with transaction.atomic():
                offer.status = StoreProductStatus.ARCHIVED

                offer.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

        except DatabaseError:
            logger.exception(
                "DatabaseError while archiving store offer."
            )

            messages.error(
                request,
                "Teklif arşivlenirken bir sistem hatası oluştu. "
                "Lütfen tekrar deneyin."
            )

            return redirect(
                "products:store_product_list",
                store_slug=store.slug,
            )

        messages.success(
            request,
            f'"{offer.variant.product.name}" için satış teklifiniz başarıyla arşivlendi.'
        )

        return redirect(
            "products:store_product_list",
            store_slug=store.slug,
        )

    def _get_offer(self):
        """
        Arşivlenecek teklifi getirir.

        Store filtresi sayesinde başka mağazalara ait teklifler
        arşivlenemez (IDOR koruması).
        """
        if not hasattr(self, "_offer"):
            self._offer = get_object_or_404(
                StoreProduct.objects.select_related(
                    "variant__product",
                ),
                pk=self.kwargs["pk"],
                store=self.get_store(),
            )

        return self._offer