from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError
from .models import CustomUser, SellerProfile, Address

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=False)
    phone_number = forms.CharField(required=False)

    class Meta:
        model = CustomUser
        fields = ('first_name', 'last_name', 'email', 'phone_number', 'password1', 'password2')

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        phone = cleaned_data.get('phone_number')

        if not email and not phone:
            raise ValidationError("Lütfen e-posta veya telefon numarası girin.")

        # Bu kontrolü manager'da da yaptık, ama kullanıcıya daha hızlı geri bildirim vermek için burada tutmak mantıklı.
        if email and CustomUser.objects.filter(email=email).exists():
            raise ValidationError("Bu e-posta adresi zaten kullanılıyor.")

        if phone and CustomUser.objects.filter(phone_number=phone).exists():
            raise ValidationError("Bu telefon numarası zaten kullanılıyor.")

        return cleaned_data

class CustomAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label='E-posta veya Telefon Numarası')


class CustomPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Tüm alanlara 'form-control' sınıfını ekle
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

        # Sadece değiştirmek istediğimiz alanların özelliklerini güncelle
        self.fields['old_password'].label = "Mevcut Şifre"
        self.fields['old_password'].widget.attrs.update({'placeholder': 'Mevcut şifrenizi girin'})

        self.fields['new_password1'].label = "Yeni Şifre"
        self.fields['new_password1'].widget.attrs.update({'placeholder': 'Yeni şifrenizi girin'})
        self.fields['new_password1'].help_text = "Şifreniz en az 8 karakter olmalıdır."

        self.fields['new_password2'].label = "Yeni Şifre (Tekrar)"
        self.fields['new_password2'].widget.attrs.update({'placeholder': 'Yeni şifrenizi tekrar girin'})


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'phone_number', 'email']
        labels = {
            'first_name': 'İsim',
            'last_name': 'Soyisim',
            'phone_number': 'Telefon Numarası',
            'email': 'E-posta Adresi',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Bootstrap class'ları
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

        self.fields['first_name'].widget.attrs['placeholder'] = 'İsminizi girin'
        self.fields['last_name'].widget.attrs['placeholder'] = 'Soyisminizi girin'
        self.fields['email'].widget.attrs['placeholder'] = 'E-posta adresinizi girin'
        self.fields['phone_number'].widget.attrs['placeholder'] = 'Telefon numaranızı girin'

        # placeholders = {
        #     'first_name': 'İsminizi girin',
        #     'last_name': 'Soyisminizi girin',
        #     'email': 'E-posta adresinizi girin',
        #     'phone_number': 'Telefon numaranızı girin',
        # }
        # for field_name, placeholder in placeholders.items():
        #     self.fields[field_name].widget.attrs['placeholder'] = placeholder

    def clean_email(self):
        email = self.cleaned_data.get('email')
        # E-posta adresinin zaten başka bir kullanıcı tarafından kullanılıp kullanılmadığını kontrol et
        if CustomUser.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Bu e-posta adresi zaten kullanılıyor.")
        return email
    
    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone and CustomUser.objects.filter(phone_number=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Bu telefon numarası zaten kullanılıyor.")
        return phone
    

class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = [
            'title', 'full_name', 'phone_number',
            'address_line1', 'address_line2',
            'city', 'state', 'postal_code', 'is_default'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Tüm alanlara 'form-control' sınıfını ekle
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
        
        # is_default alanını checkbox olarak belirt
        self.fields['is_default'].widget = forms.CheckboxInput(attrs={'class': 'form-check-input'})

        # Etiketleri ve placeholder'ları düzenle
        labels = {
            'title': 'Adres Başlığı',
            'full_name': 'Alıcı Adı Soyadı',
            'phone_number': 'Telefon Numarası',
            'address_line1': 'Adres Satırı 1',
            'address_line2': 'Adres Satırı 2',
            'city': 'Şehir',
            'state': 'İlçe',
            'postal_code': 'Posta Kodu',
            'is_default': 'Varsayılan Adres Yap',
        }
        
        placeholders = {
            'title': 'Örnek: Ev, İş',
            'full_name': 'Alıcı Adı Soyadı',
            'phone_number': '05xxxxxxxxx',
            'address_line1': 'Mahalle, Cadde/Sokak',
            'address_line2': 'Bina Adı, Daire No, Kat',
            'city': 'İstanbul',
            'state': 'Kadıköy',
            'postal_code': '34000',
        }

        for field_name, label in labels.items():
            self.fields[field_name].label = label
        
        for field_name, placeholder in placeholders.items():
            if field_name != 'is_default':
                self.fields[field_name].widget.attrs.update({'placeholder': placeholder})


class BecomeASellerForm(forms.ModelForm):
    class Meta:
        model = SellerProfile
        fields = ['company_name', 'company_address', 'company_phone', 'iban']
        labels = {
            'company_name': 'Şirket Adı',
            'company_address': 'Şirket Adresi',
            'company_phone': 'Şirket Telefonu',
            'iban': 'IBAN',
        }

    def __init__(self, *args, **kwargs):
        is_readonly = kwargs.pop('is_readonly', False)
        super().__init__(*args, **kwargs)

        # Bootstrap class'ları
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

        self.fields['company_name'].widget.attrs['placeholder'] = 'Şirket adınızı girin'
        self.fields['company_address'].widget.attrs['placeholder'] = 'Şirket adresinizi girin'
        self.fields['company_phone'].widget.attrs['placeholder'] = 'Şirket telefon numaranızı girin'
        self.fields['iban'].widget.attrs['placeholder'] = 'IBAN numaranızı girin'

        # placeholders = {
        #     'first_name': 'İsminizi girin',
        #     'last_name': 'Soyisminizi girin',
        #     'email': 'E-posta adresinizi girin',
        #     'phone_number': 'Telefon numaranızı girin',
        # }
        # for field_name, placeholder in placeholders.items():
        #     self.fields[field_name].widget.attrs['placeholder'] = placeholder

    def clean_company_name(self):
        company_name = self.cleaned_data.get('company_name')
        # E-posta adresinin zaten başka bir kullanıcı tarafından kullanılıp kullanılmadığını kontrol et
        if SellerProfile.objects.filter(company_name=company_name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Bu şirket adı zaten kullanılıyor.")
        return company_name
    