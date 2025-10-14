from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout, get_backends
from .forms import CustomUserCreationForm, CustomAuthenticationForm, CustomPasswordChangeForm, ProfileUpdateForm, AddressForm, BecomeASellerForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import CustomUser, Address, SellerProfile
from django.contrib.auth.views import PasswordChangeView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView, FormView
from django.views import View
from django.http import JsonResponse
from django.template.loader import render_to_string

def register_view(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()

            # Kullanıcıya backend tanımla (sadece bir backend kullanıyorsan bu güvenlidir)
            backend = get_backends()[0]  # İlk backend: EmailOrPhoneBackend
            user.backend = f"{backend.__module__}.{backend.__class__.__name__}"

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
    success_message = "Şifreniz başarıyla değiştirildi. 🎉"

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

# @login_required
# def profile_update_view(request):
#     if request.method == 'POST':
#         form = UserProfileUpdateForm(request.POST, instance=request.user)
#         if form.is_valid():
#             form.save()
#             messages.success(request, "Profil bilgileriniz başarıyla güncellendi.")
#             return redirect('accounts:profile') # Profil sayfasına geri yönlendirin
#         else:
#            messages.error(request, "Lütfen formu doğru şekilde doldurun.")
#     else:
#         form = UserProfileUpdateForm(instance=request.user)
    
#     return render(request, 'accounts/profile_update.html', {'form': form})


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
        """
        Mevcut satıcı profilini döndürür. Yoksa None döndürür.
        """
        try:
            return self.request.user.seller_profile
        except SellerProfile.DoesNotExist:
            return None

    def dispatch(self, request, *args, **kwargs):
        # Kullanıcının zaten onaylı satıcı olup olmadığını kontrol et
        if self.request.user.is_seller:
            messages.info(request, "Başvurunuz onaylandı.")
            kwargs['is_readonly'] = True #?
        
        # if request.method == 'POST' and self.request.user.is_seller:
        #     messages.error(request, "Onaylanmış bir satıcının başvuru bilgileri değiştirilemez.")
        #     return redirect('accounts:seller_form')
        
        # Eğer başvuru yapmış ancak onaylanmamışsa, mesaj göster
        if self.get_object() and not self.get_object().is_approved:
            messages.warning(request, "Satıcı başvurunuz onay bekliyor. Dilerseniz bilgilerinizi güncelleyebilirsiniz.")
        
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        """
        FormView'un formu başlatmak için kullandığı keyword argümanlarını (kwargs) döndürür.
        Burada, var olan bir satıcı profili varsa 'instance' argümanını ekleriz.
        """
        kwargs = super().get_form_kwargs()
        instance = self.get_object()
        if instance:
            kwargs['instance'] = instance
        return kwargs

    def get_form(self, form_class=None):
        """Onaylı satıcılar için formu readonly (değiştirilemez) yap."""
        form = super().get_form(form_class)
        if self.request.user.is_seller:
            for field in form.fields.values():
                field.disabled = True
        return form

    def form_valid(self, form):
        seller_profile = form.save(commit=False)
        seller_profile.user = self.request.user
        seller_profile.is_approved = False  # Admin onayı bekleniyor
        seller_profile.save()

        messages.success(self.request, "Satıcı başvurunuz başarıyla alındı. İnceleme sürecine geçildi.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.get_object():
            context['form_title'] = "Satıcı Başvurusu Güncelle"
        else:
            context['form_title'] = "Satıcı Ol"
        return context


# @login_required
# def become_a_seller_view(request):
#     # Kullanıcının hali hazırda başvurusu var mı?
#     try:
#         seller_profile = request.user.seller_profile
#     except SellerProfile.DoesNotExist:
#         seller_profile = None

#     if request.method == 'POST':
#         # Eğer başvuru varsa, instance olarak veriyoruz (update için)
#         form = BecomeASellerForm(request.POST, instance=seller_profile)
#         if form.is_valid():
#             seller_profile = form.save(commit=False)
#             seller_profile.user = request.user
#             # Başvuru yapıldığı anda onay false kalacak, admin onayı bekleyecek
#             seller_profile.is_approved = False
#             seller_profile.save()
#             messages.success(request, "Satıcı başvurunuz başarıyla alındı. Onay süreci tamamlandığında bilgilendirileceksiniz.")
#             return redirect('accounts:become_seller')  # Aynı sayfaya yönlendirip durum gösterebiliriz
#         else:
#             messages.error(request, "Formda hatalar var, lütfen düzeltin.")
#     else:
#         form = BecomeASellerForm(instance=seller_profile)

#     context = {
#         'form': form,
#         'seller_profile': seller_profile,
#     }

#     return render(request, 'accounts/become_seller.html', context)


# @login_required
# def address_view(request):
#     addresses = request.user.addresses.all()
#     form = AddressForm()
#     if request.method == 'POST':
#         form = AddressForm(request.POST)
#         if form.is_valid():
#             address = form.save(commit=False)
#             address.user = request.user
#             address.save()
#             messages.success(request, "Adres başarıyla eklendi.")
#             return redirect('accounts:profile')
    
#     return render(request, 'accounts/profile.html', {
#         'addresses': addresses,
#         'form': form
#     })