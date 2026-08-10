from django import forms
from PIL import Image as PILImage
from .mixins import BootstrapFormMixin
from ..models import Product, ProductVariant, ProductImage, AttributeValue


# ---------------------------------------------------------
# Global Ürün Formu
# ---------------------------------------------------------

class ProductForm(BootstrapFormMixin, forms.ModelForm):
    """
    Katalogda olmayan bir ürünü sisteme eklemek için kullanılır.
    Admin ve satıcı dashboard'unda kullanılır.

    Kullanım:
        form = ProductForm(request.POST)
        form = ProductForm(request.POST, instance=product)  # güncelleme
    """
    class Meta:
        model = Product
        fields = ['name', 'description', 'category', 'brand']
        widgets = {
            'description': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Ürün hakkında genel bilgi girin...'
            }),
        }
        labels = {
            'name':        'Ürün Adı',
            'description': 'Genel Açıklama',
            'category':    'Kategori',
            'brand':       'Marka (Opsiyonel)',
        }

    def clean_name(self):
        name = " ".join(self.cleaned_data.get("name", "").split())
        if not name:
            raise forms.ValidationError("Ürün adı boş olamaz.")
        # Aynı isimde başka ürün var mı?
        qs = Product.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                "Bu isimde bir ürün zaten sistemde mevcut. "
                "Ürünü katalogdan bulup 'Ben de Satıyorum' seçeneğini kullanın."
            )
        return name
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Güncelleme modunda başlık değiştirilemez
        if self.instance and self.instance.pk:
            self.fields['name'].widget.attrs['readonly'] = True
            self.fields['name'].widget.attrs['style'] = 'background: var(--gray-50); cursor: not-allowed;'
            self.fields['name'].help_text = 'Ürün başlığı değiştirilemez.'


# ---------------------------------------------------------
# Ürün Varyantı Formu
# ---------------------------------------------------------

class ProductVariantForm(forms.ModelForm):
    """
    Varyant oluşturma/güncelleme için kullanılır.
    Admin'de filter_horizontal bu formun widget'larını override eder.
    Satıcı dashboard'unda CheckboxSelectMultiple ile attribute seçimi yapılır.

    Kullanım:
        form = ProductVariantForm(request.POST, instance=variant, product=product)
    """
    class Meta:
        model = ProductVariant
        fields = ['barcode', 'attribute_values']
        labels = {
            'barcode':          'Barkod (EAN/UPC)',
            'attribute_values': 'Özellikler (Renk, Beden vb.)',
        }
        help_texts = {
            'barcode': 'Opsiyonel. Aynı barkod birden fazla varyanta atanamaz.',
        }
        widgets = {
            'attribute_values': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Sadece aktif attribute_values'leri göster
        self.fields['attribute_values'].queryset = (
            AttributeValue.objects
            .filter(attribute__is_active=True)
            .select_related('attribute')
            .order_by('attribute__name', 'value')
        )
        self.fields['attribute_values'].required = False

        self._product = product

    def clean_barcode(self):
        barcode = self.cleaned_data.get('barcode', '')
        if barcode:
            barcode = barcode.strip().upper()
            qs = ProductVariant.objects.filter(barcode=barcode)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "Bu barkod başka bir varyant tarafından kullanılıyor."
                )
        # Boş string yerine None kaydet
        return barcode or None

    def clean_attribute_values(self):
        values = self.cleaned_data.get('attribute_values', [])

        # Aynı attribute'dan birden fazla value kontrolü
        attribute_ids = [v.attribute_id for v in values]
        if len(attribute_ids) != len(set(attribute_ids)):
            seen = set()
            duplicates = set()
            for v in values:
                if v.attribute_id in seen:
                    duplicates.add(v.attribute.name)
                seen.add(v.attribute_id)
            raise forms.ValidationError(
                f"Şu özellik(ler) bir varyanta birden fazla değerle eklenemez: "
                f"{', '.join(duplicates)}"
            )

        # Aynı üründe aynı özellik kombinasyonu kontrolü
        if self._product and values:
            qs = ProductVariant.objects.filter(product=self._product)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            for variant in qs.prefetch_related('attribute_values'):
                existing_ids = set(v.id for v in variant.attribute_values.all())
                new_ids = set(v.id for v in values)
                if existing_ids == new_ids:
                    raise forms.ValidationError(
                        "Bu ürüne ait aynı özellik kombinasyonuna sahip bir varyant zaten var."
                    )
        return values


# ---------------------------------------------------------
# Ürün Görseli Formu
# ---------------------------------------------------------

class ProductImageForm(BootstrapFormMixin, forms.ModelForm):
    """
    Ürüne veya varyanta görsel eklemek için kullanılır.

    Kullanım:
        form = ProductImageForm(
            request.POST,
            request.FILES,
            product=product  # variant filtrelemesi için zorunlu
        )
    """
    class Meta:
        model = ProductImage
        fields = ['image', 'is_main', 'sort_order']
        labels = {
            'image':      'Görsel',
            'is_main':    'Kapak Fotoğrafı Yap',
            'sort_order': 'Sıralama',
        }
        help_texts = {
            'sort_order': 'Küçük sayı önce görünür. (0, 1, 2, ...)',
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["sort_order"].required = False
        self.fields["is_main"].required = False

        self.fields["sort_order"].initial = 0
        self.fields["is_main"].initial = False

        # Sadece ilgili ürünün aktif varyantlarını göster
        # if product:
        #     self.fields['variant'].queryset = (
        #         ProductVariant.objects
        #         .filter(product=product, is_active=True)
        #         .prefetch_related('attribute_values')
        #     )
        # else:
        #     self.fields['variant'].queryset = ProductVariant.objects.none()

        # self.fields['variant'].required = False

    def clean_image(self):
        image = self.cleaned_data.get('image')

        # Mevcut görselde değişiklik yoksa doğrulama atla
        if not image or not hasattr(image, 'size'):
            return image

        # Boyut kontrolü: maksimum 5MB
        if image.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Görsel boyutu 5MB'dan büyük olamaz.")

        # Gerçek resim mi kontrolü
        try:
            img = PILImage.open(image)
            
            if img.width > 8000 or img.height > 8000:
                raise forms.ValidationError(
                "Görsel genişliği veya yüksekliği 8000 px'den büyük olamaz."
            )

            img.verify()
            image.seek(0)
        except Exception:
            raise forms.ValidationError(
                "Geçerli bir resim dosyası yükleyin (JPEG, PNG vb.)."
            )

        return image
