from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import AccessMixin
from django.contrib import messages
from django.views.generic import CreateView, ListView, UpdateView, DetailView, View
from .models import Store, StoreUpdateRequest, StoreStatus

from django.urls import reverse_lazy

from .forms import StoreForm

import json
from urllib.parse import urlencode
from django.db.models import Exists, OuterRef, Prefetch, Q, Count
from django.http import JsonResponse, Http404

from orders.models import (
    OrderItem,
    OrderStatus,
    SubOrder,
    SubOrderStatus,
    Invoice,
    InvoiceItem,
    CargoCompany,
    RefundStatus,
)
from orders.services.order import OrderService
from orders.services.cancellation import CancellationService
from orders.services.shipping import ShippingService
from orders.services.refund import RefundService, RefundError
from orders.exceptions import (
    OrderNotFoundError, InvalidSubOrderStatusTransitionError, InvoiceAlreadyExistsError, InvoiceCreationError,
    ShippingOperationError, CancellationError
)

from products.mixins import (
    StoreOwnerMixin
)

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

class StoreOrderListView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    ListView,
):
    """
    Satıcının kendi mağazasına ait siparişlerini listeler.

    Seller tarafında ana Order yerine SubOrder temel alınır.

    URL:
        /stores/<store_slug>/orders/

    Özellikler:
        - Mağaza ownership kontrolü
        - Durum filtreleme
        - Sipariş / müşteri / ürün araması
        - Pagination
        - OrderItem prefetch
        - Durum bazlı toplam sayılar
    """

    model = SubOrder
    template_name = "store/orders/store_orders.html"
    context_object_name = "orders"
    paginate_by = 20

    # ------------------------------------------------------------------
    # ANA ORDER DURUMLARI
    # ------------------------------------------------------------------
    #
    # Sipariş checkout sırasında oluşturulduğu için Order başlangıçta
    # PENDING_PAYMENT olabilir.
    #
    # Satıcının işleme alabileceği siparişler ödeme süreci dışında
    # kalanlardır.
    #
    # EXPIRED da satıcı panelinde aktif sipariş olarak görünmemeli.
    # ------------------------------------------------------------------

    excluded_order_statuses = (
        OrderStatus.PENDING_PAYMENT,
        OrderStatus.EXPIRED,
    )

    # ------------------------------------------------------------------
    # QUERYSET
    # ------------------------------------------------------------------

    def get_queryset(self):
        store = self.get_store()

        # --------------------------------------------------------------
        # OrderItem'ların tekrar tekrar sorgulanmasını engelle.
        #
        # Template tarafında:
        #
        #     order.order_items
        #
        # şeklinde kullanacağız.
        # --------------------------------------------------------------

        order_items_prefetch = Prefetch(
            "items",
            queryset=(
                OrderItem.objects
                .order_by("pk")
            ),
            to_attr="order_items",
        )

        qs = (
            SubOrder.objects
            .filter(
                store=store,
            )
            .exclude(
                order__status__in=self.excluded_order_statuses,
            )
            .select_related(
                "order",
                "store",
            )
            .prefetch_related(
                order_items_prefetch,
            )
            .order_by(
                "-created_at",
                "-pk",
            )
        )

        # --------------------------------------------------------------
        # STATUS FILTER
        # --------------------------------------------------------------

        status = self.request.GET.get(
            "status",
            "",
        ).strip()

        if status in SubOrderStatus.values:
            qs = qs.filter(
                status=status,
            )

        # --------------------------------------------------------------
        # SEARCH
        # --------------------------------------------------------------

        query = self.request.GET.get(
            "q",
            "",
        ).strip()

        if query:
            # OrderItem tarafındaki aramayı Exists ile yapıyoruz.
            #
            # Böylece:
            #
            # SubOrder
            #   ├── Item A
            #   ├── Item B
            #   └── Item C
            #
            # gibi bir sipariş aynı sorguda 3 kere çoğalmaz.
            item_match = OrderItem.objects.filter(
                sub_order_id=OuterRef("pk"),
            ).filter(
                Q(
                    product_name_snapshot__icontains=query,
                )
                | Q(
                    sku_snapshot__icontains=query,
                )
                | Q(
                    barcode_snapshot__icontains=query,
                )
            )

            qs = qs.filter(
                Q(
                    suborder_number__icontains=query,
                )
                | Q(
                    order__order_number__icontains=query,
                )
                | Q(
                    order__shipping_full_name__icontains=query,
                )
                | Q(
                    order__customer_email__icontains=query,
                )
                | Q(
                    order__customer_phone__icontains=query,
                )
                | Exists(item_match)
            )

        return qs

    # ------------------------------------------------------------------
    # CONTEXT
    # ------------------------------------------------------------------

    def get_context_data(
        self,
        **kwargs,
    ):
        context = super().get_context_data(
            **kwargs,
        )

        store = self.get_store()

        # --------------------------------------------------------------
        # STATUS COUNTS
        # --------------------------------------------------------------
        #
        # Bunlar search sonucuna göre değil, mağazanın toplam
        # görüntülenebilir siparişlerine göre hesaplanır.
        #
        # Örneğin:
        #
        # Tümü       120
        # Bekliyor    18
        # Hazırlanan  24
        # Kargoda     31
        # Teslim      42
        # İptal        5
        #
        # --------------------------------------------------------------

        status_counts_db = (
            SubOrder.objects
            .filter(
                store=store,
            )
            .exclude(
                order__status__in=self.excluded_order_statuses,
            )
            .order_by()
            .values(
                "status",
            )
            .annotate(
                count=Count("pk"),
            )
        )

        status_counts = {
            value: 0
            for value, _label in SubOrderStatus.choices
        }

        for row in status_counts_db:
            status_counts[row["status"]] = row["count"]

        status_counts["all"] = sum(
            status_counts.values(),
        )

        # --------------------------------------------------------------
        # CURRENT FILTERS
        # --------------------------------------------------------------

        current_status = self.request.GET.get(
            "status",
            "",
        ).strip()

        if current_status not in SubOrderStatus.values:
            current_status = ""

        current_q = self.request.GET.get(
            "q",
            "",
        ).strip()

        # --------------------------------------------------------------
        # PAGINATION QUERYSTRING
        # --------------------------------------------------------------
        #
        # page parametresi çıkarılır.
        #
        # Böylece:
        #
        # ?status=pending&q=telefon&page=2
        #
        # yerine pagination linkleri:
        #
        # ?status=pending&q=telefon&page=3
        #
        # şeklinde üretilebilir.
        # --------------------------------------------------------------

        query_params = self.request.GET.copy()

        query_params.pop(
            "page",
            None,
        )

        context["pagination_query"] = urlencode(
            query_params,
        )

        # --------------------------------------------------------------
        # TEMPLATE CONTEXT
        # --------------------------------------------------------------

        context.update(
            {
                "store": store,
                "status_counts": status_counts,
                "status_choices": SubOrderStatus.choices,
                "current_status": current_status,
                "current_q": current_q,
            }
        )

        return context


class StoreOrderDetailView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Satıcının kendi mağazasına ait tek bir SubOrder'ı
    detaylı şekilde görüntülemesini sağlar.

    URL:
        /stores/<store_slug>/orders/<suborder_number>/

    Bu view:
        - Seller authorization yapmaz.
          SellerRequiredMixin + StoreOwnerMixin bunu yapar.
        - SubOrder ownership kontrolünü doğrudan yapmaz.
          OrderService.get_suborder_for_store() bunu yapar.
        - Business logic içermez.
        - Sipariş durumunu değiştirmez.
    """

    template_name = "store/orders/store_order_detail.html"

    http_method_names = [
        "get",
    ]

    def get_suborder(self):
        """
        Seller'ın erişebildiği SubOrder'ı getirir.

        Aynı request içerisinde tekrar çağrılırsa
        tekrar database sorgusu yapılmaz.
        """

        if not hasattr(self, "_suborder"):
            try:
                self._suborder = (
                    OrderService.get_suborder_for_store(
                        store=self.get_store(),
                        suborder_number=self.kwargs["suborder_number"],
                    )
                )

            except OrderNotFoundError as exc:
                raise Http404(str(exc)) from exc

        return self._suborder

    def get(
        self,
        request,
        *args,
        **kwargs,
    ):
        store = self.get_store()
        suborder = self.get_suborder()

        try:
            invoice = suborder.invoice
        except Invoice.DoesNotExist:
            invoice = None

        can_cancel = suborder.status in {
            SubOrderStatus.PENDING,
            SubOrderStatus.PREPARING,
        }

        context = {
            "store": store,
            "suborder": suborder,
            "main_order": suborder.order,
            "items": list(suborder.items.all()),
            "invoice": invoice,
            "cargo_company_choices": CargoCompany.choices,
            "can_cancel": can_cancel,
        }

        return render(
            request,
            self.template_name,
            context,
        )

class StoreOrderCancellationView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Satıcının kendi mağazasına ait SubOrder'ı iptal etmesini sağlar.

    URL:
        /stores/<store_slug>/orders/<suborder_number>/cancel/

    Sadece POST kabul edilir.

    Authorization:
        SellerRequiredMixin
            +
        StoreOwnerMixin
            +
        OrderService.get_suborder_for_store()

    Business logic:
        CancellationService.cancel_suborder()
    """

    http_method_names = ["post"]

    def post(
        self,
        request,
        *args,
        **kwargs,
    ):
        store = self.get_store()
        print("CANCEL POST:", request.POST)

        suborder_number = kwargs["suborder_number"]

        # --------------------------------------------------------------
        # SUBORDER
        # --------------------------------------------------------------

        try:
            suborder = OrderService.get_suborder_for_store(
                store=store,
                suborder_number=suborder_number,
            )

        except OrderNotFoundError as exc:
            raise Http404(str(exc)) from exc

        # --------------------------------------------------------------
        # REASON
        # --------------------------------------------------------------

        reason = request.POST.get(
            "reason",
            "",
        ).strip()

        print("CANCEL REASON:", repr(reason))

        if not reason:
            messages.error(
                request,
                "İptal nedeni belirtilmelidir.",
            )

            return redirect(
                "store:store_order_detail",
                store_slug=store.slug,
                suborder_number=suborder.suborder_number,
            )

        # --------------------------------------------------------------
        # CANCELLATION SERVICE
        # --------------------------------------------------------------
        print(
            "CALLING CANCELLATION SERVICE:",
            suborder_number,
            reason,
        )
        try:
            cancellation = (
                CancellationService.cancel_suborder(
                    suborder=suborder,
                    cancelled_by=request.user,
                    reason=reason,
                )
            )

        except CancellationError as exc:
            messages.error(
                request,
                str(exc),
            )

            return redirect(
                "store:store_order_detail",
                store_slug=store.slug,
                suborder_number=suborder.suborder_number,
            )

        # --------------------------------------------------------------
        # Cancellation transaction burada commit edilmiştir.
        #
        # Artık provider refund işlenebilir.
        # --------------------------------------------------------------

        payment_refund = cancellation.payment_refund

        if payment_refund is None:
            messages.success(
                request,
                "Sipariş başarıyla iptal edildi.",
            )

            return redirect(
                "store:store_order_detail",
                store_slug=store.slug,
                suborder_number=suborder.suborder_number,
            )

        try:
            refund = RefundService.process_refund(
                payment_refund_id=payment_refund.pk,
            )

        except RefundError as exc:
            messages.warning(
                request,
                (
                    "Sipariş iptal edildi ancak "
                    f"iade işlemi tamamlanamadı: {exc}"
                ),
            )

        else:
            if refund.status == RefundStatus.SUCCESS:
                messages.success(
                    request,
                    "Sipariş iptal edildi ve ücret başarıyla iade edildi.",
                )

            elif refund.status == RefundStatus.RECONCILIATION_REQUIRED:
                messages.warning(
                    request,
                    (
                        "Sipariş iptal edildi ancak iade sonucu "
                        "doğrulanamadı. İade kontrolü gerekiyor."
                    ),
                )

            elif refund.status == RefundStatus.PENDING:
                messages.info(
                    request,
                    (
                        "Sipariş iptal edildi. "
                        "İade işlemi başlatıldı ve işleniyor."
                    ),
                )

            elif refund.status == RefundStatus.FAILED:
                messages.error(
                    request,
                    (
                        "Sipariş iptal edildi ancak ödeme iadesi "
                        "başarısız oldu."
                    ),
                )

        return redirect(
            "store:store_order_detail",
            store_slug=store.slug,
            suborder_number=suborder.suborder_number,
        )

class StoreOrderStatusUpdateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        store = self.get_store()
        suborder_number = kwargs["suborder_number"]

        try:
            suborder = OrderService.get_suborder_for_store(
                store=store,
                suborder_number=suborder_number,
            )
        except OrderNotFoundError as exc:
            raise Http404(str(exc)) from exc

        target_status = request.POST.get("status", "").strip()

        if target_status not in SubOrderStatus.values:
            messages.error(
                request,
                "Geçersiz sipariş durumu.",
            )
        else:
            try:
                if target_status == SubOrderStatus.PREPARING:
                    OrderService.start_suborder_preparation(
                        suborder=suborder,
                    )

                elif target_status == SubOrderStatus.SHIPPED:
                    ShippingService.ship_suborder(
                        suborder=suborder,
                        cargo_company=request.POST.get(
                            "cargo_company",
                            "",
                        ),
                        cargo_tracking_number=request.POST.get(
                            "cargo_tracking_number",
                            "",
                        ),
                    )

                elif target_status == SubOrderStatus.DELIVERED:
                    ShippingService.mark_suborder_delivered(
                        suborder=suborder,
                    )

                else:
                    OrderService.transition_suborder_status(
                        suborder=suborder,
                        target_status=target_status,
                    )

            except InvalidSubOrderStatusTransitionError as exc:
                messages.error(
                    request,
                    str(exc),
                )

            except InvoiceAlreadyExistsError as exc:
                messages.error(
                    request,
                    str(exc),
                )

            except InvoiceCreationError as exc:
                messages.error(
                    request,
                    str(exc),
                )

            except ShippingOperationError as exc:
                messages.error(
                    request,
                    str(exc),
                )

            else:
                messages.success(
                    request,
                    "Sipariş durumu başarıyla güncellendi.",
                )

        return redirect(
            "store:store_order_detail",
            store_slug=store.slug,
            suborder_number=suborder.suborder_number,
        )

# class StoreInvoiceListView(
#     SellerRequiredMixin,
#     StoreOwnerMixin,
#     ListView,
# ):
#     model = Invoice
#     template_name = "store/orders/store_invoice_list.html"
#     context_object_name = "invoices"
#     paginate_by = 20

#     def get_queryset(self):
#         store = self.get_store()

#         invoice_items_prefetch = Prefetch(
#             "items",
#             queryset=InvoiceItem.objects.order_by("pk"),
#             to_attr="invoice_items",
#         )

#         qs = (
#             Invoice.objects
#             .filter(
#                 suborder__store=store,
#             )
#             .select_related(
#                 "suborder",
#                 "suborder__order",
#                 "suborder__store",
#             )
#             .prefetch_related(
#                 invoice_items_prefetch,
#             )
#             .order_by(
#                 "-issued_at",
#                 "-pk",
#             )
#         )

#         query = self.request.GET.get(
#             "q",
#             "",
#         ).strip()

#         if query:
#             item_match = (
#                 InvoiceItem.objects
#                 .filter(
#                     invoice_id=OuterRef("pk"),
#                 )
#                 .filter(
#                     Q(product_name__icontains=query)
#                     | Q(sku__icontains=query)
#                     | Q(barcode__icontains=query)
#                     | Q(variant_display__icontains=query)
#                 )
#             )

#             qs = qs.filter(
#                 Q(invoice_number__icontains=query)
#                 | Q(
#                     suborder__suborder_number__icontains=query
#                 )
#                 | Q(
#                     suborder__order__order_number__icontains=query
#                 )
#                 | Q(
#                     buyer_full_name__icontains=query
#                 )
#                 | Q(
#                     buyer_email__icontains=query
#                 )
#                 | Q(
#                     buyer_phone__icontains=query
#                 )
#                 | Exists(item_match)
#             )

#         return qs

#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)

#         store = self.get_store()

#         current_q = self.request.GET.get(
#             "q",
#             "",
#         ).strip()

#         query_params = self.request.GET.copy()
#         query_params.pop("page", None)

#         context.update({
#             "store": store,
#             "current_q": current_q,
#             "pagination_query": query_params.urlencode(),
#         })

#         return context

# class StoreInvoiceDetailView(
#     SellerRequiredMixin,
#     StoreOwnerMixin,
#     View,
# ):
#     template_name = "store/orders/store_invoice_detail.html"

#     http_method_names = [
#         "get",
#     ]

#     def get_invoice(self):
#         if not hasattr(self, "_invoice"):
#             try:
#                 self._invoice = (
#                     InvoiceService.get_invoice_for_store(
#                         store=self.get_store(),
#                         invoice_number=self.kwargs["invoice_number"],
#                     )
#                 )
#             except InvoiceNotFoundError as exc:
#                 raise Http404(str(exc)) from exc

#         return self._invoice

#     def get(self, request, *args, **kwargs):
#         store = self.get_store()
#         invoice = self.get_invoice()

#         context = {
#             "store": store,
#             "invoice": invoice,
#             "suborder": invoice.suborder,
#             "main_order": invoice.suborder.order,
#             "items": list(invoice.items.all()),
#         }

#         return render(
#             request,
#             self.template_name,
#             context,
#         )