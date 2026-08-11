from django import forms

from products.models import ProductDraftVariant, AttributeValue, CategoryAttribute, ProductVariant



class ProductDraftVariantForm(forms.ModelForm):
    """
    Wizard Step 2

    ProductDraftVariant oluşturur.

    Bu form yalnızca ProductDraftVariant modeline ait
    alanları yönetir.

    Sadece varyantın fiziksel bilgilerini yönetir.
    (Attribute seçimleri VariantAttributeForm tarafından yönetilir.)

    Ürün yayınlanana kadar gerçek ProductVariant oluşturulmaz.
    """

    class Meta:
        model = ProductDraftVariant

        fields = (
            "barcode",
        )

        labels = {
            "barcode": "Barkod (Opsiyonel)",
        }

        help_texts = {
            "barcode": (
                "EAN / UPC / GTIN barkodu varsa giriniz."
            ),
        }

        widgets = {
            "barcode": forms.TextInput(
                attrs={
                    "placeholder": "Örn: 8691234567890",
                    "autocomplete": "off",
                }
            ),
        }

    def clean_barcode(self):
        barcode = self.cleaned_data.get("barcode")

        if not barcode:
            return None

        barcode = "".join(barcode.split())

        if len(barcode) > 50:
            raise forms.ValidationError(
                "Barkod çok uzun."
            )
        
        if ProductVariant.objects.filter(
            barcode=barcode
        ).exists():

            raise forms.ValidationError(
                "Bu barkod aktif bir üründe kullanılmaktadır."
            )
        
        queryset = ProductDraftVariant.objects.filter(
            barcode=barcode
        )

        if self.instance.pk:
            queryset = queryset.exclude(
                pk=self.instance.pk
            )

        if queryset.exists():
            raise forms.ValidationError(
                "Bu barkod başka bir varyantta kullanılmaktadır."
            )
        return barcode
    


class VariantAttributeForm(forms.Form):
    """
    Wizard Step 2

    Ürünün kategorisine göre dinamik varyant alanlarını üretir.

    Örnek:

    Telefon
        Renk
        Depolama
        RAM

    Ayakkabı
        Numara
        Renk

    Kullanıcı ister mevcut AttributeValue seçer,
    ister yeni bir değer girer.

    Yeni girilen değer DraftVariantService  tarafından
    AttributeValue olarak oluşturulur.
    """

    def __init__(
        self,
        *args,
        draft=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.draft = draft
        self.variant_attributes = {}
        self.category_attributes = CategoryAttribute.objects.none()

        if draft is None:
            return

        self.category_attributes = (
            CategoryAttribute.objects
            .select_related("attribute")
            .filter(
                category=draft.category,
                is_variant=True,
            )
            .order_by(
                "sort_order",
                "attribute__name",
            )
        )

        for category_attribute in self.category_attributes:

            attribute = category_attribute.attribute

            self.variant_attributes[attribute.pk] = category_attribute


            #
            # Mevcut değer
            #
            self.fields[f"attribute_{attribute.pk}"] = (
                forms.ModelChoiceField(
                    queryset=AttributeValue.objects.filter(
                        attribute=attribute,
                        is_active=True,
                    ).order_by("value"),
                    required=False,
                    label=attribute.name,
                    empty_label="Seçiniz",
                    widget=forms.Select(
                        attrs={
                            "class": "pw-select",
                            "data-attribute-id": str(attribute.pk),
                        }
                    ),
                )
            )

            #
            # Yeni değer
            #
            if category_attribute.allow_custom_values:

                self.fields[
                    f"custom_attribute_{attribute.pk}"
                ] = forms.CharField(
                    required=False,
                    label="",
                    max_length=100,
                    widget=forms.TextInput(
                        attrs={
                            "class": "pw-custom-value",
                            "data-attribute-id": str(attribute.pk),
                            "placeholder": (
                                f"Yeni {attribute.name} giriniz"
                            ),
                            "autocomplete": "off",
                        }
                    ),
                )

    def clean(self):
        cleaned_data = super().clean()

        for attribute_id, category_attribute in (
            self.variant_attributes.items()
        ):

            attribute = category_attribute.attribute

            selected = cleaned_data.get(
                f"attribute_{attribute_id}"
            )

            custom = (
                cleaned_data.get(
                    f"custom_attribute_{attribute_id}"
                )
                or ""
            )

            custom = " ".join(custom.split()).strip() if custom else ""

            if custom and not category_attribute.allow_custom_values:
                self.add_error(
                    f"attribute_{attribute_id}",
                    "Bu özellik için yeni değer eklenemez.",
                )


            if category_attribute.allow_custom_values:
                cleaned_data[
                    f"custom_attribute_{attribute_id}"
                ] = custom
            #
            # Aynı anda ikisi birden girilemez.
            #
            if selected and custom:

                self.add_error(
                    f"custom_attribute_{attribute_id}",
                    (
                        "Mevcut değeri seçtiyseniz "
                        "yeni değer girmeyiniz."
                    ),
                )

            #
            # Zorunlu attribute
            #
            if (
                category_attribute.is_required
                and not selected
                and not custom
            ):

                self.add_error(
                    f"attribute_{attribute_id}",
                    (
                        f"{attribute.name} alanı zorunludur."
                    ),
                )

        return cleaned_data
    
    def get_attribute_fields(self):
        """
        Template'in dinamik alanları kolayca
        oluşturabilmesi için yardımcı yapı döndürür.
        """
    
        fields = []
    
        for attribute_id, category_attribute in (
            self.variant_attributes.items()
        ):
    
            fields.append(
                {
                    "category_attribute": category_attribute,
                    "select_field": self[
                        f"attribute_{attribute_id}"
                    ],
                    "custom_field": self.fields.get(
                        f"custom_attribute_{attribute_id}"
                    )
                    and self[
                        f"custom_attribute_{attribute_id}"
                    ],
                }
            )
    
        return fields
    
    def get_attribute_data(self):
        """
        Seçilen attribute değerlerini döndürür.
        """

        if not hasattr(self, "cleaned_data"):
            raise ValueError(
                "Önce form doğrulanmalıdır."
            )

        result = []

        for attribute_id, category_attribute in (
            self.variant_attributes.items()
        ):

            result.append(
                {
                    "category_attribute": category_attribute,
                    "attribute": category_attribute.attribute,
                    "selected": self.cleaned_data.get(
                        f"attribute_{attribute_id}"
                    ),
                    "custom": self.cleaned_data.get(
                        f"custom_attribute_{attribute_id}"
                    )
                    or None,
                }
            )

        return result
    
    def has_custom_values(self):
        return any(
            item["custom"]
            for item in self.get_attribute_data()
        )
    

class BulkVariantAttributeForm(forms.Form):
    """
    Wizard Step 2

    Aynı anda birden fazla varyant oluşturmak için
    attribute seçimlerini yönetir.

    Örnek:

    Renk
        ☑ Siyah
        ☑ Beyaz

    Beden
        ☑ S
        ☑ M
        ☑ L

    Servise şu formatta veri sağlar:

    [
        {
            "attribute": Color,
            "values": [Black, White],
        },
        {
            "attribute": Size,
            "values": [S, M, L],
        },
    ]
    """

    def __init__(
        self,
        *args,
        draft=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.draft = draft
        self.variant_attributes = {}
        self.category_attributes = CategoryAttribute.objects.none()

        if draft is None:
            return

        self.category_attributes = (
            CategoryAttribute.objects
            .select_related("attribute")
            .filter(
                category=draft.category,
                is_variant=True,
            )
            .order_by(
                "sort_order",
                "attribute__name",
            )
        )

        for category_attribute in self.category_attributes:

            attribute = category_attribute.attribute

            self.variant_attributes[
                attribute.pk
            ] = category_attribute

            self.fields[
                f"attribute_{attribute.pk}"
            ] = forms.ModelMultipleChoiceField(
                queryset=(
                    AttributeValue.objects
                    .filter(
                        attribute=attribute,
                        is_active=True,
                    )
                    .order_by("value")
                ),
                required=False,
                label=attribute.name,
                widget=forms.CheckboxSelectMultiple(attrs={
                    "class": "pw-checkbox-group",
                    }),
                )

    def clean(self):

        cleaned_data = super().clean()

        has_selection = False

        for (
            attribute_id,
            category_attribute,
        ) in self.variant_attributes.items():

            attribute = category_attribute.attribute

            selected = cleaned_data.get(
                f"attribute_{attribute_id}"
            )

            if selected:
                has_selection = True

            if (
                category_attribute.is_required
                and not selected
            ):

                self.add_error(
                    f"attribute_{attribute_id}",
                    (
                        f"{attribute.name} alanı zorunludur."
                    ),
                )

        if not has_selection:

            raise forms.ValidationError(
                "En az bir varyant değeri seçmelisiniz."
            )

        return cleaned_data

    def get_attribute_fields(self):
        """
        Template'in dinamik alanları kolayca
        oluşturabilmesi için yardımcı yapı döndürür.
        """

        fields = []

        for (
            attribute_id,
            category_attribute,
        ) in self.variant_attributes.items():

            fields.append(
                {
                    "category_attribute": category_attribute,
                    "field": self[
                        f"attribute_{attribute_id}"
                    ],
                }
            )

        return fields

    def get_attribute_data(self):
        """
        Seçilen attribute değerlerini döndürür.

        Dönen yapı:

        [
            {
                "attribute": Attribute,
                "values": QuerySet[AttributeValue],
            },
        ]
        """

        if not hasattr(self, "cleaned_data"):
            raise ValueError(
                "Önce form doğrulanmalıdır."
            )

        result = []

        for (
            attribute_id,
            category_attribute,
        ) in self.variant_attributes.items():

            result.append(
                {
                    "category_attribute": category_attribute,
                    "attribute": category_attribute.attribute,
                    "values": list(
                        self.cleaned_data.get(
                            f"attribute_{attribute_id}"
                        )
                    ),
                }
            )

        return result

    def selected_count(self):
        """
        Toplam seçilen AttributeValue sayısını döndürür.
        """

        return sum(
            len(item["values"])
            for item in self.get_attribute_data()
        )
    
    def get_combination_count(self):
        """
        Seçilen özellik değerlerinin yaratacağı toplam kombinasyon sayısını hesaplar.
        """
        if not hasattr(self, "cleaned_data"):
            return 0

        total = 1
        has_selection = False

        for item in self.get_attribute_data():
            count = len(item["values"])
            if count > 0:
                total *= count
                has_selection = True

        return total if has_selection else 0


