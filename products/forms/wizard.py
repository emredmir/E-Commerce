# from django import forms
# from products.models import ProductDraft, Category, Brand, BrandRequest


# class ProductWizardStep1Form(forms.ModelForm):
#     """
#     Wizard Step 1

#     Amaç:
#     Ürünün sadece temel bilgilerini oluşturur.

#     Bu adımda;

#     - Ürün adı
#     - Açıklama
#     - Ana kategori
#     - Alt kategori
#     - Marka

#     seçilir.

#     Ürün DRAFT olarak oluşur.

#     Varyant, resim ve fiyat henüz oluşturulmaz.
#     """
#     parent_category = forms.ModelChoiceField(
#         queryset=Category.objects.none(),
#         required=True,
#         label="Ana Kategori",
#         empty_label="Ana kategori seçiniz",
#     )

#     category = forms.ModelChoiceField(
#         queryset=Category.objects.none(),
#         required=True,
#         label="Alt Kategori",
#         empty_label="Önce ana kategori seçiniz",
#     )

#     brand = forms.ModelChoiceField(
#         queryset=Brand.objects.none(),
#         required=False,
#         label="Marka (Opsiyonel)",
#         empty_label="Marka seçiniz (Opsiyonel)",
#     )


#     class Meta:
#         model = ProductDraft

#         fields = (
#             "name",
#             "description",
#             "category",
#             "brand",
#         )

#         widgets = {
#             "description": forms.Textarea(
#                 attrs={
#                     "rows": 6,
#                 }
#             ),
#         }

#         labels = {
#             'name':        'Ürün Adı',
#             'description': 'Genel Açıklama',
#             'category':    'Alt Kategori',
#             'brand':       'Marka (Opsiyonel)',
#         }

#     def __init__(self, *args, request=None, **kwargs):
#         self.request = request
#         super().__init__(*args, **kwargs)

#         # --------------------------------------------------
#         # Ana kategoriler
#         # --------------------------------------------------
#         self.fields["parent_category"].queryset = (
#             Category.objects.filter(
#                 parent__isnull=True,
#                 is_active=True,
#             ).order_by("name")
#         )

#         # --------------------------------------------------
#         # Alt kategori
#         # --------------------------------------------------
#         self.fields["category"].queryset = Category.objects.none()

#         parent_id = (
#             self.data.get("parent_category")
#             or self.initial.get("parent_category")
#             or (
#                 self.instance.category.parent_id
#                 if self.instance.pk and self.instance.category
#                 else None
#             )
#         )

#         if parent_id:
#             self.fields["category"].queryset = (
#                 Category.objects.filter(
#                     parent_id=parent_id,
#                     is_active=True,
#                 ).order_by("name")
#             )

#         # --------------------------------------------------
#         # Marka
#         # --------------------------------------------------
#         self.fields["brand"].queryset = Brand.objects.none()

#         category_id = (
#             self.data.get("category")
#             or self.initial.get("category")
#             or (
#                 self.instance.category_id
#                 if self.instance.pk
#                 else None
#             )
#         )

#         if category_id:
#             self.fields["brand"].queryset = (
#                 Brand.objects.filter(
#                     brand_categories__category_id=category_id,
#                     is_active=True,
#                 )
#                 .distinct()
#                 .order_by("name")
#             )

#         if self.instance.pk:


#             if self.instance.category:
#                 self.fields["parent_category"].initial = (
#                     self.instance.category.parent
#                 )

#                 self.fields["category"].initial = (
#                     self.instance.category
#                 )

#             if self.instance.brand:
            
#                 self.fields["brand"].initial = (
#                     self.instance.brand
#                 )

#         # --------------------------------------------------
#         # Widget Ayarları
#         # --------------------------------------------------
#         self.fields["name"].widget.attrs.update({
#             "placeholder": "Örn: iPhone 15 Pro 256 GB",
#             "autocomplete": "off",
#         })
    
#         self.fields["description"].widget.attrs.update({
#             "placeholder": "Ürün hakkında genel bir açıklama yazın...",
#         })

    

#         # self.fields["category"].empty_label = "Kategori seçiniz"


#         # self.fields["brand"].empty_label = "Marka seçiniz (Opsiyonel)"

#     def clean_name(self):
#         name = " ".join(self.cleaned_data.get("name", "").split())
#         if not name:
#             raise forms.ValidationError("Ürün adı boş olamaz.")

#         return name.strip()
    
#     def clean_category(self):
#         category = self.cleaned_data["category"]

#         if category.parent is None:
#             raise forms.ValidationError(
#                 "Lütfen bir alt kategori seçin."
#             )

#         return category
    
#     def clean(self):
#         cleaned_data = super().clean()

#         category = cleaned_data.get("category")
#         brand = cleaned_data.get("brand")

#         if category and brand:
#             exists = brand.brand_categories.filter(
#                 category=category
#             ).exists()

#             if not exists:
#                 self.add_error(
#                     "brand",
#                     "Seçilen marka bu kategoriye ait değildir."
#                 )

#         return cleaned_data
    


# class BrandRequestForm(forms.ModelForm):
#     """
#     Satıcının sisteme yeni marka eklenmesini talep ettiği form.
#     """

#     class Meta:
#         model = BrandRequest

#         fields = (
#             "brand_name",
#             "note",
#         )

#         widgets = {
#             "note": forms.Textarea(
#                 attrs={
#                     "rows": 4,
#                     "placeholder": (
#                         "Varsa marka hakkında ek bilgi yazabilirsiniz."
#                     ),
#                 }
#             ),
#         }

#         labels = {
#             "brand_name": "Marka Adı",
#             "note": "Açıklama (Opsiyonel)",
#         }

#         help_texts = {
#             "brand_name": (
#                 "Listede bulunmayan marka adını giriniz."
#             ),
#         }

#     def __init__(self, *args, seller=None, category=None, **kwargs):
#         self.seller = seller
#         self.category = category

#         super().__init__(*args, **kwargs)


#         self.fields["brand_name"].widget.attrs.update({
#             "placeholder": "Örn: Anker",
#             "autocomplete": "off",
#         })

#     def clean_brand_name(self):
#         brand_name = " ".join(
#             self.cleaned_data["brand_name"].split()
#         ).strip()

#         if not brand_name:
#             raise forms.ValidationError(
#                 "Marka adı boş olamaz."
#             )

#         return brand_name

#     def clean(self):
#         cleaned_data = super().clean()
#         brand_name = cleaned_data.get("brand_name")
#         category = self.category

#         if not category or not brand_name:
#             return cleaned_data

#         brand = Brand.objects.filter(
#             name__iexact=brand_name,
#         ).first()
        
#         if (
#             brand
#             and brand.brand_categories.filter(
#                 category=category,
#             ).exists()
#         ):
#             self.add_error(
#                 "brand_name",
#                 "Bu marka bu kategori için zaten bulunmaktadır.",
#             )
#         if (
#             self.seller
#             and BrandRequest.objects.filter(
#                 seller=self.seller,
#                 category=category,
#                 brand_name__iexact=brand_name,
#                 status=BrandRequest.Status.PENDING,
#             ).exists()
#         ):
#             self.add_error(
#                 "brand_name",
#                 "Bu marka için bekleyen bir talebiniz bulunmaktadır.",
#             )

#         return cleaned_data