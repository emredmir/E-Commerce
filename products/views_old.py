from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin
from django.views.generic import ListView, View
from django.db.models import Min, Count, Q, Prefetch, Sum
from django.db import DatabaseError, IntegrityError, transaction
import logging
from collections import defaultdict
from django.forms import inlineformset_factory

from store.models import Store
from .models import (
    Product, ProductVariant, ProductImage,
    StoreProduct, StoreProductStatus,
    Category, ProductStatus, ProductImageGroup
)
from .forms import (
    ProductSearchForm
)

#silinecekler 
# ProductOfferCreateView, SellerRequiredMixin, StoreOwnerMixin, ProductSearchView, ProductCreateView

# düzenlenecekler
# inventory altında -> StoreProductListView, StoreProductUpdateView, StoreProductArchiveView, ProductUpdateView
# storefront altında -> ProductListView, CategoryProductListView, ProductDetailView (belki ayrı view pysinde)

# ---------------------------------------------------------
# Mixin'ler
# ---------------------------------------------------------


class SellerRequiredMixin(AccessMixin):
    """
    Sadece onaylı satıcıların erişimine izin ver.
    store/views.py'deki ile aynı mantık, products app'i store'a
    bağımlı kılmamak için burada da tanımlandı.
    """
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(request, "Bu sayfaya erişmek için giriş yapmalısınız.")
            return self.handle_no_permission()

        if not request.user.is_seller:
            messages.warning(request, "Bu sayfaya erişmek için onaylı satıcı hesabına sahip olmalısınız.")
            return redirect('accounts:seller_form')

        return super().dispatch(request, *args, **kwargs)


class StoreOwnerMixin:
    """
    URL'den gelen store_slug'ın bu kullanıcıya ait olduğunu doğrular.
    Tüm satıcı dashboard view'larında kullanılır.
    get_store() sonucu _store'a cache'lenir — aynı request'te tek sorgu gider.
    """
    def get_store(self):
        if not hasattr(self, '_store'):
            self._store = Store.objects.filter(
                slug=self.kwargs['store_slug'],
                seller=self.request.user.seller_profile,
                status='approved'
            ).first()
        return self._store

    def dispatch(self, request, *args, **kwargs):
        if not self.get_store():
            messages.error(request, "Mağaza bulunamadı veya bu mağazaya erişim yetkiniz yok.")
            return redirect('store:store_list')
        return super().dispatch(request, *args, **kwargs)

# ---------------------------------------------------------
# Satıcı Dashboard View'ları
# ---------------------------------------------------------

# class StoreProductListView(SellerRequiredMixin, StoreOwnerMixin, ListView):
#     """
#     Satıcının mağazasındaki teklifleri listeler.
#     Tek SQL sorgusuyla durum sayılarını hesaplar, arama ve filtreleme destekler.

#     URL: /stores/<store_slug>/products/
#     """
#     model = StoreProduct
#     template_name = 'products/seller/store_product_list.html'
#     context_object_name = 'store_products'
#     paginate_by = 20

#     def get_queryset(self):
#         qs = (
#             StoreProduct.objects
#             .filter(store=self.get_store())
#             .select_related('variant__product')
#             .prefetch_related('variant__attribute_values')
#             .order_by('-updated_at')
#         )

#         # Durum filtresi (?status=active)
#         status = self.request.GET.get('status')
#         if status in StoreProductStatus.values:
#             qs = qs.filter(status=status)

#         # Ürün adı araması (?q=iphone)
#         q = self.request.GET.get('q', '').strip()
#         if q:
#             qs = qs.filter(variant__product__name__icontains=q)

#         return qs

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         store = self.get_store()  # cache'li, ekstra sorgu gitmez
#         context['store'] = store

#         # Tek sorguda tüm durum sayıları
#         status_counts_db = (
#             StoreProduct.objects
#             .filter(store=store)
#             .values('status')
#             .annotate(count=Count('id'))
#         )

#         # Önce tüm durumları sıfırla — veritabanında kaydı olmayan durum 0 görünsün
#         status_counts = {status[0]: 0 for status in StoreProductStatus.choices}

#         # Veritabanından gelen dolu olanları eşle
#         for item in status_counts_db:
#             status_counts[item['status']] = item['count']

#         # 'all' toplamı Python'da hesapla — ekstra sorgu yok
#         status_counts['all'] = sum(status_counts.values())

#         context['status_counts'] = status_counts
#         context['current_status'] = self.request.GET.get('status', '')
#         context['current_q'] = self.request.GET.get('q', '')
#         context['status_choices'] = StoreProductStatus.choices

#         return context


class ProductSearchView(SellerRequiredMixin, StoreOwnerMixin, View):
    """
    Satıcının katalogda ürün aradığı view.
    GET parametresiyle çalışır — URL paylaşılabilir, geri tuşu çalışır.
    Sonuç varsa → Her ürünün yanında varyant bazlı 'Ben de Satıyorum' butonu gösterilir.
    Sonuç yoksa → 'Yeni Ürün Ekle' butonu gösterilir.
    """
    template_name = 'products/seller/product_search.html'

    def get(self, request, store_slug):
        store = self.get_store()
        form = ProductSearchForm(request.GET or None)
        
        context = {
            'form': form,
            'store': store,
            'results': [],
            'searched': False,
            'has_results': False,
            'store_variant_ids': set()
        }

        if not form.is_valid():
            return render(request, self.template_name, context)

        query = form.cleaned_data.get('query')
        if not query:
            return render(request, self.template_name, context)

        context['searched'] = True
        context['query'] = query

        # Kapak fotoğrafını önceden çekmek için temiz Prefetch
        main_image_prefetch = Prefetch(
            'images',
            queryset=ProductImage.objects.filter(is_main=True),
            to_attr='main_images'
        )

        # Varyantları ve özelliklerini çekmek için Prefetch
        active_variants_prefetch = Prefetch(
            'variants',
            queryset=ProductVariant.objects.filter(is_active=True).prefetch_related('attribute_values'),
            to_attr='active_variants'
        )

        # Ürün adı veya varyant barkoduyla eşleşen aktif ürünler
        results = (
            Product.objects
            .filter(
                Q(name__icontains=query) | Q(variants__barcode__icontains=query),
                status=ProductStatus.ACTIVE,
            )
            .distinct()
            .select_related('category', 'brand')
            .prefetch_related(main_image_prefetch, active_variants_prefetch)
            .annotate(
                # Buy Box önizlemesi için en düşük aktif teklif fiyatı
                min_price=Min(
                    'variants__store_offers__price',
                    filter=Q(variants__store_offers__status=StoreProductStatus.ACTIVE)
                )
            )
            .order_by('name')
        )

        # O(1) hızında template içi kontrol için satıcının mevcut aktif tekliflerinin ID'leri
        # Arşivlenmiş olanları hariç tutuyoruz ki satıcı sildiği ürüne tekrar teklif verebilsin
        store_variant_ids = set(
            StoreProduct.objects
            .filter(store=store)
            .exclude(status=StoreProductStatus.ARCHIVED)
            .values_list('variant_id', flat=True)
        )

        context['results'] = results
        context['has_results'] = results.exists()
        context['store_variant_ids'] = store_variant_ids

        return render(request, self.template_name, context)


logger = logging.getLogger(__name__)


# class ProductCreateView(SellerRequiredMixin, StoreOwnerMixin, View):
#     """
#     Global kataloğa yeni bir ürün ekler.

#     Oluşturulan kayıtlar:

#         Product
#             └── ProductVariant
#                     └── ProductImage (opsiyonel)

#     Bu view mağazaya teklif oluşturmaz.
#     Başarılı kayıttan sonra kullanıcı teklif oluşturma sayfasına yönlendirilir.

#     Tüm kayıtlar tek transaction içerisinde oluşturulur.
#     """

#     template_name = "products/seller/product_create.html"

#     def get(self, request, *args, **kwargs):
#         context = self._get_forms()
#         context["store"] = self.get_store()
#         return render(request, self.template_name, context)

#     def post(self, request, *args, **kwargs):
#         store = self.get_store()
#         forms = self._get_forms(
#             data=request.POST,
#             files=request.FILES,
#         )

#         product_form = forms["product_form"]
#         variant_form = forms["variant_form"]
#         image_form = forms["image_form"]

#         # Short-circuit oluşmaması için tüm formlar ayrı ayrı doğrulanır.
#         product_valid = product_form.is_valid()
#         variant_valid = variant_form.is_valid()
#         image_valid = image_form.is_valid()

#         if not (product_valid and variant_valid and image_valid):
#             messages.error(
#                 request,
#                 "Lütfen formdaki hataları düzeltin."
#             )
#             print(product_form.errors)
#             print(variant_form.errors)
#             print(image_form.errors)

#             forms["store"] = store
#             return render(request, self.template_name, forms)

#         try:
#             with transaction.atomic():

#                 # Product
#                 product = product_form.save()

#                 # Variant
#                 variant = variant_form.save(commit=False)
#                 variant.product = product
#                 variant.save()

#                 variant_form.save_m2m()

#                 # Product Image (opsiyonel)
#                 if image_form.cleaned_data.get("image"):
#                     product_image = image_form.save(commit=False)
#                     product_image.product = product

#                     # Görsel ürünün geneline aittir.
#                     # Varyanta özel olması istenirse:
#                     # product_image.variant = variant

#                     product_image.save()

#         except IntegrityError:
#             logger.exception(
#                 "IntegrityError while creating product."
#             )

#             messages.error(
#                 request,
#                 "Ürün oluşturulurken bir veri çakışması oluştu. "
#                 "Lütfen tekrar deneyin."
#             )

#             forms["store"] = store
#             return render(request, self.template_name, forms)

#         except DatabaseError:
#             logger.exception(
#                 "DatabaseError while creating product."
#             )

#             messages.error(
#                 request,
#                 "Şu anda ürün kaydedilemiyor. "
#                 "Lütfen birkaç dakika sonra tekrar deneyin."
#             )

#             forms["store"] = store
#             return render(request, self.template_name, forms)

#         messages.success(
#             request,
#             f'"{product.name}" başarıyla oluşturuldu. '
#             "Şimdi fiyat ve stok bilgilerinizi girerek satış teklifinizi oluşturabilirsiniz."
#         )

#         return redirect(
#             "products:offer_create",
#             store_slug=store.slug,
#             variant_id=variant.pk,
#         )

#     def _get_forms(self, data=None, files=None):
#         """
#         Product oluşturma sayfasındaki formları hazırlar.

#         Offer (fiyat/stok) oluşturma işlemi bu view'ın sorumluluğunda değildir.
#         """
#         return {
#             "product_form": ProductForm(data=data),
#             "variant_form": ProductVariantForm(data=data),
#             "image_form": ProductImageForm(data=data, files=files),
#         }


# class ProductOfferCreateView(SellerRequiredMixin, StoreOwnerMixin, View):
#     """
#     Belirli bir varyant için mağazaya satış teklifi (StoreProduct) oluşturur.

#     İki noktadan tetiklenir:
#         1. ProductCreateView sonrası — yeni eklenen ürün için
#         2. ProductSearchView'dan — mevcut bir ürüne 'Ben de Satıyorum' ile

#     Varyant URL'den geldiği için StoreProductForm'daki 'variant' alanı
#     formdan çıkarılır, kullanıcıya salt-okunur bilgi olarak gösterilir.

#     URL: /stores/<store_slug>/products/offer/create/<variant_id>/
#     """
#     template_name = "products/seller/offer_create.html"

#     def get_variant(self):
#         """URL'deki varyantı tek sorguyla getirir ve istek boyunca cache'ler."""
#         if not hasattr(self, '_variant'):
#             self._variant = get_object_or_404(
#                 ProductVariant.objects
#                     .select_related('product')
#                     .prefetch_related('attribute_values'),
#                 pk=self.kwargs['variant_id'],
#                 status=ProductStatus.ACTIVE,
#                 product__is_active=True,
#             )
#         return self._variant

#     def get_existing_offer(self, store, variant):
#         """Aynı mağazanın aynı varyant için aktif bir teklifi var mı?"""
#         return StoreProduct.objects.filter(
#             store=store, variant=variant
#         ).exclude(status=StoreProductStatus.ARCHIVED).first()

#     def build_form(self, data=None):
#         """Teklif formunu hazırlar ve variant alanını çıkarır."""
#         form = StoreProductForm(data=data, store=self.get_store())
#         # 'None' fallback'i ile KeyError riskini sıfıra indiriyoruz
#         form.fields.pop('variant', None) 
#         return form

#     def get(self, request, *args, **kwargs):
#         store = self.get_store()
#         variant = self.get_variant()

#         existing = self.get_existing_offer(store, variant)
#         if existing:
#             messages.info(request, "Bu varyant için zaten bir teklifiniz var.")
#             return redirect('products:offer_update', store_slug=store.slug, pk=existing.pk)

#         context = {
#             'store': store,
#             'variant': variant,
#             'form': self.build_form(),
#         }
#         return render(request, self.template_name, context)

#     def post(self, request, *args, **kwargs):
#         store = self.get_store()
#         variant = self.get_variant()

#         form = self.build_form(data=request.POST)

#         if not form.is_valid():
#             messages.error(request, "Lütfen formdaki hataları düzeltin.")
#             context = {'store': store, 'variant': variant, 'form': form}
#             return render(request, self.template_name, context)

#         try:
#             with transaction.atomic():
#                 offer = form.save(commit=False)
#                 offer.store = store
#                 offer.variant = variant
#                 offer.save()

#         except IntegrityError:
#             logger.exception("IntegrityError while creating store offer.")
#             # Race condition: aynı anda başka bir istek bu varyant için teklif oluşturmuş olabilir
#             existing = self.get_existing_offer(store, variant)
#             if existing:
#                 messages.warning(request, "Bu varyant için teklif az önce oluşturulmuş görünüyor.")
#                 return redirect('products:offer_update', store_slug=store.slug, pk=existing.pk)
            
#             messages.error(request, "Teklif oluşturulurken bir veri çakışması oluştu. Lütfen tekrar deneyin.")
#             context = {'store': store, 'variant': variant, 'form': form}
#             return render(request, self.template_name, context)

#         except DatabaseError:
#             logger.exception("DatabaseError while creating store offer.")
#             messages.error(request, "Şu anda teklif kaydedilemiyor. Lütfen tekrar deneyin.")
#             context = {'store': store, 'variant': variant, 'form': form}
#             return render(request, self.template_name, context)

#         messages.success(request, f'"{variant.product.name}" için teklifiniz oluşturuldu.')
#         return redirect('products:store_product_list', store_slug=store.slug)


# class StoreProductUpdateView(SellerRequiredMixin, StoreOwnerMixin, View): 
#     """ 
#     Satıcının mevcut satış teklifini günceller. 

#     Sadece mağazaya ait bilgiler güncellenebilir. 
#     (Fiyat, stok, SKU, durum, notlar vb.) 

#     Varyant değiştirilemez. 

#     URL: 
#         /stores/<store_slug>/products/offer/<pk>/update/ 
#     """ 

#     template_name = "products/seller/offer_update.html" 


#     def get(self, request, *args, **kwargs): 
#         store = self.get_store() 
#         offer = self._get_offer() 

#         return render( 
#             request, 
#             self.template_name, 
#             { 
#                 "store": store, 
#                 "offer": offer, 
#                 "form": self._get_form(instance=offer), 
#             }, 
#         ) 

#     def post(self, request, *args, **kwargs): 
#         store = self.get_store() 
#         offer = self._get_offer() 

#         form = self._get_form( 
#             data=request.POST, 
#             instance=offer, 
#         ) 

#         if not form.is_valid(): 
#             messages.error( 
#                 request, 
#                 "Lütfen formdaki hataları düzeltin." 
#             ) 

#             return render( 
#                 request, 
#                 self.template_name, 
#                 { 
#                     "store": store, 
#                     "offer": offer, 
#                     "form": form, 
#                 }, 
#             ) 

#         try: 
#             with transaction.atomic(): 
#                 offer = form.save() 

#         except IntegrityError: 
#             logger.exception( 
#                 "IntegrityError while updating store offer." 
#             ) 

#             messages.error( 
#                 request, 
#                 "Teklif güncellenirken bir veri çakışması oluştu. " 
#                 "Lütfen tekrar deneyin." 
#             ) 

#             return render( 
#                 request, 
#                 self.template_name, 
#                 { 
#                     "store": store, 
#                     "offer": offer, 
#                     "form": form, 
#                 }, 
#             ) 

#         except DatabaseError: 
#             logger.exception( 
#                 "DatabaseError while updating store offer." 
#             ) 

#             messages.error( 
#                 request, 
#                 "Şu anda teklif güncellenemiyor. " 
#                 "Lütfen birkaç dakika sonra tekrar deneyin." 
#             ) 

#             return render( 
#                 request, 
#                 self.template_name, 
#                 { 
#                     "store": store, 
#                     "offer": offer, 
#                     "form": form, 
#                 }, 
#             ) 

#         messages.success( 
#             request, 
#             f'"{offer.variant.product.name}" için satış teklifiniz başarıyla güncellendi.' 
#         ) 

#         return redirect( 
#             "products:store_product_list", 
#             store_slug=store.slug, 
#         ) 

#     def _get_offer(self): 
#         """ 
#         Güncellenecek teklifi getirir. 

#         Store filtresi sayesinde başka mağazaların teklifleri 
#         düzenlenemez (IDOR koruması). 
#         """ 
#         if not hasattr(self, "_offer"): 
#             self._offer = get_object_or_404( 
#                 StoreProduct.objects.select_related( 
#                     "variant__product", "variant__product__category", "variant__product__brand"
#                 ).prefetch_related("variant__attribute_values", "variant__attribute_values__attribute", 
#                                    Prefetch(
#                                         "variant__product__images",
#                                         queryset=ProductImage.objects.filter(is_main=True),
#                                         to_attr="main_images"
#                                     ),
#                 ), 
#                 pk=self.kwargs["pk"], 
#                 store=self.get_store(), 
#             ) 

#         return self._offer 

#     def _get_form(self, data=None, instance=None): 
#         """ 
#         Teklif güncelleme formunu oluşturur. 
#         """ 
#         return StoreProductUpdateForm( 
#             data=data, 
#             instance=instance, 
#         )

# ProductImageFormSet = inlineformset_factory(
#     Product,
#     ProductImage,
#     fields=['image', 'variant', 'is_main', 'sort_order'],
#     extra=3,
#     can_delete=True,
# )

# class ProductUpdateView(SellerRequiredMixin, StoreOwnerMixin, View):
#     """
#     Satıcının kendi eklediği ürünün açıklama ve görsellerini günceller.
#     Başlık değiştirilemez — katalog bütünlüğü korunur.

#     URL: /stores/<store_slug>/products/<product_slug>/edit/
#     """
#     template_name = "products/seller/product_update.html"

#     def get_product(self):
#         if not hasattr(self, '_product'):
#             self._product = get_object_or_404(
#                 Product.objects.prefetch_related(
#                     "image_groups__images",
#                     'variants',
#                 ),
#                 slug=self.kwargs['product_slug'],
#                 status=ProductStatus.ACTIVE,
#             )
#         return self._product

#     def get(self, request, *args, **kwargs):
#         store = self.get_store()
#         product = self.get_product()
#         form = ProductForm(instance=product)
#         context = {
#             'store': store,
#             'product': product,
#             'form': form,
#             # 'formset': formset,
#         }
#         return render(request, self.template_name, context)

#     def post(self, request, *args, **kwargs):
#         store = self.get_store()
#         product = self.get_product()
#         form = ProductForm(request.POST, instance=product)


#         form_ok = form.is_valid()

#         messages.success(request, f'"{product.name}" başarıyla güncellendi.')
#         return redirect(
#             'products:store_product_list',
#             store_slug=store.slug,
#         )


# class StoreProductArchiveView(SellerRequiredMixin, StoreOwnerMixin, View):
#     """
#     Satıcının satış teklifini arşivler.

#     Kayıt veritabanından silinmez; yalnızca durumu ARCHIVED olarak güncellenir.

#     Güvenlik:
#         - Sadece teklifin sahibi olan mağaza arşivleme yapabilir.
#         - GET isteği desteklenmez.

#     URL:
#         /stores/<store_slug>/products/offer/<pk>/archive/
#     """

#     def get(self, request, *args, **kwargs):
#         return redirect(
#             "products:store_product_list",
#             store_slug=self.get_store().slug,
#         )

#     def post(self, request, *args, **kwargs):
#         store = self.get_store()
#         offer = self._get_offer()

#         if offer.status == StoreProductStatus.ARCHIVED:
#             messages.info(
#                 request,
#                 "Bu teklif zaten arşivlenmiş."
#             )
#             return redirect(
#                 "products:store_product_list",
#                 store_slug=store.slug,
#             )

#         try:
#             with transaction.atomic():
#                 offer.status = StoreProductStatus.ARCHIVED

#                 offer.save(
#                     update_fields=[
#                         "status",
#                         "updated_at",
#                     ]
#                 )

#         except DatabaseError:
#             logger.exception(
#                 "DatabaseError while archiving store offer."
#             )

#             messages.error(
#                 request,
#                 "Teklif arşivlenirken bir sistem hatası oluştu. "
#                 "Lütfen tekrar deneyin."
#             )

#             return redirect(
#                 "products:store_product_list",
#                 store_slug=store.slug,
#             )

#         messages.success(
#             request,
#             f'"{offer.variant.product.name}" için satış teklifiniz başarıyla arşivlendi.'
#         )

#         return redirect(
#             "products:store_product_list",
#             store_slug=store.slug,
#         )

#     def _get_offer(self):
#         """
#         Arşivlenecek teklifi getirir.

#         Store filtresi sayesinde başka mağazalara ait teklifler
#         arşivlenemez (IDOR koruması).
#         """
#         if not hasattr(self, "_offer"):
#             self._offer = get_object_or_404(
#                 StoreProduct.objects.select_related(
#                     "variant__product",
#                 ),
#                 pk=self.kwargs["pk"],
#                 store=self.get_store(),
#             )

#         return self._offer


# ---------------------------------------------------------
# Müşteri Vitrini View'ları
# ---------------------------------------------------------

# class ProductListView(ListView):
#     """
#     Müşteri vitrini.

#     Sadece satın alınabilir (aktif ve stokta bulunan) en az bir teklife sahip
#     ürünleri listeler.

#     Desteklenen filtreler:
#         - Kategori
#         - Marka
#         - Fiyat aralığı

#     Desteklenen sıralamalar:
#         - En düşük fiyat
#         - En yüksek fiyat
#         - En çok satan
#         - En yeni
#     """

#     model = Product
#     template_name = "products/public/product_list.html"
#     context_object_name = "products"
#     paginate_by = 24

#     ORDERING_MAP = {
#         "min_price": "min_price",
#         "-min_price": "-min_price",
#         "-sold": "-total_sold",
#         "-created": "-created_at",
#     }

#     def get_queryset(self):
#         main_images_qs = (
#             ProductImageGroup.objects
#             .filter(
#                 is_active=True,
#                 visual_attribute_values__isnull=True,
#                 images__is_main=True,
#             )
#             .prefetch_related(
#                 Prefetch(
#                     "images",
#                     queryset=ProductImage.objects.filter(is_main=True),
#                     to_attr="main_images",
#                 )
#             )
#             .order_by("sort_order", "id")
#         )
#         queryset = (
#             Product.objects
#             .filter(
#                 status=ProductStatus.ACTIVE,
#                 variants__is_active=True,
#                 variants__store_offers__status=StoreProductStatus.ACTIVE,
#                 variants__store_offers__stock__gt=0,
#                 variants__store_offers__store__is_active=True,
#             )
#             .distinct()
#             .select_related("category", "brand")
#             .prefetch_related(
#                 Prefetch(
#                     "image_groups",
#                     queryset=main_images_qs,
#                     to_attr="active_image_groups",
#                 )
#             )
#             .annotate(
#                 min_price=Min(
#                     "variants__store_offers__price",
#                     filter=Q(
#                         variants__store_offers__status=StoreProductStatus.ACTIVE,
#                         variants__store_offers__stock__gt=0,
#                         variants__store_offers__store__is_active=True,
#                     ),
#                 ),
#                 total_sold=Sum(
#                     "variants__store_offers__sold_count",
#                     filter=Q(
#                         variants__store_offers__status=StoreProductStatus.ACTIVE,
#                     ),
#                 ),
#             )
#         )

#         form = self.get_filter_form()

#         if form.is_valid():
#             category = form.cleaned_data.get("category")
#             brand = form.cleaned_data.get("brand")
#             min_price = form.cleaned_data.get("min_price")
#             max_price = form.cleaned_data.get("max_price")
#             ordering = form.cleaned_data.get("ordering")

#             if category:
#                 queryset = queryset.filter(
#                     category_id__in=self._get_category_descendants(category)
#                 )

#             if brand:
#                 queryset = queryset.filter(
#                     brand=brand
#                 )

#             if min_price is not None:
#                 queryset = queryset.filter(
#                     min_price__gte=min_price
#                 )

#             if max_price is not None:
#                 queryset = queryset.filter(
#                     min_price__lte=max_price
#                 )

#             queryset = queryset.order_by(
#                 self.ORDERING_MAP.get(ordering, "-total_sold")
#             )

#         else:
#             queryset = queryset.order_by("-created_at")

#         return queryset

#     def get_filter_form(self):
#         """
#         Filtre formunu cache'ler.

#         Böylece aynı request içerisinde hem get_queryset() hem de
#         get_context_data() tarafından yeniden oluşturulmaz.
#         """
#         if not hasattr(self, "_filter_form"):
#             self._filter_form = ProductFilterForm(
#                 self.request.GET or None
#             )

#         return self._filter_form

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)

#         context["filter_form"] = self.get_filter_form()

#         context["active_filters"] = {
#             key: value
#             for key, value in self.request.GET.items()
#             if value and key not in {"page", "ordering"}
#         }

#         return context

#     def _get_category_descendants(self, category):
#         """
#         Verilen kategorinin kendisi ve tüm aktif alt kategorilerinin
#         ID listesini döndürür.

#         Örnek:
#             Elektronik ->
#             [Elektronik, Telefon, Laptop, Tablet]
#         """
#         ids = [category.pk]

#         children = list(
#             category.children
#             .filter(is_active=True)
#             .values_list("pk", flat=True)
#         )

#         while children:
#             ids.extend(children)

#             children = list(
#                 Category.objects
#                 .filter(
#                     parent_id__in=children,
#                     is_active=True,
#                 )
#                 .values_list("pk", flat=True)
#             )

#         return ids


# class CategoryProductListView(ProductListView):
#     """
#     Belirli bir kategoriye ait ürünleri listeler.

#     Alt kategorilerde bulunan ürünler de otomatik olarak listeye dahil edilir.

#     ProductListView'dan miras aldığı için filtreleme, sıralama,
#     sayfalama ve Buy Box mantığı aynen korunur.

#     URL:
#         /products/category/<slug:slug>/
#     """

#     template_name = "products/public/category_product_list.html"

#     def get_category(self):
#         """
#         URL'deki slug ile kategoriyi getirir ve cache'ler.
#         """
#         if not hasattr(self, "_category"):
#             self._category = get_object_or_404(
#                 Category.objects.select_related("parent"),
#                 slug=self.kwargs["slug"],
#                 is_active=True,
#             )

#         return self._category

#     def get_queryset(self):
#         """
#         ProductListView queryset'ini alır ve yalnızca seçilen
#         kategori ile alt kategorilerine ait ürünleri döndürür.
#         """
#         category = self.get_category()

#         return (
#             super()
#             .get_queryset()
#             .filter(
#                 category_id__in=self._get_category_descendants(category)
#             )
#         )

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)

#         category = self.get_category()

#         context["category"] = category
#         context["breadcrumbs"] = self._get_breadcrumbs(category)

#         context["subcategories"] = (
#             category.children
#             .filter(is_active=True)
#             .annotate(
#                 product_count=Count(
#                     "products",
#                     filter=Q(
#                         products__is_active=True,
#                         products__variants__is_active=True,
#                         products__variants__store_offers__status=StoreProductStatus.ACTIVE,
#                         products__variants__store_offers__stock__gt=0,
#                         products__variants__store_offers__store__is_active=True,
#                     ),
#                     distinct=True,
#                 )
#             )
#             .order_by("name")
#         )

#         return context

#     def _get_breadcrumbs(self, category):
#         """
#         Kategoriden köke doğru breadcrumb zinciri oluşturur.

#         Örnek:
#             Elektronik > Telefon > Akıllı Telefon
#         """
#         breadcrumbs = []
#         current = category

#         while current:
#             breadcrumbs.append(current)
#             current = current.parent

#         return list(reversed(breadcrumbs))


class ProductDetailView(View):
    """
    Müşteri vitrini — ürün detay sayfası.

    Özellikler:
        - URL üzerinden varyant seçimi desteklenir (?variant=<pk>).
        - Seçilen varyanta ait görseller gösterilir.
        - Varyanta özel görsel yoksa ürünün genel görselleri kullanılır.
        - Buy Box (en uygun satın alınabilir teklif) gösterilir.
        - Buy Box dışındaki teklifler "Diğer Satıcılar" bölümünde listelenir.

    URL:
        /products/<slug:slug>/
    """

    template_name = "products/public/product_detail.html"

    def get_product(self):
        """
        Ürünü, görsellerini ve aktif varyantlarını tek seferde yükler.
        """
        if not hasattr(self, "_product"):
            self._product = get_object_or_404(
                Product.objects
                .select_related("category", "brand")
                .prefetch_related(
                    Prefetch(
                        "images",
                        queryset=ProductImage.objects.order_by("sort_order"),
                        to_attr="all_images",
                    ),
                    Prefetch(
                        "variants",
                        queryset=(
                            ProductVariant.objects
                            .filter(status=ProductStatus.ACTIVE)
                            .prefetch_related("attribute_values__attribute")
                        ),
                        to_attr="active_variants",
                    ),
                ),
                slug=self.kwargs["slug"],
                is_active=True,
            )

        return self._product

    def get_selected_variant(self, product):
        """
        URL'deki ?variant=<pk> parametresini okur.

        Parametre yoksa veya ürünün aktif varyantlarından biri değilse
        None döner.
        """
        variant_pk = self.request.GET.get("variant")

        if not variant_pk:
            return None

        return next(
            (
                variant
                for variant in product.active_variants
                if str(variant.pk) == variant_pk
            ),
            None,
        )

    def get_offers(self, product, selected_variant=None):
        """
        Satın alınabilir teklifleri getirir.

        StoreProductQuerySet.purchasable() sayesinde yalnızca:
            - aktif teklifler
            - stokta olan teklifler
            - aktif mağazalara ait teklifler

        döndürülür.
        """

        queryset = (
            StoreProduct.objects
            .purchasable()
            .filter(variant__product=product)
            .select_related(
                "store",
                "variant",
                "variant__product",
            )
            .prefetch_related("variant__attribute_values")
            .order_by("price")
        )

        if selected_variant:
            queryset = queryset.filter(variant=selected_variant)

        return list(queryset)

    def _get_display_images(self, product, selected_variant):
        """
        Gösterilecek görselleri belirler.

        Öncelik sırası:

            1. Seçili varyanta ait görseller
            2. Ürünün genel görselleri
            3. Üründeki tüm görseller
        """

        if selected_variant:
            variant_images = [
                image
                for image in product.all_images
                if image.variant_id == selected_variant.pk
            ]

            if variant_images:
                return variant_images

        general_images = [
            image
            for image in product.all_images
            if image.variant_id is None
        ]

        return general_images or product.all_images

    def _get_variant_attributes(self, product):
        """
        Template'deki varyant seçici için attribute -> value yapısını oluşturur.

        Örnek:

        {
            "Renk": [Kırmızı, Mavi],
            "Beden": [S, M, L],
        }
        """

        attributes = defaultdict(dict)

        for variant in product.active_variants:
            for attribute_value in variant.attribute_values.all():
                attributes[
                    attribute_value.attribute.name
                ][attribute_value.pk] = attribute_value

        return {
            name: list(values.values())
            for name, values in attributes.items()
        }

    def get(self, request, *args, **kwargs):
        product = self.get_product()

        selected_variant = self.get_selected_variant(product)

        offers = self.get_offers(
            product,
            selected_variant,
        )

        buy_box_offer = offers[0] if offers else None
        other_offers = offers[1:] if offers else []

        context = {
            "product": product,
            "selected_variant": selected_variant,
            "buy_box_offer": buy_box_offer,
            "other_offers": other_offers,
            "display_images": self._get_display_images(
                product,
                selected_variant,
            ),
            "variant_attributes": self._get_variant_attributes(product),
            "has_offers": bool(offers),
        }

        return render(
            request,
            self.template_name,
            context,
        )