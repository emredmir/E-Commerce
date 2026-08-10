from django import forms
from PIL import Image
from django.core.exceptions import ValidationError

from products.models import CategoryAttribute, ProductDraftImage, AttributeValue


class ImageGroupForm(forms.Form):
    """
    Wizard Step 3

    Görsel grubu oluşturma/güncelleme formu.
    """

    def __init__(
        self,
        *args,
        draft,
        instance=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.draft = draft
        self.instance = instance

        used_value_ids = set(
            AttributeValue.objects.filter(
                draft_variants__draft=draft,
                draft_variants__is_active=True # Silinmiş varyantların renklerini gösterme
            ).values_list("id", flat=True)
        )

        visual_attributes = (
            CategoryAttribute.objects
            .filter(
                category=draft.category,
                is_visual=True,
            )
            .select_related(
                "attribute",
            )
            .prefetch_related(
                "attribute__values",
            )
            .order_by(
                "sort_order",
                "id",
            )
        )

        for category_attribute in visual_attributes:

            attribute = category_attribute.attribute

            filtered_values = attribute.values.filter(id__in=used_value_ids).order_by("value")

            if not filtered_values.exists():
                continue

            # Seçenek sayısı 1 ise bunu "sabit" kabul et
            is_single_choice = filtered_values.count() == 1

            self.fields[
                f"attribute_{attribute.pk}"
            ] = forms.ModelChoiceField(
                queryset=filtered_values,
                required=is_single_choice,
                empty_label=None if is_single_choice else "Seçiniz",
                label=attribute.name,
            )

        if instance is None:
            return

        for value in instance.visual_attribute_values.all():

            field_name = (
                f"attribute_{value.attribute_id}"
            )

            if field_name in self.fields:
                self.initial[field_name] = value

    def clean(self):

        cleaned_data = super().clean()

        selections = {}

        has_variable_fields = False
        variable_selections_count = 0

        for field_name, value in cleaned_data.items():

            field = self.fields.get(field_name)
            
            # Bu alanın birden fazla seçeneği var mıydı? (empty_label boş değilse değişkendir)
            if field and field.empty_label is not None:
                has_variable_fields = True
                if value is not None:
                    variable_selections_count += 1

            if value is None:
                continue

            attribute_id = int(
                field_name.replace(
                    "attribute_",
                    "",
                )
            )

            selections[attribute_id] = [
                value.pk,
            ]

        # Sadece kilitli (tek seçenekli) alanlar gönderilmişse, kullanıcı aslında hiçbir "yeni" seçim yapmamıştır.
        if has_variable_fields and variable_selections_count == 0:
            raise ValidationError("Yeni bir grup oluşturmak için, birden fazla seçeneği olan özelliklerden en az birini seçmelisiniz.")
        elif not has_variable_fields and not selections:
            raise ValidationError("Yeni bir grup oluşturmak için en az bir özellik seçmelisiniz.")

        cleaned_data["selections"] = selections

        return cleaned_data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    """
    Django'nun standart FileField'ı listeleri kabul etmez. 
    MultipleFileInput list döndürdüğü için bunu işleyecek özel Field.
    """
    def to_python(self, data):
        if data in self.empty_values:
            return []
        
        # Gelen data tekil bir dosyaysa listeye çeviriyoruz
        if not isinstance(data, list):
            data = [data]
        
        # İçindeki her bir dosyanın geçerli bir File objesi olup olmadığını denetliyoruz
        for d in data:
            try:
                file_name = d.name
                file_size = d.size
            except AttributeError:
                raise ValidationError(self.error_messages['invalid'], code='invalid')
        
        return data

    def clean(self, data, initial=None):
        if not data and self.required:
            raise ValidationError(self.error_messages['empty'], code='empty')
        return self.to_python(data)

class ImageUploadForm(forms.Form):
    """
    Wizard Step 3

    Bir görsel grubuna bir veya daha fazla
    görsel yüklemek için kullanılır.
    """

    MAX_IMAGE_COUNT = 20

    images = MultipleFileField(
        widget=MultipleFileInput(
            attrs={
                "class": "js-file-input",
                "multiple": True,
                "accept": "image/*",
            }
        ),
        label="Görseller",
    )

    def clean_images(self):

        images = self.cleaned_data.get("images", [])

        if not images:
            raise ValidationError(
                "En az bir görsel seçmelisiniz."
            )

        if len(images) > self.MAX_IMAGE_COUNT:
            raise ValidationError(
                f"En fazla {self.MAX_IMAGE_COUNT} görsel yükleyebilirsiniz."
            )


        allowed_formats = {
            "JPEG",
            "PNG",
            "WEBP",
        }

        max_size = 10 * 1024 * 1024  # 10 MB

        for image in images:
            try:
                img = Image.open(image)
                img.verify()

            except Exception:
                raise ValidationError(
                    "Geçersiz görsel dosyası."
                )

            image.seek(0)

            img = Image.open(image)
            if img.format not in allowed_formats:
                raise ValidationError(
                    "Desteklenmeyen görsel formatı."
                )

            if image.size > max_size:
                raise ValidationError(
                    "Her görsel en fazla 10 MB olabilir."
                )

            image.seek(0)

        return images



class ImageUpdateForm(forms.ModelForm):
    """
    Wizard Step 3

    Taslak görsel güncelleme formu.
    """

    class Meta:

        model = ProductDraftImage

        fields = [
            "alt_text",
            "is_main",
        ]

        widgets = {

            "alt_text": forms.TextInput(
                attrs={
                    "placeholder": "Görsel açıklaması",
                }
            ),

            "is_main": forms.CheckboxInput(),

        }

        labels = {

            "alt_text": "Alt Metin",

            "is_main": "Kapak Görseli",

        }