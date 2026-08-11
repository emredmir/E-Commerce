from django.core.management.base import BaseCommand
from django.db import transaction

from products.models import (
    Brand,
    Category,
    CategoryBrand,
    Attribute,
    AttributeValue,
    CategoryAttribute,
    Product,
    ProductVariant,
    ProductImage,
)


class Command(BaseCommand):
    help = "Katalog verilerini temizler."

    @transaction.atomic
    def handle(self, *args, **options):
        confirmation = input(
            "\nBu işlem TÜM katalog verilerini silecektir.\n"
            "Devam etmek istiyorsanız YES yazın: "
        )

        if confirmation != "YES":
            self.stdout.write(
                self.style.WARNING("İşlem iptal edildi.")
            )
            return
        
        self.stdout.write("Katalog temizleniyor...\n")

        image_count = ProductImage.objects.count()
        variant_count = ProductVariant.objects.count()
        product_count = Product.objects.count()
        category_attribute_count = CategoryAttribute.objects.count()
        attribute_value_count = AttributeValue.objects.count()
        attribute_count = Attribute.objects.count()
        brand_count = Brand.objects.count()
        category_brand_count = CategoryBrand.objects.count()
        category_count = Category.objects.count()

        # FK sırasına dikkat
        ProductImage.objects.all().delete()
        ProductVariant.objects.all().delete()
        Product.objects.all().delete()

        CategoryAttribute.objects.all().delete()
        AttributeValue.objects.all().delete()
        Attribute.objects.all().delete()

        CategoryBrand.objects.all().delete()

        Brand.objects.all().delete()
        Category.objects.all().delete()

        self.stdout.write(
            self.style.SUCCESS(
                "\n✔ Katalog başarıyla temizlendi.\n"
                f"• {image_count} ürün görseli silindi.\n"
                f"• {variant_count} ürün varyantı silindi.\n"
                f"• {product_count} ürün silindi.\n"
                f"• {category_attribute_count} kategori-özellik ilişkisi silindi.\n"
                f"• {attribute_value_count} özellik değeri silindi.\n"
                f"• {attribute_count} özellik silindi.\n"
                f"• {category_brand_count} kategori-marka ilişkisi silindi.\n"
                f"• {brand_count} marka silindi.\n"
                f"• {category_count} kategori silindi."
            )
        )