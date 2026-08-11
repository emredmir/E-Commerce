from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import AccessMixin
from django.contrib import messages
from django.views.generic import CreateView, ListView, UpdateView, DetailView, View
from .models import Store, StoreUpdateRequest, StoreStatus

from django.urls import reverse_lazy

from .forms import StoreForm


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
    
