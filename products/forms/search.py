from django import forms
from ..models import Category, Brand

# SONRADAN KALDIR!!! ürün ekleme akışı değiştiği için kaldırılacak
class ProductSearchForm(forms.Form):
    """
    Satıcının ürün eklerken kataloğu arayacağı form.
    Sonuçta ürün varsa 'Ben de Satıyorum', yoksa 'Yeni Ekle' akışına gider.

    Kullanım:
        form = ProductSearchForm(request.GET)
        if form.is_valid():
            query = form.cleaned_data['query']
    """
    query = forms.CharField(
        label="Ürün Adı veya Barkod",
        max_length=255,
        widget=forms.TextInput(attrs={
            'class':       'form-control form-control-lg',
            'placeholder': 'Ürün adı veya barkod ile arayın...',
            'autofocus':   True,
        })
    )

    def clean_query(self):
        query = " ".join(self.cleaned_data.get("query", "").split())
        if len(query) < 2:
            raise forms.ValidationError("En az 2 karakter giriniz.")
        return query


class ProductFilterForm(forms.Form):
    """
    Müşteri vitrininde ürün listeleme sayfası için filtre formu.
    GET parametreleriyle çalışır, URL'e yansır.

    Kullanım:
        form = ProductFilterForm(request.GET)
        if form.is_valid():
            category  = form.cleaned_data['category']
            brand     = form.cleaned_data['brand']
            min_price = form.cleaned_data['min_price']
            max_price = form.cleaned_data['max_price']
            ordering  = form.cleaned_data['ordering']
    """

    ORDERING_CHOICES = [
        ('',           'Önerilen'),
        ('min_price',  'En Düşük Fiyat'),
        ('-min_price', 'En Yüksek Fiyat'),
        ('-sold',      'En Çok Satan'),
        ('-created',   'En Yeni'),
    ]

    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(is_active=True).order_by('name'),
        required=False,
        empty_label="Tüm Kategoriler",
        label="Kategori",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(is_active=True).order_by('name'),
        required=False,
        empty_label="Tüm Markalar",
        label="Marka",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    min_price = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        label="Min Fiyat",
        widget=forms.NumberInput(attrs={
            'class':       'form-control',
            'placeholder': '0',
        })
    )

    max_price = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        label="Max Fiyat",
        widget=forms.NumberInput(attrs={
            'class':       'form-control',
            'placeholder': 'Örn. 99999',
        })
    )

    ordering = forms.ChoiceField(
        choices=ORDERING_CHOICES,
        required=False,
        label="Sıralama",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def clean(self):
        cleaned_data = super().clean()
        min_price = cleaned_data.get('min_price')
        max_price = cleaned_data.get('max_price')

        if (min_price is not None and max_price is not None and min_price > max_price):
            raise forms.ValidationError(
                "Minimum fiyat, maksimum fiyattan büyük olamaz."
            )
        return cleaned_data