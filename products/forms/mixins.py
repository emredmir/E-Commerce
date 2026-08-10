from django import forms

#KALDIRILACAK / Eski view için mixin

class BootstrapFormMixin:     
    """
    Form alanlarına otomatik Bootstrap CSS sınıfları ekler.
    Widget türüne göre doğru sınıfı seçer.

    Kullanım:
        class MyForm(BootstrapFormMixin, forms.ModelForm):
            ...

    Not: forms.ModelForm'dan ÖNCE yazılmalı (MRO sırası).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault('class', 'form-select')
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                # Template tarafında yönetilir, otomatik class eklenmez
                pass
            else:
                widget.attrs.setdefault('class', 'form-control')


class StoreProductValidationMixin:
    """
    StoreProduct formlarında tekrar eden alan validasyonlarını toplar.
    StoreProductForm ve StoreProductUpdateForm ikisi de kullanır.

    Kullanım:
        class StoreProductForm(BootstrapFormMixin, StoreProductValidationMixin, forms.ModelForm):
            ...
    """
    """
        StoreProduct formlarında ortak validasyonları içerir.

        Not:
        Bu mixin yalnızca ilgili alanlar formda mevcutsa kullanılmalıdır.
    """
    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is not None and price <= 0:
            raise forms.ValidationError("Satış fiyatı 0'dan büyük olmalıdır.")
        return price

    def clean_stock(self):
        stock = self.cleaned_data.get('stock')
        if stock is not None and stock < 0:
            raise forms.ValidationError("Stok adedi negatif olamaz.")
        return stock

    def clean_sku(self):
        sku = self.cleaned_data.get('sku', '')
        # Boş string yerine None kaydet — unique_store_sku constraint None'ları atlar
        return sku.strip() or None


