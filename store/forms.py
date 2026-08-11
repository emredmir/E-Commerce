from django import forms
from .models import Store
from PIL import Image

class StoreForm(forms.ModelForm):
    class Meta:
        model = Store
        fields = [
            'store_name', 
            'logo', 
            'banner', 
            'contact_email', 
            'contact_phone', 
            'address'
        ]
        
        # HTML tarafında nasıl görüneceklerini buradan ayarlıyoruz (Widget)
        widgets = {
            'store_name': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'Örn: Teknoloji Dünyası'
            }),
            'contact_email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'iletisim@magaza.com'
            }),
            'contact_phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '05XX XXX XX XX'
            }),
            'address': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Mağazanızın fiziksel adresi (Varsa)'
            }),
            'logo': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*' # Sadece resim dosyalarını göster
            }),
            'banner': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*'
            }),
        }
        
        labels = {
            'store_name': 'Mağaza Adı',
            'contact_email': 'İletişim E-posta',
            'contact_phone': 'İletişim Telefon',
            'address': 'Adres',
            'logo': 'Mağaza Logosu (Kare önerilir)',
            'banner': 'Mağaza Banner (Geniş görsel önerilir)',
        }

    def clean_logo(self):
        """Logo boyutu kontrolü (Maksimum 2MB)"""
        logo = self.cleaned_data.get('logo')
        
        if logo:
            # Eğer yeni bir dosya yüklenmişse kontrol et (Mevcut dosya ise atla)
            if hasattr(logo, 'size'): 
                if logo.size > 2 * 1024 * 1024:
                    raise forms.ValidationError("Logo boyutu 2MB'dan büyük olamaz.")
                
            # Gerçekten resim mi kontrol et
            try:
                Image.open(logo).verify()
                logo.seek(0)  # Dosya işaretçisini sıfırla
            except Exception:
                raise forms.ValidationError(
                    "Geçerli bir resim dosyası yükleyin."
                )
        return logo

    def clean_banner(self):
        """Banner boyutu kontrolü (Maksimum 5MB)"""
        banner = self.cleaned_data.get('banner')
        
        if banner:
            if hasattr(banner, 'size'):
                if banner.size > 5 * 1024 * 1024:
                    raise forms.ValidationError("Banner boyutu 5MB'dan büyük olamaz.")  

            try:
                Image.open(banner).verify()
                banner.seek(0)  # Dosya işaretçisini sıfırla
            except Exception:
                raise forms.ValidationError(
                    "Geçerli bir resim dosyası yükleyin."
                )
        return banner