from django import forms

from products.models import ProductDraftVariant


class VariantOfferForm(forms.ModelForm):
    """
    Wizard Step 4

    Bir taslak varyantın satış bilgilerinin
    düzenlenmesi için kullanılır.
    """

    class Meta:

        model = ProductDraftVariant

        fields = [
            "price",
            "stock",
            "sku",
            "barcode",
            "is_default",
        ]

        widgets = {

            "price": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0.01",
                }
            ),

            "stock": forms.NumberInput(
                attrs={
                    "min": "0",
                }
            ),

            "sku": forms.TextInput(
                attrs={
                    "placeholder": "Opsiyonel (SKU)",
                }
            ),

            "barcode": forms.TextInput(
                attrs={
                    "placeholder": "Opsiyonel (EAN/UPC)",
                }
            ),

            "is_default": forms.CheckboxInput(),

        }

        labels = {

            "price": "Fiyat",

            "stock": "Stok",

            "sku": "SKU",

            "barcode": "Barkod",

            "is_default": "Varsayılan",

        }

    def clean_sku(self):

        sku = self.cleaned_data.get("sku")

        if sku:
            sku = sku.strip()

        return sku or None

    def clean_barcode(self):

        barcode = self.cleaned_data.get("barcode")

        if barcode:
            barcode = barcode.replace(" ", "").strip()

        return barcode or None