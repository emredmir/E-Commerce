from django import forms
from products.models import Brand, Category


class ProductFilterForm(forms.Form):
    """
    Müşteri vitrinindeki ürün listeleme sayfası için filtre formu.

    GET parametreleriyle çalışır ve seçilen filtrelerin URL'e
    yansımasını sağlar.
    """

    ORDERING_CHOICES = [
        ('', 'Önerilen Sıralama'),
        ('-sold', 'En Çok Satanlar'),
        ('min_price', 'En Düşük Fiyat'),
        ('-min_price', 'En Yüksek Fiyat'),
        ('-created', 'En Yeniler'),
    ]

    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(
            is_active=True
        ).order_by('name'),
        required=False,
        empty_label='Tüm Kategoriler',
        label='Kategori',
        widget=forms.Select(
            attrs={
                'class': 'pw-select',
            }
        ),
    )

    brand = forms.ModelMultipleChoiceField(
        queryset=Brand.objects.filter(
            is_active=True
        ).order_by('name'),
        required=False,
        label='Marka',
        widget=forms.CheckboxSelectMultiple(
            attrs={
                'class': 'pw-select',
            }
        ),
    )

    min_price = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        label='Min Fiyat',
        widget=forms.NumberInput(
            attrs={
                'class': 'pw-input',
                'placeholder': 'En Az',
                'min': '0',
                'step': '0.01',
            }
        ),
    )

    max_price = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        label='Max Fiyat',
        widget=forms.NumberInput(
            attrs={
                'class': 'pw-input',
                'placeholder': 'En Çok',
                'min': '0',
                'step': '0.01',
            }
        ),
    )

    price_range = forms.MultipleChoiceField(
        required=False,
        choices=[
            ('0-100', '0-100 TL'),
            ('100-500', '100-500 TL'),
            ('500-1000', '500-1000 TL'),
            ('1000+', '1000 TL+'),
        ]
    )

    ordering = forms.ChoiceField(
        choices=ORDERING_CHOICES,
        required=False,
        label='Sıralama',
        widget=forms.Select(
            attrs={
                'class': 'pw-select',
                'onchange': 'this.form.submit();',
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()

        min_price = cleaned_data.get('min_price')
        max_price = cleaned_data.get('max_price')

        if (
            min_price is not None
            and max_price is not None
            and min_price > max_price
        ):
            raise forms.ValidationError(
                'Minimum fiyat, maksimum fiyattan büyük olamaz.'
            )

        return cleaned_data
