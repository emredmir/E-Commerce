from django import forms
from .mixins import BootstrapFormMixin, StoreProductValidationMixin
from ..models import StoreProduct, StoreProductStatus, ProductVariant

#kaldırılacak

class StoreProductForm(BootstrapFormMixin, StoreProductValidationMixin, forms.ModelForm):
    """
    Satıcının mağazasına yeni ürün teklifi oluştururken kullandığı form.
    'store' alanı view tarafından set edilir, formda gösterilmez.

    Kullanım:
        form = StoreProductForm(request.POST, store=store)
        if form.is_valid():
            offer = form.save(commit=False)
            offer.store = store
            offer.save()
    """

    class Meta:
        model = StoreProduct
        fields = ['variant', 'sku', 'price', 'stock', 'seller_notes', 'status']
        widgets = {
            'seller_notes': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Ürün hakkında ek bilgi (opsiyonel)'
            }),
            'status': forms.Select(choices=[
                (StoreProductStatus.DRAFT,  'Taslak Olarak Kaydet'),
                (StoreProductStatus.ACTIVE, 'Hemen Yayına Al'),
            ]),
        }
        labels = {
            'variant':      'Ürün Varyantı',
            'sku':          'Stok Kodu (SKU)',
            'price':        'Satış Fiyatı (₺)',
            'stock':        'Stok Adedi',
            'seller_notes': 'Ek Açıklama',
            'status':       'Yayın Durumu',
        }
        help_texts = {
            'sku':   'Opsiyonel. Kendi stok takip sisteminiz için kullanılır.',
            'price': 'KDV dahil satış fiyatı giriniz.',
            'stock': 'Mevcut stok adedini giriniz.',
        }

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._store = store
        self.fields['sku'].required = False

        # Sadece aktif varyantları göster
        # Bu mağazanın zaten teklif verdiği varyantları çıkar
        active_variants_qs = (
            ProductVariant.objects
            .filter(is_active=True, product__is_active=True)
            .select_related('product')
            .prefetch_related('attribute_values')
        )

        if store:
            # Mağazanın zaten aktif teklif verdiği varyantları hariç tut
            # Arşivlenmiş teklifler hariç — aynı varyanta tekrar teklif verilebilir
            existing_variant_ids = StoreProduct.objects.filter(
                store=store
            ).exclude(
                status=StoreProductStatus.ARCHIVED
            ).values_list('variant_id', flat=True)

            active_variants_qs = active_variants_qs.exclude(pk__in=existing_variant_ids)

        self.fields['variant'].queryset = active_variants_qs

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        stock = cleaned_data.get('stock')

        # Stok 0 iken yayına almayı engelle
        # save() zaten OUT_OF_STOCK yapacak ama önceden kullanıcıyı bilgilendir
        if status == StoreProductStatus.ACTIVE and stock == 0:
            self.add_error(
                'stock',
                "Stok adedi 0 iken yayına alamazsınız. "
                "Stok ekleyin veya 'Taslak Olarak Kaydet' seçeneğini kullanın."
            )

        # store + variant unique kontrolü
        # Model constraint bunu DB'de de yakalar ama kullanıcıya önceden bildir
        variant = cleaned_data.get('variant')
        if self._store and variant:
            if StoreProduct.objects.filter(
                store=self._store,
                variant=variant
            ).exclude(status=StoreProductStatus.ARCHIVED).exists():
                self.add_error(
                    'variant',
                    "Bu varyant için mağazanızda zaten aktif bir teklif bulunuyor."
                )

        return cleaned_data

#Silinecek
class StoreProductUpdateForm(BootstrapFormMixin, StoreProductValidationMixin, forms.ModelForm):
    """
    Satıcının mevcut teklifini güncellerken kullandığı form.
    Varyant değiştirilemez — sadece fiyat, stok, durum, notlar güncellenir.
    OUT_OF_STOCK otomatik yönetildiği için satıcıya seçenek olarak sunulmaz.

    Kullanım:
        form = StoreProductUpdateForm(request.POST, instance=store_product)
    """

    class Meta:
        model = StoreProduct
        fields = ['sku', 'price', 'stock', 'seller_notes', 'status']
        widgets = {
            'seller_notes': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Ürün hakkında ek bilgi (opsiyonel)'
            }),
        }
        labels = {
            'sku':          'Stok Kodu (SKU)',
            'price':        'Satış Fiyatı (₺)',
            'stock':        'Stok Adedi',
            'seller_notes': 'Ek Açıklama',
            'status':       'Yayın Durumu',
        }
        help_texts = {
            'status': 'Stok adedi 0 olduğunda ürün otomatik olarak "Tükendi" durumuna geçer.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sku'].required = False

        self.fields['status'].choices =[
            (StoreProductStatus.DRAFT,    'Taslak'),
            (StoreProductStatus.ACTIVE,   'Yayında'),
            # OUT_OF_STOCK buraya dahil değil — save() otomatik yönetir
        ]

        # Mevcut durum OUT_OF_STOCK ise seçeneklere ekle — kullanıcı görebilsin
        # ama dropdown'dan seçemesin, sadece bilgi amaçlı
        if self.instance and self.instance.status == StoreProductStatus.OUT_OF_STOCK:
            self.fields['status'].widget.choices = [
                (StoreProductStatus.OUT_OF_STOCK, 'Tükendi (Otomatik)'),
                (StoreProductStatus.ACTIVE,       'Yeniden Yayına Al'),
                (StoreProductStatus.DRAFT,         'Taslağa Al'),
                # (StoreProductStatus.ARCHIVED,      'Arşivle'),
            ]

    def clean(self):
        cleaned_data = super().clean()
        # status = cleaned_data.get('status')
        # stock = cleaned_data.get('stock')

        # if status == StoreProductStatus.ACTIVE and stock == 0:
        #     self.add_error(
        #         'stock',
        #         "Stok adedi 0 iken yayına alamazsınız."
        #     )

        return cleaned_data