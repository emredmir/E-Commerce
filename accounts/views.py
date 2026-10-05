from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout, get_backends
from .forms import CustomUserCreationForm, CustomAuthenticationForm, CustomPasswordChangeForm, ProfileUpdateForm, AddressForm, BecomeASellerForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import CustomUser, Address, SellerProfile, SellerProfileUpdateRequest
from django.contrib.auth.views import PasswordChangeView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView, FormView, DetailView, ListView, TemplateView
from django.views import View
from django.http import JsonResponse
from django.template.loader import render_to_string
import json
import logging

from datetime import timedelta

from django.utils import timezone

from products.services.storefront_offers import StorefrontOfferService
from products.services.storefront import ProductQAService
from django.db.models.functions import Coalesce


from django.db.models import Count, F, Prefetch, Window, Sum, Subquery, DecimalField, IntegerField, OuterRef, Exists, Q, Max
from django.db.models.functions import RowNumber

from products.models import ProductCollection, ProductCollectionItem, ProductQuestion, ProductAnswer
from cart.services.cart import CartService
from orders.services.card_storage import (
    CardStorageService,
    StoredCardCreateData,
)

from orders.exceptions import (
    CardStorageConsistencyError,
    CardStorageGatewayError,
    CardStorageOperationInProgressError,
    PaymentValidationError,
    StoredCardNotFoundError,
)

from orders.models import Order, SubOrder, OrderItem, PaymentStatus, PaymentTransaction, SubOrderStatus

logger = logging.getLogger(__name__)

#TODO: view fazla büyüdü ayırabilirsin.

def register_view(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()

            # Kullanıcıya backend tanımla (sadece bir backend kullanıyorsan bu güvenlidir)
            backend = get_backends()[0]  # İlk backend: EmailOrPhoneBackend
            user.backend = f"{backend.__module__}.{backend.__class__.__name__}"

            # Login olmadan hemen önce sepeti birleştir
            CartService.merge_guest_cart(request, user)

            login(request, user)
            messages.success(request, "Kayıt başarılı!")
            return redirect('core:home')  # Ana sayfaya yönlendir
    else:
        form = CustomUserCreationForm()
    return render(request, 'accounts/register.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            # email = form.cleaned_data.get('username')
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                # Login olmadan hemen önce sepeti birleştir
                CartService.merge_guest_cart(request, user)
                login(request, user)
                messages.success(request, "Giriş başarılı!")
                return redirect('core:home')
            else:
                messages.error(request, "Giriş bilgileri hatalı.")
        else:
            messages.error(request, "Form geçersiz. Lütfen tekrar deneyin.")
    else:
        form = CustomAuthenticationForm()
    return render(request, 'accounts/login.html', {'form': form})

@login_required
def logout_view(request):
    logout(request)
    messages.success(request, "Çıkış yapıldı.")
    return redirect('core:home')

@login_required 
def profile_view(request): 
    return render(request, 'accounts/profile.html') 

class CustomPasswordChangeView(LoginRequiredMixin, SuccessMessageMixin, PasswordChangeView):
    form_class = CustomPasswordChangeForm
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('accounts:password_change')  # Aynı sayfada kalır
    success_message = "Şifreniz başarıyla değiştirildi."

    def form_invalid(self, form):
        messages.error(self.request, "Lütfen formu doğru doldurduğunuzdan emin olun.")
        return super().form_invalid(form)
    

class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = CustomUser
    form_class = ProfileUpdateForm
    template_name = 'accounts/profile_update.html'
    success_url = reverse_lazy('accounts:profile_update')  # Kendi profil sayfanın URL adı

    def get_object(self):
        # Giriş yapan kullanıcıyı döner
        return self.request.user

    def form_valid(self, form):
        # E-posta değişti mi kontrolü (İleride ekleyeceğiniz onay süreci için)
        # if 'email' in form.changed_data:
        #     # E-posta onayı için gerekli işlemleri burada yapın
        #     # Örneğin, bir onay e-postası gönderebilirsiniz.
        #     # Bu aşamada e-postayı hemen kaydetmeyebilirsiniz.

        messages.success(self.request, "Profil bilgileriniz başarıyla güncellendi.")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "Formda hatalar var. Lütfen kontrol edin.")
        return super().form_invalid(form)



class AddressListView(LoginRequiredMixin, View):
    def get(self, request):   #HTTP GET isteği geldiğinde çalışacak metod, request ile bilgileri çektik
        addresses = request.user.addresses.order_by('-is_default', '-id')
        # İlk adres varsayılan değilse, varsayılan yap
        if addresses.count() == 1 and not addresses.first().is_default:
            address = addresses.first()
            address.is_default = True
            address.save()
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':  #AJAX istek kontrolü
            html = render_to_string('accounts/address_list_partial.html', {'addresses': addresses}, request=request) #tam HTTP response yerine, sadece HTML içeriği almanızı sağlar
            return JsonResponse({'html': html})
        return render(request, 'accounts/address_list.html', {'addresses': addresses})

class AddressFormView(LoginRequiredMixin, View):
    def get_object(self, pk):
        if pk:
            return get_object_or_404(Address, pk=pk, user=self.request.user) #Eğer adres bulunamazsa 404 (sayfa bulunamadı) hatası döner.
        return None

    def get(self, request, pk=None): #pk zorunlu değil eğer varsa düzenle yoksa yeni form
        address = self.get_object(pk) #Var olan adresi al
        form = AddressForm(instance=address) #adres yoksa boş form
        html = render_to_string('accounts/address_form_partial.html', {'form': form}, request=request)
        return JsonResponse({'form_html': html})

    def post(self, request, pk=None):
        address = self.get_object(pk)
        form = AddressForm(request.POST, instance=address)
        if form.is_valid():
            new_address = form.save(commit=False) #Formdan hemen kaydetme, önce kullanıcı bilgisini eklemek için nesneyi oluştur.
            new_address.user = request.user #Adresin hangi kullanıcıya ait olduğunu ata
            # Eğer ilk adres ise varsayılan yap
            if not request.user.addresses.exists():
                new_address.is_default = True
            new_address.save()
            return JsonResponse({'success': True}) #form doğruysa yeni forma gerek yok o yüzden form_html : html yok
        else:
            html = render_to_string('accounts/address_form_partial.html', {'form': form}, request=request)
            return JsonResponse({'success': False, 'form_html': html})

class AddressDeleteView(LoginRequiredMixin, View):
    def get(self, request, pk):
        address = get_object_or_404(Address, pk=pk, user=request.user)
        html = render_to_string('accounts/address_confirm_delete_partial.html', {'address': address}, request=request)
        return JsonResponse({'confirm_html': html})

    def post(self, request, pk):
        address = get_object_or_404(Address, pk=pk, user=request.user)
        if address.is_default and request.user.addresses.exclude(pk=pk).exists(): #pk=pk(görüntülenen adres hariç) diğer adresleri varsa(exists) getir
            other = request.user.addresses.exclude(pk=pk).first()
            other.is_default = True
            other.save()
        address.delete()
        return JsonResponse({'success': True})



# CUSTOMER ORDER LIST
class CustomerOrderListView(LoginRequiredMixin, ListView):
    """
    Giriş yapmış müşterinin kendi siparişlerini listeler.

    Filtreler:

        period=all
        period=3m
        period=<year>

    Pagination:

        page_size=10
        page_size=20
        page_size=30

    Her sipariş kartında en fazla 4 ürün önizlenir.
    """

    model = Order

    template_name = "accounts/orders/orders.html"
    context_object_name = "orders"

    default_page_size = 10
    allowed_page_sizes = (5, 10, 20, 30)

    preview_item_limit = 4

    def get_page_size(self):
        value = self.request.GET.get(
            "page_size",
            str(self.default_page_size),
        )

        try:
            value = int(value)
        except (TypeError, ValueError):
            return self.default_page_size

        if value not in self.allowed_page_sizes:
            return self.default_page_size

        return value

    def get_paginate_by(self, queryset):
        return self.get_page_size()

    def get_queryset(self):

        period = self.request.GET.get(
            "period",
            "all",
        )

        queryset = (
            Order.objects
            .filter(
                user=self.request.user,
            )
        )

        # ---------------------------------------------------------
        # DATE FILTER
        # ---------------------------------------------------------

        if period == "3m":

            start_date = (
                timezone.now()
                - timedelta(days=90)
            )

            queryset = queryset.filter(
                created_at__gte=start_date,
            )

        elif period.isdigit():

            year = int(period)

            queryset = queryset.filter(
                created_at__year=year,
            )

        # ---------------------------------------------------------
        # COUNTS
        # ---------------------------------------------------------

        queryset = queryset.annotate(
            store_count=Count(
                "sub_orders__store",
                distinct=True,
            ),
            item_count=Sum(
                "sub_orders__items__quantity",
            ),
        )

        # ---------------------------------------------------------
        # ORDER ITEMS
        # ---------------------------------------------------------

        order_item_qs = (
            OrderItem.objects
            .order_by(
                "id",
            )
        )

        sub_order_qs = (
            SubOrder.objects
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=order_item_qs,
                    to_attr="prefetched_items",
                ),
            )
            .order_by(
                "id",
            )
        )

        queryset = queryset.prefetch_related(
            Prefetch(
                "sub_orders",
                queryset=sub_order_qs,
                to_attr="prefetched_sub_orders",
            ),
        )

        return queryset.order_by(
            "-created_at",
            "-id",
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(
            **kwargs
        )

        # ---------------------------------------------------------
        # AVAILABLE YEARS
        # ---------------------------------------------------------

        available_years = [
            value.year
            for value in (
                Order.objects
                .filter(
                    user=self.request.user,
                )
                .dates(
                    "created_at",
                    "year",
                    order="DESC",
                )
            )
        ]

        # ---------------------------------------------------------
        # FILTER STATE
        # ---------------------------------------------------------

        period = self.request.GET.get(
            "period",
            "all",
        )

        if (
            period != "all"
            and period != "3m"
            and not period.isdigit()
        ):
            period = "all"

        context["period"] = period

        context["available_years"] = (
            available_years
        )

        context["page_size"] = (
            self.get_page_size()
        )

        # ---------------------------------------------------------
        # FLATTEN ORDER ITEMS
        # ---------------------------------------------------------

        for order in context["orders"]:

            all_items = []

            for sub_order in getattr(
                order,
                "prefetched_sub_orders",
                [],
            ):

                all_items.extend(
                    getattr(
                        sub_order,
                        "prefetched_items",
                        [],
                    )
                )

            order.total_item_count = len(all_items)

            # Kartta gösterilecek ürünler.
            order.preview_items = all_items[
                :self.preview_item_limit
            ]

            order.remaining_item_count = max(
                len(all_items) - len(order.preview_items),
                0,
            )

        # ---------------------------------------------------------
        # PAGINATION QUERY
        # ---------------------------------------------------------

        query_params = (
            self.request.GET.copy()
        )

        query_params.pop(
            "page",
            None,
        )

        context["pagination_query"] = (
            query_params.urlencode()
        )

        return context

# =============================================================================
# CUSTOMER ORDER DETAIL
# =============================================================================


class CustomerOrderDetailView(LoginRequiredMixin, DetailView):
    """
    Müşterinin kendi siparişinin tüm detaylarını gösterir.

    GET:
        /accounts/orders/<order_number>/

    Hiyerarşi:

        Order
            ├── SubOrder (Store A)
            │      ├── OrderItem
            │      └── OrderItem
            │
            └── SubOrder (Store B)
                   └── OrderItem

    Sorumlulukları:
        - Yalnızca mevcut kullanıcıya ait Order'ı getirmek
        - SubOrder ve OrderItem ilişkilerini optimize şekilde yüklemek
        - Detay template'i için gerekli context'i hazırlamak

    Business logic içermez.
    """

    model = Order
    template_name = "accounts/orders/order_detail.html"
    context_object_name = "order"
    slug_field = "order_number"
    slug_url_kwarg = "order_number"

    def get_queryset(self):
        """
        Ownership kontrolü queryset seviyesinde yapılır.

        Böylece başka bir kullanıcının order_number değeri URL üzerinden
        bilinse bile ilgili Order queryset içerisinde bulunamaz ve
        DetailView otomatik olarak 404 döndürür.
        """

        order_items = (
            OrderItem.objects
            .select_related(
                "store_product__variant__product",
            )
            .order_by(
                "id",
            )
        )

        sub_orders = (
            SubOrder.objects
            .select_related(
                "store",
            )
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=order_items,
                ),
            )
            .order_by(
                "id",
            )
        )

        return (
            Order.objects
            .filter(
                user=self.request.user,
            )
            .annotate(
                total_quantity=Sum(
                    "sub_orders__items__quantity",
                ),
            )
            .prefetch_related(
                Prefetch(
                    "sub_orders",
                    queryset=sub_orders,
                ),
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        payment_transaction = (
            PaymentTransaction.objects
            .filter(
                order=self.object,
                status=PaymentStatus.SUCCESS,
            )
            .order_by(
                "-succeeded_at",
                "-id",
            )
            .first()
        )

        sub_orders = list(
            self.object.sub_orders.all()
        )

        for sub_order in sub_orders:
            sub_order.is_delivered = (
                sub_order.status == SubOrderStatus.DELIVERED
            )

        context["payment_transaction"] = payment_transaction

        context["sub_orders"] = sub_orders

        return context

class StoredCardListView(LoginRequiredMixin, TemplateView):
    """ 
    Kullanıcının kayıtlı kartlarını gösterir.
    GET /accounts/stored-cards/

    Bu view yalnızca presentation/context hazırlığından sorumludur.

    Business logic içermez. 
    """

    template_name = "accounts/stored_card_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["stored_cards"] = (
            CardStorageService.list_cards(
                user=self.request.user,
                active_only=True,
            )
        )

        return context

# =============================================================================
# BASE STORED CARD API VIEW
# =============================================================================

class BaseStoredCardAPIView(View):
    """
    Stored Card JSON endpoint'leri için ortak base view.

    Sorumlulukları:
        - Authentication kontrolü
        - JSON body parse etmek
        - Standart success response üretmek
        - Standart error response üretmek
        - Card Storage domain exception'larını HTTP response'a çevirmek

    Business logic içermez.
    """

    http_method_names = [
        "post",
        "options",
    ]

    # -------------------------------------------------------------------------
    # DISPATCH / EXCEPTION HANDLING
    # -------------------------------------------------------------------------

    def dispatch(
        self,
        request,
        *args,
        **kwargs,
    ):
        # ---------------------------------------------------------------------
        # AUTHENTICATION
        # ---------------------------------------------------------------------

        if not request.user.is_authenticated:
            return self.error_response(
                message=(
                    "Kayıtlı kart işlemleri için "
                    "giriş yapmalısınız."
                ),
                code="AUTHENTICATION_REQUIRED",
                status=401,
            )

        try:
            return super().dispatch(
                request,
                *args,
                **kwargs,
            )

        # ---------------------------------------------------------------------
        # 404 - NOT FOUND
        # ---------------------------------------------------------------------

        except StoredCardNotFoundError as exc:
            logger.info(
                "Stored card not found. "
                "method=%s path=%s user_id=%s error=%s",
                request.method,
                request.path,
                request.user.pk,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code="STORED_CARD_NOT_FOUND",
                status=404,
            )

        # ---------------------------------------------------------------------
        # 409 - CONFLICT
        # ---------------------------------------------------------------------

        except CardStorageOperationInProgressError as exc:
            logger.info(
                "Stored card operation in progress. "
                "method=%s path=%s user_id=%s error=%s",
                request.method,
                request.path,
                request.user.pk,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code="CARD_STORAGE_OPERATION_IN_PROGRESS",
                status=409,
            )

        # ---------------------------------------------------------------------
        # 400 - VALIDATION
        # ---------------------------------------------------------------------

        except PaymentValidationError as exc:
            logger.info(
                "Stored card validation error. "
                "method=%s path=%s user_id=%s error=%s",
                request.method,
                request.path,
                request.user.pk,
                exc,
            )

            return self.error_response(
                message=str(exc),
                code=self._get_error_code(exc),
                status=400,
            )

        # ---------------------------------------------------------------------
        # 502 - CONSISTENCY
        # ---------------------------------------------------------------------

        except CardStorageConsistencyError as exc:
            logger.error(
                "Stored card consistency error. "
                "method=%s path=%s user_id=%s error=%s",
                request.method,
                request.path,
                request.user.pk,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "Kayıtlı kart işlemi doğrulanamadı. "
                    "Lütfen daha sonra tekrar deneyin."
                ),
                code="CARD_STORAGE_CONSISTENCY_ERROR",
                status=502,
            )

        # ---------------------------------------------------------------------
        # 502 - GATEWAY
        # ---------------------------------------------------------------------

        except CardStorageGatewayError as exc:
            logger.warning(
                "Stored card gateway error. "
                "method=%s path=%s user_id=%s error=%s",
                request.method,
                request.path,
                request.user.pk,
                exc,
                exc_info=True,
            )

            return self.error_response(
                message=(
                    "Kayıtlı kart servisiyle iletişim "
                    "kurulurken bir sorun oluştu."
                ),
                code="CARD_STORAGE_GATEWAY_ERROR",
                status=502,
            )

        # ---------------------------------------------------------------------
        # UNEXPECTED SYSTEM ERROR
        # ---------------------------------------------------------------------

        except Exception:
            logger.exception(
                "Unexpected stored card error. "
                "method=%s path=%s user_id=%s",
                request.method,
                request.path,
                request.user.pk,
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
            return None, BaseStoredCardAPIView.error_response(
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
            return None, BaseStoredCardAPIView.error_response(
                message="Geçersiz JSON.",
                code="INVALID_JSON",
                status=400,
            )

        if not isinstance(data, dict):
            return None, BaseStoredCardAPIView.error_response(
                message="JSON object gönderilmelidir.",
                code="INVALID_JSON_OBJECT",
                status=400,
            )

        return data, None

    # -------------------------------------------------------------------------
    # IDEMPOTENCY KEY
    # -------------------------------------------------------------------------

    @staticmethod
    def get_idempotency_key(request):
        """
        Idempotency-Key header'ını alır.

        Card Storage create/delete işlemleri provider tarafında
        doğal olarak idempotent olmadığı için client tarafından
        aynı logical operation için aynı key kullanılmalıdır.
        """

        value = request.headers.get(
            "Idempotency-Key"
        )

        if value is None:
            raise PaymentValidationError(
                "Idempotency-Key gereklidir."
            )

        value = value.strip()

        if not value:
            raise PaymentValidationError(
                "Idempotency-Key gereklidir."
            )

        return value

    # -------------------------------------------------------------------------
    # ERROR CODE
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_error_code(exc):
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
    # STORED CARD RESPONSE
    # -------------------------------------------------------------------------

    @staticmethod
    def serialize_card(card):
        """
        StoredCard -> frontend response DTO

        ÖNEMLİ:

            provider_card_token
            provider_customer_key

        gibi hassas provider bilgileri response'a dahil edilmez.
        """

        return {
            "id": card.id,
            "card_alias": (
                card.card_alias
            ),
            "last_four_digits": (
                card.last_four_digits
            ),
            "card_type": (
                card.card_type
            ),
            "card_association": (
                card.card_association
            ),
            "card_family": (
                card.card_family
            ),
            "card_bank_name": (
                card.card_bank_name
            ),
            "expire_month": (
                card.expire_month
            ),
            "expire_year": (
                card.expire_year
            ),
            "is_default": (
                card.is_default
            ),
            "is_active": (
                card.is_active
            ),
        }

    # -------------------------------------------------------------------------
    # BOOLEAN VALUE
    # -------------------------------------------------------------------------

    @staticmethod
    def get_boolean(
        value,
        *,
        field_name,
        default=False,
    ):
        """
        JSON boolean alanını güvenli şekilde normalize eder.

        Örneğin:

            true
            false

        kabul edilir.

        String:

            "true"
            "false"

        kabul edilmez.
        """

        if value is None:
            return default

        if not isinstance(
            value,
            bool,
        ):
            raise PaymentValidationError(
                f"{field_name} boolean olmalıdır."
            )

        return value

# =============================================================================
# STORED CARD CREATE API
# =============================================================================

class StoredCardCreateAPIView(
    BaseStoredCardAPIView,
):
    """
    Kullanıcının iyzico Card Storage'a yeni kart kaydetmesini sağlar.

    POST /accounts/stored-cards/create/

    Örnek:

        {
            "card_holder_name": "John Doe",
            "card_number": "5528790000000008",
            "expire_month": "12",
            "expire_year": "2030",
            "card_alias": "Benim Visa Kartım",
            "make_default": true
        }

    CVC alınmaz.

    CardStorageService:
        - kart validasyonu
        - idempotency
        - PaymentCustomer
        - iyzico Card Storage
        - StoredCard
        - default card
        işlemlerini yönetir.

    Bu view business logic içermez.
    """

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
        # 2. IDEMPOTENCY
        # ---------------------------------------------------------------------

        idempotency_key = (
            self.get_idempotency_key(
                request
            )
        )

        # ---------------------------------------------------------------------
        # 3. CARD INPUT
        # ---------------------------------------------------------------------

        card = StoredCardCreateData(
            card_holder_name=str(
                data.get(
                    "card_holder_name",
                    "",
                )
            ).strip(),

            card_number=str(
                data.get(
                    "card_number",
                    "",
                )
            ).strip(),

            expire_month=str(
                data.get(
                    "expire_month",
                    "",
                )
            ).strip(),

            expire_year=str(
                data.get(
                    "expire_year",
                    "",
                )
            ).strip(),

            card_alias=str(
                data.get(
                    "card_alias",
                    "",
                )
            ).strip(),
        )

        # ---------------------------------------------------------------------
        # 4. DEFAULT
        # ---------------------------------------------------------------------

        make_default = self.get_boolean(
            data.get(
                "make_default"
            ),
            field_name="make_default",
            default=False,
        )

        # ---------------------------------------------------------------------
        # 5. CREATE CARD
        # ---------------------------------------------------------------------

        stored_card = (
            CardStorageService.create_card(
                user=request.user,
                card=card,
                idempotency_key=idempotency_key,
                make_default=make_default,
            )
        )

        # ---------------------------------------------------------------------
        # 6. RESPONSE
        # ---------------------------------------------------------------------

        return self.success_response(
            {
                "message": (
                    "Kart başarıyla kaydedildi."
                ),
                "card": self.serialize_card(
                    stored_card
                ),
            },
            status=201,
        )

# =============================================================================
# STORED CARD DELETE API
# =============================================================================

class StoredCardDeleteAPIView(
    BaseStoredCardAPIView,
):
    """
    Kullanıcının kayıtlı kartını siler.

    POST /accounts/stored-cards/<card_id>/delete/

    Örnek header:

        Idempotency-Key: 0f7f2c8d-...

    Fiziksel DB delete yapılmaz.

    CardStorageService:
        iyzico kartını siler
        ve
        local StoredCard.is_active = False

    durumunu yönetir.
    """

    def post(
        self,
        request,
        card_id,
        *args,
        **kwargs,
    ):
        # ---------------------------------------------------------------------
        # 1. IDEMPOTENCY
        # ---------------------------------------------------------------------

        idempotency_key = (
            self.get_idempotency_key(
                request
            )
        )

        # ---------------------------------------------------------------------
        # 2. DELETE CARD
        # ---------------------------------------------------------------------

        stored_card = (
            CardStorageService.delete_card(
                user=request.user,
                card_id=card_id,
                idempotency_key=idempotency_key,
            )
        )

        # ---------------------------------------------------------------------
        # 3. RESPONSE
        # ---------------------------------------------------------------------

        return self.success_response(
            {
                "message": (
                    "Kart başarıyla silindi."
                ),
                "card_id": stored_card.id,
            }
        )

# =============================================================================
# STORED CARD DEFAULT API
# =============================================================================

class StoredCardDefaultAPIView(
    BaseStoredCardAPIView,
):
    """
    Kullanıcının aktif kayıtlı kartını varsayılan kart yapar.

    POST /accounts/stored-cards/<card_id>/default/

    Bu işlem yalnızca local DB üzerinde gerçekleşir.

    CardStorageService:
        - user ownership
        - active card
        - transaction
        - row locking
        - mevcut default temizleme
        işlemlerini yönetir.
    """

    def post(
        self,
        request,
        card_id,
        *args,
        **kwargs,
    ):
        # ---------------------------------------------------------------------
        # 1. SET DEFAULT
        # ---------------------------------------------------------------------

        stored_card = (
            CardStorageService.set_default_card(
                user=request.user,
                card_id=card_id,
            )
        )

        # ---------------------------------------------------------------------
        # 2. RESPONSE
        # ---------------------------------------------------------------------

        return self.success_response(
            {
                "message": (
                    "Varsayılan kart güncellendi."
                ),
                "card": self.serialize_card(
                    stored_card
                ),
            }
        )

class SellerApplicationMixin:
    """
    Satıcı profili var mı? Onaylandı mı?
    """
    def get_seller_profile(self):
        if not hasattr(self, '_seller_profile'):
            try:
                self._seller_profile = self.request.user.seller_profile
            except SellerProfile.DoesNotExist:
                self._seller_profile = None
        return self._seller_profile


class BecomeASellerView(LoginRequiredMixin, SellerApplicationMixin, FormView):
    template_name = 'accounts/become_seller.html'
    form_class = BecomeASellerForm
    success_url = reverse_lazy('accounts:seller_form')

    def get_object(self):
        try:
            return self.request.user.seller_profile
        except SellerProfile.DoesNotExist:
            return None

    def dispatch(self, request, *args, **kwargs):
        profile = self.get_object()
        if getattr(self.request.user, 'is_seller', False):
            messages.info(request, "Kurumsal bilgilerinizi güncelleyebilirsiniz. Değişiklikleriniz admin onayına gönderilecektir.")
        elif profile and not profile.is_approved:
            messages.warning(request, "Satıcı başvurunuz onay bekliyor. Bilgilerinizi güncelleyebilirsiniz.")
        
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        instance = self.get_object()
        if instance:
            kwargs['instance'] = instance
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        profile = self.get_object()
        # Onaylı satıcının bekleyen isteği varsa formu yeni bilgilerle doldur
        if profile and profile.is_approved:
            pending_request = SellerProfileUpdateRequest.objects.filter(
                seller_profile=profile, status='pending'
            ).first()
            if pending_request:
                if pending_request.new_company_name:
                    initial['company_name'] = pending_request.new_company_name
                if pending_request.new_company_address:
                    initial['company_address'] = pending_request.new_company_address
                if pending_request.new_company_phone:
                    initial['company_phone'] = pending_request.new_company_phone
                if pending_request.new_iban:
                    initial['iban'] = pending_request.new_iban
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.get_object()
        
        if getattr(self.request.user, 'is_seller', False):
            context['form_title'] = "Satıcı Bilgilerim"
            # Bekleyen isteği şablona gönderiyoruz (Eski vs Yeni karşılaştırması için)
            context['pending_request'] = SellerProfileUpdateRequest.objects.filter(
                seller_profile=profile, status='pending'
            ).first()
            context['seller_profile'] = profile
        elif profile:
            context['form_title'] = "Satıcı Başvurusu Güncelle"
        else:
            context['form_title'] = "Satıcı Ol"
        return context

    def form_valid(self, form):
        profile = self.get_object()
        
        # ONAYLI SATICIYSA: Profili ezme, Değişiklik İsteği oluştur!
        if profile and profile.is_approved:
            change_request, created = SellerProfileUpdateRequest.objects.update_or_create(
                seller_profile=profile,
                status='pending',
                defaults={
                    'new_company_name': form.cleaned_data.get('company_name', ''),
                    'new_company_address': form.cleaned_data.get('company_address', ''),
                    'new_company_phone': form.cleaned_data.get('company_phone', ''),
                    'new_iban': form.cleaned_data.get('iban', ''),
                }
            )
            msg = "Bekleyen değişiklik isteğiniz güncellendi." if not created else "Değişiklikleriniz admin onayına gönderildi. Onaylanana kadar mevcut bilgileriniz geçerlidir."
            messages.info(self.request, msg)
            return redirect(self.get_success_url())
            
        # ONAYLI DEĞİLSE (Yeni başvuruysa veya onay bekliyorsa): Direkt profili kaydet
        else:
            seller_profile = form.save(commit=False)
            seller_profile.user = self.request.user
            seller_profile.is_approved = False  
            seller_profile.save()
            messages.success(self.request, "Satıcı başvurunuz başarıyla alındı/güncellendi.")
            return super().form_valid(form)



class CollectionListView(LoginRequiredMixin, ListView):
    """
    Kullanıcının koleksiyonlarını listeler.

    Her koleksiyon için:
    - Toplam item sayısını verir.
    - Son eklenen 4 item'ı preview olarak getirir.
    - Ürün aktif olmasa / stokta olmasa bile favoride göstermeye devam eder.
    """

    model = ProductCollection
    template_name = "accounts/collections/collection_list.html"
    context_object_name = "collections"

    def get_queryset(self):
        # Sıralama Parametresini Al
        sort_by = self.request.GET.get('sort', 'newest')
        valid_sorts = {
            'newest': '-created_at',
            'oldest': 'created_at',
            'name_asc': 'name',
            'name_desc': '-name'
        }
        order_field = valid_sorts.get(sort_by, '-created_at')

        preview_items = (
            ProductCollectionItem.objects
            .select_related(
                "variant__product",
            )
            .prefetch_related(
                "variant__attribute_values__attribute__category_attributes",
                "variant__product__image_groups__images",
                "variant__product__image_groups__visual_attribute_values"
            )
            .annotate(
                row_number=Window(
                    expression=RowNumber(),
                    partition_by=[F("collection_id")],
                    order_by=[
                        F("added_at").desc(),
                        F("id").desc(),
                    ],
                )
            )
            .filter(
                row_number__lte=4,
            )
        )

        return (
            ProductCollection.objects
            .filter(
                user=self.request.user,
            )
            .annotate(
                item_count=Count("items"),
            )
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=preview_items,
                    to_attr="preview_items",
                )
            )
            .order_by(
                "-is_default",
                order_field,
            )
        )


class CollectionDetailView(LoginRequiredMixin, DetailView):
    """
    Kullanıcının belirli bir koleksiyonundaki ürünleri listeler.

    Sadece kullanıcının kendi koleksiyonlarına erişmesine izin verilir.
    Pasif veya stokta olmayan ürünler de gösterilmeye devam eder.
    """

    model = ProductCollection
    template_name = "accounts/collections/collection_detail.html"
    context_object_name = "collection"

    def get_queryset(self):
        return (
            ProductCollection.objects
            .filter(
                user=self.request.user,
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)


        items = (
            ProductCollectionItem.objects
            .filter(collection=self.object)
            .select_related(
                "variant__product__brand", 
                "variant__product__category",
                "offer",
                "offer__store"
            )
            .prefetch_related(
                "variant__attribute_values__attribute",
                "variant__product__image_groups__images",
                "variant__product__image_groups__visual_attribute_values"
            )
            .order_by("-added_at", "-id")
        )

        context["items"] = items
        return context

class CollectionDeleteAPIView(View):
    """
    Kullanıcının seçtiği listeyi tamamen siler.
    """
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Giriş yapmanız gerekiyor.'}, status=403)
        try:
            data = json.loads(request.body)
            collection_id = data.get('collection_id')
            
            # Yalnızca giriş yapan kullanıcının kendi koleksiyonu silinebilir ve varsayılan liste silinemez
            collection = get_object_or_404(ProductCollection, id=collection_id, user=request.user)
            
            if collection.is_default:
                return JsonResponse({'success': False, 'error': 'Varsayılan favori listesi silinemez.'}, status=400)
            
            collection.delete()
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)



class UserQuestionsListView(LoginRequiredMixin, ListView):
    """
    Hesabım > Soru ve Taleplerim

    Kullanıcının yalnızca kendi ürün sorularını listeler.

    Her soru için:
    - Ürün
    - Marka
    - Kategori
    - Hedef mağaza
    - Varyant
    - Görünür cevaplar
    bilgileri optimize şekilde hazırlanır.
    """

    model = ProductQuestion
    template_name = "accounts/questions/user_questions.html"
    context_object_name = "questions"
    paginate_by = 10

    def get_queryset(self):
        # ---------------------------------------------------------
        # CEVAPLAR
        # ---------------------------------------------------------
        #
        # Müşteriye yalnızca görünür cevapları gösteriyoruz.
        #
        # Store bilgisi template'te kullanılacaksa:
        # select_related("store")
        # sayesinde ekstra sorgu oluşmaz.

        answers_prefetch = Prefetch(
            "answers",
            queryset=(
                ProductAnswer.objects
                .filter(
                    is_visible=True,
                )
                .select_related(
                    "store",
                )
                .order_by(
                    "created_at",
                )
            ),
            to_attr="visible_answers",
        )

        # ---------------------------------------------------------
        # SORULAR
        # ---------------------------------------------------------

        queryset = (
            ProductQuestion.objects
            .filter(
                user=self.request.user,
            )
            .select_related(
                # Soru → Product
                "product",

                # Product → Brand
                "product__brand",

                # Product → Category
                "product__category",

                # Soru → Store
                "target_store",

                # Soru → Variant
                "variant_context",
            )
            .prefetch_related(
                # -------------------------------------------------
                # ANSWERS
                # -------------------------------------------------
                answers_prefetch,

                # -------------------------------------------------
                # VARIANT ATTRIBUTE'LARI
                # -------------------------------------------------
                #
                # get_thumbnail_url içerisinde:
                

                "variant_context__attribute_values__attribute",

                # -------------------------------------------------
                # IMAGE GROUPS
                # -------------------------------------------------
                #
                # Variant thumbnail'ı:
                #
                # Product
                #   └── image_groups
                #         ├── visual_attribute_values
                #         └── images
                #
                # üzerinden çözülüyor.

                "product__image_groups__visual_attribute_values",
                "product__image_groups__images",
            )
            .annotate(
                has_unread_answer=Exists(
                    ProductAnswer.objects.filter(
                        question_id=OuterRef("pk"),
                        is_visible=True,
                        is_read_by_user=False
                    )
                ),
                # Sıralama için en son cevabın tarihini al
                last_answer_date=Max('answers__created_at', filter=Q(answers__is_visible=True))
            )
            .order_by(
                "-has_unread_answer", # Okunmamış olanlar EN ÜSTTE
                "-last_answer_date",  # Sonra en son cevap verilenler
                "-created_at",        # En son sorulanlar
            )
        )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Hesabım sayfasındaki aktif sekme
        context["active_tab"] = "product_questions"

        # ---------------------------------------------------------
        # OKUNMAMIŞ CEVAP SAYISI
        # ---------------------------------------------------------
        #
        # Sadece:
        # - bu kullanıcıya ait sorular
        # - görünür cevaplar
        # - okunmamış cevaplar
        # sayılır.

        context["unread_count"] = (
            ProductAnswer.objects
            .filter(
                question__user=self.request.user,
                is_visible=True,
                is_read_by_user=False,
            )
            .count()
        )

        return context


class MarkAnswerAsReadAPIView(LoginRequiredMixin, View):
    """
    Kullanıcının kendi sorusuna gelen görünür cevapları
    okundu olarak işaretler.

    POST:
        /accounts/questions/<question_id>/read/
    """

    def post(self, request, question_id):
        if not request.user.is_authenticated:
            return JsonResponse({"success": False, "error": "Giriş yapmalısınız."}, status=403)

        updated_count = (
            ProductAnswer.objects
            .filter(
                question_id=question_id,
                question__user=request.user,
                is_visible=True,
                is_read_by_user=False,
            )
            .update(
                is_read_by_user=True,
            )
        )

        return JsonResponse({
            "success": True,
            "updated_count": updated_count,
        })


class ProductQADeleteAPIView(View):
    """
    Kullanıcının kendi sorusunu silmesini sağlar.

    DELETE
    """

    def delete(
        self,
        request,
        question_id,
        *args,
        **kwargs,
    ):
        # -------------------------------------------------
        # AUTH
        # -------------------------------------------------

        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Giriş yapmanız gerekiyor.",
                },
                status=401,
            )

        # -------------------------------------------------
        # QUESTION
        # -------------------------------------------------

        question = get_object_or_404(
            ProductQuestion,
            pk=question_id,
        )

        # -------------------------------------------------
        # DELETE
        # -------------------------------------------------

        try:
            ProductQAService.delete_question(
                question=question,
                user=request.user,
            )

        except PermissionError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=403,
            )

        return JsonResponse(
            {
                "success": True,
                "message": "Sorunuz silindi.",
            },
            status=200,
        )