from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import AccessMixin
from django.contrib import messages
from django.views.generic import CreateView, ListView, UpdateView, DetailView, View
from .models import Store, StoreUpdateRequest, StoreStatus

from django.urls import reverse_lazy

from .forms import StoreForm

import json
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import JsonResponse


from products.models import ProductQuestion, ProductAnswer
from products.services.storefront import ProductQAService


class SellerRequiredMixin(AccessMixin):
    """
    Sadece SellerProfile sahibi olan kullanıcıların erişimine izin ver.
    Eğer kullanıcı giriş yapmamışsa -> Login sayfasına at.
    Giriş yapmış ama satıcı değilse, 'Satıcı Ol' sayfasına veya dashboard'a gönder.
    """
    def dispatch(self, request, *args, **kwargs):
        #Kullanıcı giriş yaptı mı?
        if not request.user.is_authenticated:
            messages.info(request, "Mağaza işlemlerine erişmek için lütfen giriş yapın.")
            return self.handle_no_permission()
        
        #Satıcı profili var mı?
        if not hasattr(request.user, 'seller_profile'):
            messages.warning(request, "Bu sayfaya erişebilmek için satıcı hesabı oluşturmalısınız.")
            return redirect('accounts:seller_form')

        return super().dispatch(request, *args, **kwargs)

class StoreCreateView(SellerRequiredMixin, CreateView):
    model = Store
    form_class = StoreForm
    template_name = 'store/create_store.html'
    success_url = reverse_lazy('store:store_list')

    def dispatch(self, request, *args, **kwargs):
        # 1. KONTROL: Eğer kullanıcı giriş yapmış ve bir satıcı profiline sahipse,
        # mağaza sayısını kontrol et. 
        # (Bu kontrolü super().dispatch'den ÖNCE yapıyoruz ki form hiç render edilmesin/işlenmesin)
        #Eğer super().dispatch'i üste koysaydık, Django önce HTML sayfasını (formu) hazırlamak için sunucuyu yoracak, sonra senin sınırına takılıp sayfayı çöpe atıp yönlendirme yapacaktı.
        if request.user.is_authenticated and hasattr(request.user, 'seller_profile'):
            seller_profile = request.user.seller_profile
            
            # 1. KONTROL: Sistemdeki arşiv dahil mutlak sınır 5 mi?
            total_stores = seller_profile.stores.count()
            if total_stores >= 5:
                messages.error(request, "Toplam mağaza sınırınıza (arşivlenenler dahil 5 adet) ulaştınız. Daha fazla mağaza açamazsınız.")
                return redirect('store:store_list')
                
            # 2. KONTROL: Aktif/Bekleyen mağaza sınırı 3 mü?
            non_archived_stores = seller_profile.stores.exclude(status=StoreStatus.ARCHIVED).count()
            if non_archived_stores >= 3:
                messages.error(request, "Maksimum aktif/bekleyen mağaza sınırına (3 adet) ulaştınız. Yeni mağaza açabilmek için mevcut mağazalarınızdan birini silmeniz (arşive almanız) gerekir.")
                return redirect('store:store_list')
                
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Form başarıyla doldurulduğunda, mağazayı oluşturan kişiyi (satıcıyı) kaydet
        form.instance.seller = self.request.user.seller_profile
        messages.success(self.request, "Mağaza oluşturma isteği alındı.")
        return super().form_valid(form)

class MyStoresListView(SellerRequiredMixin, ListView):
    model = Store
    template_name = 'store/store_list.html'
    context_object_name = 'stores'

    def get_queryset(self):
        # Mixin sayesinde buraya gelen kişinin kesinlikle seller_profile'ı vardır.
        return Store.objects.filter(seller=self.request.user.seller_profile).order_by('-created_at')

class StoreUpdateView(SellerRequiredMixin, UpdateView):
    model = Store
    form_class = StoreForm
    template_name = 'store/update_store.html'

    def get_success_url(self):
        return reverse_lazy('store:update_store', kwargs={'slug': self.object.slug})

    def get_queryset(self):
        # Kullanıcı sadece kendi mağazasını güncelleyebilsin
        return Store.objects.filter(seller=self.request.user.seller_profile)

    #Mağaza arşivlenmişse POST isteklerini reddet
    def dispatch(self, request, *args, **kwargs):
        store = self.get_object()
        if store.status == StoreStatus.ARCHIVED and request.method == 'POST':
            messages.error(request, "Arşivlenmiş bir mağazanın bilgileri değiştirilemez.")
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    # Mağaza arşivlenmişse form alanlarını kilitle
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.object.status == StoreStatus.ARCHIVED:
            for field in form.fields.values():
                field.disabled = True
        return form

    # Onay bekleyen değişikliği HTML şablonuna gönderiyoruz
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['pending_request'] = StoreUpdateRequest.objects.filter(
            store=self.object, status=StoreStatus.PENDING
        ).first()
        return context

    # Formu açtığında eski bilgileri değil, bekleyen yeni bilgileri görsün
    def get_initial(self):
        initial = super().get_initial()
        pending_request = StoreUpdateRequest.objects.filter(
            store=self.object, status=StoreStatus.PENDING
        ).first()

        if pending_request:
            if pending_request.new_store_name:
                initial['store_name'] = pending_request.new_store_name
            if pending_request.new_contact_email:
                initial['contact_email'] = pending_request.new_contact_email
            if pending_request.new_contact_phone:
                initial['contact_phone'] = pending_request.new_contact_phone
            if pending_request.new_address:
                initial['address'] = pending_request.new_address
        return initial

    def form_valid(self, form):
        store = form.instance
        #Mağaza zaten onay bekliyorsa veya reddedildiyse
        if store.status in [StoreStatus.PENDING, StoreStatus.REJECTED, StoreStatus.SUSPENDED]:
            messages.success(self.request, "Mağaza bilgileriniz güncellendi.")
            store.status = StoreStatus.PENDING
            return super().form_valid(form)
        
        #Mağaza yayındaysa
        else:
            change_request, created = StoreUpdateRequest.objects.update_or_create( #update_or_create her zaman iki değer döndürür o yüzden iki isimlendirme var
                store=store,
                status=StoreStatus.PENDING,
                defaults={     #update_or_create dictionary alıyor, o yüzden key: value sözdizimi kullanılıyor.
                    'new_store_name': form.cleaned_data.get('store_name', ''),
                    # Resimler değiştiyse al, değişmediyse None
                    'new_logo': form.cleaned_data.get('logo') or None, #stringler boş gelirse "" olarak saklanabilir ama dosyalar False olarak saklanamaz o sebeple none 
                    'new_banner': form.cleaned_data.get('banner') or None,
                    'new_contact_email': form.cleaned_data.get('contact_email', ''),
                    'new_contact_phone': form.cleaned_data.get('contact_phone', ''),
                    'new_address': form.cleaned_data.get('address', ''),
                }
            )
            
            msg = "Bekleyen değişiklik isteğiniz güncellendi. Başvurunuz onaylanana kadar eski bilgileriniz görünecektir." if not created else "Değişiklikleriniz admin onayına gönderildi. Başvurunuz onaylanana kadar eski bilgileriniz görünecektir."
            messages.info(self.request, msg)
            
            # Asıl Store modelini güncellemeden (kaydetmeden) başarı sayfasına git
            return redirect(self.get_success_url())
        
class StoreDashboardView(SellerRequiredMixin, DetailView):
    model = Store
    template_name = 'store/store_dashboard.html'
    context_object_name = 'store'

    def get_queryset(self):
        # Mixin sayesinde buraya gelen kişinin kesinlikle seller_profile'ı vardır.
        return Store.objects.filter(seller=self.request.user.seller_profile)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # İleride buraya o mağazaya ait istatistikleri ekleyeceğiz
        # context['product_count'] = self.object.products.count()
        # context['pending_orders'] = self.object.orders.filter(status='pending').count()
        return context

class StoreArchiveView(SellerRequiredMixin, View):
    def post(self, request, slug):
        # 1. Sadece giriş yapan satıcının KENDİ mağazasını bulmasını garantiye alıyoruz
        store = get_object_or_404(Store, slug=slug, seller=request.user.seller_profile)

        # SENARYO 1: Mağaza henüz hiç yayınlanmamış (Tamamen Sil)
        if store.approved_at is None:
            store_name = store.store_name # Mesajda göstermek için ismini yedeğe alıyoruz
            store.delete() # Veritabanından tamamen uçur
            messages.success(request, f"'{store_name}' adlı mağaza başvurunuz sistemden tamamen silindi.")
        # SENARYO 2: Mağaza daha önce yayınlanmış (Arşive Al)
        else:
            store.archive() # Sadece statüsünü ARCHIVED yapar
            # Varsa bekleyen ayar değiştirme isteklerini de iptal et
            StoreUpdateRequest.objects.filter(store=store, status=StoreStatus.PENDING).update(status=StoreStatus.REJECTED)
            messages.success(request, f"'{store.store_name}' adlı mağazanız başarıyla kapatılmış ve arşive alınmıştır.")

        return redirect('store:store_list')


# Sınıfı views.py dosyasının uygun bir yerine (örneğin en alta) ekleyebilirsin
class StorePublicDetailView(DetailView):
    model = Store
    template_name = 'store/store_public.html'
    context_object_name = 'store'

    def get_queryset(self):
        # Müşteriler SADECE onaylanmış ve aktif mağazaları görebilir
        return Store.objects.filter(status=StoreStatus.APPROVED, is_active=True)
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # İleride mağazaya ait ürünleri de burada context'e ekleyeceğiz
        # context['products'] = Product.objects.filter(store=self.object, is_active=True)
        return context


class StoreQuestionsListView(SellerRequiredMixin, ListView):
    """
    Mağaza Paneli -> Müşteri Soruları

    Satıcının:
    - doğrudan kendi mağazasına yöneltilen,
    - tüm satıcılara yöneltilen

    görünür sorularını listeler.

    Cevaplanma durumu bu mağaza açısından,
    görünür ProductAnswer kayıtlarından türetilir.
    """

    model = ProductQuestion
    template_name = "store/store_questions.html"
    context_object_name = "questions"
    paginate_by = 10

    def get_store(self):
        if not hasattr(self, "_store"):
            self._store = get_object_or_404(
                Store,
                slug=self.kwargs["slug"],
                seller=self.request.user.seller_profile,
            )

        return self._store

    def get_queryset(self):
        store = self.get_store()

        visible_answer_exists = ProductAnswer.objects.filter(
            question_id=OuterRef("pk"),
            store=store,
            is_visible=True,
        )

        answers_prefetch = Prefetch(
            "answers",
            queryset=(
                ProductAnswer.objects
                .filter(
                    store=store,
                    is_visible=True,
                )
                .select_related("user")
                .order_by("created_at")
            ),
        )

        return (
            ProductQuestion.objects
            .filter(
                Q(target_store=store) |
                Q(target_store__isnull=True),
                is_visible=True,
            )
            .annotate(
                has_visible_answer=Exists(
                    visible_answer_exists
                ),
            )
            .select_related(
                "product",
                "product__brand",
                "product__category",
                "variant_context",
                "user",
                "target_store",
            )
            .prefetch_related(
                answers_prefetch,
                "variant_context__attribute_values__attribute",
                "product__image_groups__images",
                "product__image_groups__visual_attribute_values",
            )
            .order_by(
                "has_visible_answer",
                "-created_at",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        store = self.get_store()

        context["store"] = store

        context["pending_count"] = (
            ProductQuestion.objects
            .filter(
                Q(target_store=store) |
                Q(target_store__isnull=True),
                is_visible=True,
            )
            .annotate(
                has_visible_answer=Exists(
                    ProductAnswer.objects.filter(
                        question_id=OuterRef("pk"),
                        store=store,
                        is_visible=True,
                    )
                ),
            )
            .filter(
                has_visible_answer=False,
            )
            .count()
        )

        return context


class StoreAnswerQuestionAPIView(SellerRequiredMixin, View):
    """
    Satıcının müşteri sorusuna cevap vermesini sağlayan API.

    target_store:
        Store A -> yalnızca Store A cevaplayabilir.
        NULL     -> tüm mağazalar cevaplayabilir.
    """

    def post(self, request, slug, question_id, *args, **kwargs):

        # =====================================================
        # STORE
        # =====================================================

        store = get_object_or_404(
            Store,
            slug=slug,
            seller=request.user.seller_profile,
        )

        # =====================================================
        # QUESTION
        # =====================================================

        # DİKKAT:
        # Burada target_store=store kullanmıyoruz.
        #
        # Çünkü target_store=NULL olan global sorular da
        # bu mağaza tarafından cevaplanabilir.

        # question = get_object_or_404(
        #     ProductQuestion,
        #     pk=question_id,
        #     is_visible=True,
        # )

        # =====================================================
        # JSON
        # =====================================================

        try:
            data = json.loads(request.body)

        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz JSON.",
                },
                status=400,
            )

        if not isinstance(data, dict):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz istek gövdesi.",
                },
                status=400,
            )

        # =====================================================
        # INPUT
        # =====================================================

        text = data.get("text")

        if not isinstance(text, str):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Cevap metni geçersiz.",
                },
                status=400,
            )

        text = text.strip()

        if not text:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Cevap metni boş olamaz.",
                },
                status=400,
            )

        # =====================================================
        # SERVICE
        # =====================================================

        try:
            answer = ProductQAService.create_answer(
                question_id=question_id,
                store=store,
                user=request.user,
                text=text,
            )

        except PermissionError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=403,
            )

        except ValueError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=400,
            )

        # =====================================================
        # RESPONSE
        # =====================================================

        return JsonResponse(
            {
                "success": True,
                "message": "Cevabınız başarıyla yayınlandı.",
                "answer": {
                    "id": answer.pk,
                    "text": answer.text,
                    "created_at": answer.created_at.strftime(
                        "%d %b %Y, %H:%M"
                    ),
                },
            },
            status=201,
        )

