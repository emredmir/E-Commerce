from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError

from products.models import StoreProduct, StoreProductStatus

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
            "status": "Yayın Durumu",
            "seller_notes": "Satıcı Notları (Gizli)",
        }

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
        
        # Eğer string boşsa veritabanına NULL (None) gitmesi UniqueConstraint için daha sağlıklıdır.
        return sku or None

    def clean(self):
        cleaned_data = super().clean()
        stock = cleaned_data.get("stock")
        status = cleaned_data.get("status")

        # İş Kuralları (Business Logic): 
        # Eğer satıcı stok miktarını 0 yapıp durumu "Yayında" bırakmaya çalışırsa 
        # veya dalgınlığına gelirse, arka planda durumu otomatik olarak "Tükendi" yapıyoruz.
        if stock == 0 and status == StoreProductStatus.ACTIVE:
            cleaned_data["status"] = StoreProductStatus.OUT_OF_STOCK
            
        # Eğer stok girdiyse ama durumu "Tükendi" olarak unutmuşsa "Yayında" yap.
        elif stock and stock > 0 and status == StoreProductStatus.OUT_OF_STOCK:
            cleaned_data["status"] = StoreProductStatus.ACTIVE

        return cleaned_data