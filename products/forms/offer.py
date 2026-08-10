import json

from django import forms
from .wizard.step3 import MultipleFileField, MultipleFileInput


class OfferCreateForm(forms.Form):
    """
    Mevcut bir Product için satıcının tekliflerini oluşturma formu.

    Frontend, varyant tekliflerini JSON olarak
    `variants_data` alanında gönderir.

    Örnek:

    [
        {
            "type": "existing",
            "id": 12,
            "price": "65000.00",
            "stock": 10,
            "sku": "IPH15-BLK-128",
            "barcode": "123456789"
        },
        {
            "type": "custom",
            "id": 25,
            "price": "70000.00",
            "stock": 5,
            "sku": "IPH15-TIT-1TB",
            "barcode": null
        }
    ]
    """

    variants_data = forms.CharField(
        required=True,
        widget=forms.HiddenInput(),
    )

    def clean_variants_data(self):
        value = self.cleaned_data["variants_data"]

        try:
            data = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            raise forms.ValidationError(
                "Geçersiz teklif verisi."
            )

        if not isinstance(data, list):
            raise forms.ValidationError(
                "Geçersiz teklif formatı."
            )

        if not data:
            raise forms.ValidationError(
                "En az bir varyant gönderilmelidir."
            )

        return data

class OfferCustomVariantForm(forms.Form):
    """
    Offer ekranında katalogda bulunmayan yeni
    bir varyant eklemek için kullanılan form.

    Örneğin:

    Renk   → Siyah
    Hafıza → 1TB
    Görsel → image files
    """

    attributes_data = forms.CharField(
        required=True,
        widget=forms.HiddenInput(),
    )

    images = MultipleFileField(
        required=False,
        widget=MultipleFileInput(
            attrs={
                "class": "js-file-input",
                "multiple": True,
                "accept": "image/*",
            }
        ),
    )

    def clean_attributes_data(self):
        value = self.cleaned_data["attributes_data"]

        if not value:
            raise forms.ValidationError(
                "En az bir özellik seçilmelidir."
            )

        return value