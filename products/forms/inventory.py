from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError

from products.models import StoreProduct, StoreProductStatus, Product

class StoreProductUpdateForm(forms.ModelForm):
    """
    Satıcının Envanter sayfasından mevcut bir teklifin
    fiyat, stok, sku ve durum gibi mağaza seviyesindeki 
    bilgilerini güncellemesi için kullanılır.
    """
    
    class Meta:
        model = StoreProduct
        fields = [
            "price",
            "stock",
            "sku",
            "status",
            "seller_notes",
        ]
        
        widgets = {
            "price": forms.NumberInput(
                attrs={
                    "class": "pw-input", 
                    "step": "0.01", 
                    "min": "0.01",
                    "placeholder": "Örn: 499.90"
                }
            ),
            "stock": forms.NumberInput(
                attrs={
                    "class": "pw-input", 
                    "min": "0",
                    "placeholder": "Örn: 50"
                }
            ),
            "sku": forms.TextInput(
                attrs={
                    "class": "pw-input", 
                    "placeholder": "İsteğe bağlı mağaza içi stok kodunuz"
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": "pw-select"
                }
            ),
            "seller_notes": forms.Textarea(
                attrs={
                    "class": "pw-textarea", 
                    "rows": 3, 
                    "placeholder": "Müşteriler görmez. Sadece sizin görebileceğiniz notlar..."
                }
            ),
        }
        
        labels = {
            "price": "Satış Fiyatı (TL)",
            "stock": "Güncel Stok Adedi",
            "sku": "Satıcı Stok Kodu (SKU)",
            "status": "Satış Durumu",
            "seller_notes": "Satıcı Notları (Gizli)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. KULLANICI ARAYÜZÜ (UX) KISITLAMASI:
        # Satıcıya sadece "Yayında" ve "Arşivlendi" seçeneklerini gösteriyoruz.
        # "Taslak" veya "Tükendi" gibi sistemin kontrol ettiği durumları seçemez.
        self.fields["status"].choices = [
            (StoreProductStatus.ACTIVE.value, "Yayında (Satışa Açık)"),
            (StoreProductStatus.ARCHIVED.value, "Arşivlendi (Satışa Kapalı)"),
        ]
        
        # 2. Eğer ürün veritabanında "Tükendi" (OUT_OF_STOCK) durumundaysa, 
        # dropdown menüsünde "Yayında" seçili gibi gösteriyoruz. Çünkü ürün aslında
        # satışa açıktır, sadece stoğu bitmiştir.
        if self.instance and self.instance.pk:
            if self.instance.status == StoreProductStatus.OUT_OF_STOCK:
                self.initial["status"] = StoreProductStatus.ACTIVE.value
        

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is None or price <= Decimal("0"):
            raise forms.ValidationError("Fiyat 0'dan büyük geçerli bir tutar olmalıdır.")
        return price

    def clean_stock(self):
        stock = self.cleaned_data.get("stock")
        if stock is None or stock < 0:
            raise forms.ValidationError("Stok adedi negatif olamaz.")
        return stock

    def clean_sku(self):
        sku = self.cleaned_data.get("sku")
        if sku:
            sku = sku.strip()
            # Satıcının kendi mağazasında, bu ürün "haricinde" aynı SKU'yu 
            # kullanıp kullanmadığını kontrol ediyoruz.
            exists = StoreProduct.objects.filter(
                store=self.instance.store,
                sku__iexact=sku
            ).exclude(pk=self.instance.pk).exists()
            
            if exists:
                raise forms.ValidationError(f"'{sku}' stok kodu mağazanızda başka bir üründe zaten kullanılıyor.")
        
        return sku or None

    def clean(self):
        cleaned_data = super().clean()
        stock = cleaned_data.get("stock")
        status = cleaned_data.get("status")

        # Önceki alanlarda hata varsa (Örn: stok boş geçildiyse) işlemi durdur.
        if stock is None or status is None:
            return cleaned_data

        status_str = str(status)

        # 3. İŞ KURALLARI
        
        # A. Kullanıcı ürünü arşivlemek istiyorsa, stoğu 1.000 adet bile olsa arşivlenir.
        if status_str == StoreProductStatus.ARCHIVED.value:
            cleaned_data["status"] = StoreProductStatus.ARCHIVED.value
            
        # B. Kullanıcı ürünü "Yayında" tutmak istiyorsa, stok miktarına bakarak nihai kararı sistem verir.
        else:
            if stock == 0:
                cleaned_data["status"] = StoreProductStatus.OUT_OF_STOCK.value
            else:
                cleaned_data["status"] = StoreProductStatus.ACTIVE.value

        return cleaned_data


class ProductUpdateForm(forms.ModelForm):

    """

    Satıcının global katalogdaki bir ürünün açıklamasını

    güncellemesi için kullanılır.

    İsim, marka, kategori gibi alanlar kilitlidir.

    """

    class Meta:

        model = Product

        fields = ["description"]


        widgets = {
        
        "description": forms.Textarea(
        
        attrs={
        
        "class": "pw-textarea",

        "rows": 8,

        "placeholder": "Ürünün genel özelliklerini, donanım detaylarını ve kullanım avantajlarını anlatın..."

        }

        ),

        }


        labels = {
        
        "description": "Katalog Ürün Açıklaması",

        }

    def clean_description(self):
        description = self.cleaned_data.get("description")

        if not description or len(description.strip()) < 10:

            raise forms.ValidationError("Lütfen daha detaylı ve açıklayıcı bir metin girin (En az 10 karakter).")

        return description.strip() 