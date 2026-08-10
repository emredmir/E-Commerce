from django.core.management.base import BaseCommand
from django.db import transaction

from products.models import (
    Category,
    Brand,
    Attribute,
    AttributeValue,
    CategoryAttribute,
    CategoryBrand
)


# =========================================================
# KATEGORİLER
# =========================================================

CATEGORIES = {
    "Elektronik": [
        "Telefon",
        "Tablet",
        "Laptop",
        "Masaüstü Bilgisayar",
        "Monitör",
        "Televizyon",
        "Akıllı Saat",
        "Kulaklık",
        "Hoparlör",
        "Powerbank",
        "Şarj Cihazı",
        "Kablo",
    ],
    "Bilgisayar Bileşenleri": [
        "İşlemci",
        "Anakart",
        "RAM",
        "SSD",
        "HDD",
        "Ekran Kartı",
        "Güç Kaynağı",
        "Kasa",
        "Soğutucu",
    ],
    "Ev & Yaşam": [
        "Mutfak",
        "Dekorasyon",
        "Aydınlatma",
        "Temizlik",
        "Banyo",
    ],
    "Moda": [
        "Erkek Giyim",
        "Kadın Giyim",
        "Tişört",
        "Pantolon",
        "Ayakkabı",
        "Çanta",
        "Saat",
    ],
    "Spor": [
        "Fitness",
        "Koşu",
        "Bisiklet",
        "Kamp",
        "Yüzme",
    ],
    "Hobi": [
        "Kitap",
        "Lego",
        "Puzzle",
        "Masa Oyunları",
        "Boyama",
    ],
    "Kozmetik": [
        "Cilt Bakımı",
        "Saç Bakımı",
        "Parfüm",
        "Makyaj",
    ],
    "Bebek": [
        "Oyuncak",
        "Bebek Giyim",
        "Beslenme",
        "Bebek Arabası",
    ],
    "Pet Shop": [
        "Kedi",
        "Köpek",
        "Kuş",
        "Balık",
    ],
    "Otomotiv": [
        "Motor Yağı",
        "Lastik",
        "Aksesuar",
        "Temizlik",
    ],
}



# ---------------------------------------------------------
# Attributes
# ---------------------------------------------------------

ATTRIBUTES = {
    "Renk": [
        "Siyah",
        "Beyaz",
        "Mavi",
        "Kırmızı",
        "Yeşil",
        "Gri",
        "Pembe",
        "Mor",
        "Sarı",
    ],
    "Depolama": [
        "64 GB",
        "128 GB",
        "256 GB",
        "512 GB",
        "1 TB",
    ],
    "RAM": [
        "4 GB",
        "6 GB",
        "8 GB",
        "12 GB",
        "16 GB",
        "32 GB",
    ],
    "Ekran Boyutu": [
        '6.1"',
        '6.7"',
        '13"',
        '15.6"',
        '27"',
        '32"',
        '55"',
        '65"',
    ],
    "İşlemci": [
        "Intel i5",
        "Intel i7",
        "Intel Ultra 7",
        "Ryzen 5",
        "Ryzen 7",
        "Apple M2",
        "Apple M3",
    ],
    "Beden": [
        "XS",
        "S",
        "M",
        "L",
        "XL",
        "XXL",
    ],
    "Numara": [
        "36",
        "37",
        "38",
        "39",
        "40",
        "41",
        "42",
        "43",
        "44",
        "45",
    ],
    "Cilt Tipi": [
        "Kuru",
        "Yağlı",
        "Karma",
        "Normal",
    ],
    "Yaş Grubu": [
        "0-3",
        "3-6",
        "6-9",
        "9-12",
        "12+",
    ],
    "Kapak Türü": [
        "Karton Kapak",
        "Ciltli Kapak",

    ],
}

# ---------------------------------------------------------
# Category -> Attribute Eşleştirmeleri - Özellik , Variant(ayrı sku) , Required , is_visual
# ---------------------------------------------------------

CATEGORY_ATTRIBUTES = {
    # Elektronik
    "Telefon": [
        ("Renk", True, True, True),
        ("Depolama", True, True, False),
        ("RAM", True, False, False),
    ],

    "Tablet": [
        ("Renk", True, True, True),
        ("Depolama", True, True, False),
        ("RAM", True, False, False),
    ],

    "Laptop": [
        ("RAM", True, True, True),
        ("İşlemci", True, True, True),
        ("Ekran Boyutu", False, False, True),
        ("Depolama", True, False, True),
    ],

    "Monitör": [
        ("Ekran Boyutu", True, True, True),
    ],

    "Televizyon": [
        ("Ekran Boyutu", True, True, True),
        ("Renk", False, False, True),
    ],

    # Giyim
    "Tişört": [
        ("Beden", True, True, False),
        ("Renk", True, False, True),
    ],

    "Pantolon": [
        ("Beden", True, True, False),
        ("Renk", True, False, True),
    ],

    "Ayakkabı": [
        ("Numara", True, True, False),
        ("Renk", True, False, True),
    ],

    # Kitap
    "Kitap": [
        ("Kapak Türü", True, True, True),
    ],

    # Lego
    "Lego": [
        ("Yaş Grubu", False, False, False),
    ],

    # Kozmetik
    "Cilt Bakımı": [
        ("Cilt Tipi", False, False, True),
    ],
}

# ---------------------------------------------------------
# Brands
# ---------------------------------------------------------
CATEGORY_BRANDS = {

    # =====================================================
    # Elektronik
    # =====================================================

    ("Elektronik", "Telefon"): [
        "Apple", "Samsung", "Xiaomi", "Huawei", "Honor",
        "Google", "Nothing", "OnePlus", "Oppo", "Vivo", "Realme",
    ],

    ("Elektronik", "Tablet"): [
        "Apple", "Samsung", "Huawei", "Lenovo", "Xiaomi",
    ],

    ("Elektronik", "Laptop"): [
        "Apple", "ASUS", "Acer", "Dell", "HP",
        "Lenovo", "MSI", "Monster", "Huawei",
    ],

    ("Elektronik", "Masaüstü Bilgisayar"): [
        "Dell", "HP", "Lenovo", "MSI", "ASUS",
    ],

    ("Elektronik", "Monitör"): [
        "Samsung", "LG", "ASUS", "Acer",
        "Dell", "Philips", "BenQ", "ViewSonic",
    ],

    ("Elektronik", "Televizyon"): [
        "Samsung", "LG", "Sony", "Philips",
        "TCL", "Vestel", "Arçelik", "Beko",
    ],

    ("Elektronik", "Akıllı Saat"): [
        "Apple", "Samsung", "Huawei",
        "Xiaomi", "Honor", "Garmin",
    ],

    ("Elektronik", "Kulaklık"): [
        "Apple", "Samsung", "Sony", "JBL",
        "Huawei", "Anker", "Beats", "Sennheiser",
    ],

    ("Elektronik", "Hoparlör"): [
        "JBL", "Sony", "Marshall",
        "Anker", "LG", "Samsung",
    ],

    ("Elektronik", "Powerbank"): [
        "Anker", "Baseus", "Xiaomi", "Samsung",
    ],

    ("Elektronik", "Şarj Cihazı"): [
        "Apple", "Samsung", "Anker",
        "Baseus", "UGREEN",
    ],

    ("Elektronik", "Kablo"): [
        "UGREEN", "Anker", "Baseus",
        "Apple", "Samsung",
    ],

    # =====================================================
    # Bilgisayar Bileşenleri
    # =====================================================

    ("Bilgisayar Bileşenleri", "İşlemci"): [
        "Intel", "AMD",
    ],

    ("Bilgisayar Bileşenleri", "Anakart"): [
        "ASUS", "MSI", "Gigabyte", "ASRock",
    ],

    ("Bilgisayar Bileşenleri", "RAM"): [
        "Corsair", "Kingston", "G.Skill",
        "Crucial", "ADATA",
    ],

    ("Bilgisayar Bileşenleri", "SSD"): [
        "Samsung", "Kingston", "Crucial",
        "WD", "ADATA", "Seagate",
    ],

    ("Bilgisayar Bileşenleri", "HDD"): [
        "Seagate", "WD", "Toshiba",
    ],

    ("Bilgisayar Bileşenleri", "Ekran Kartı"): [
        "ASUS", "MSI", "Gigabyte",
        "Zotac", "Sapphire",
    ],

    ("Bilgisayar Bileşenleri", "Güç Kaynağı"): [
        "Corsair", "Cooler Master", "FSP", "MSI",
    ],

    ("Bilgisayar Bileşenleri", "Kasa"): [
        "NZXT", "Corsair", "Cooler Master", "MSI",
    ],

    ("Bilgisayar Bileşenleri", "Soğutucu"): [
        "Cooler Master", "Noctua", "DeepCool", "Arctic",
    ],

    # =====================================================
    # Moda
    # =====================================================

    ("Moda", "Erkek Giyim"): [
        "Nike", "Adidas", "Puma",
        "Mavi", "LC Waikiki", "Koton",
    ],

    ("Moda", "Kadın Giyim"): [
        "Nike", "Adidas", "Koton",
        "Mango", "Zara",
    ],

    ("Moda", "Tişört"): [
        "Nike", "Adidas", "Puma",
        "Under Armour", "New Balance", "Mavi",
    ],

    ("Moda", "Pantolon"): [
        "Nike", "Adidas", "Levi's",
        "Mavi", "Koton",
    ],

    ("Moda", "Ayakkabı"): [
        "Nike", "Adidas", "Puma",
        "New Balance", "Skechers",
    ],

    ("Moda", "Çanta"): [
        "Nike", "Adidas", "Eastpak", "Samsonite",
    ],

    ("Moda", "Saat"): [
        "Casio", "Seiko", "Citizen",
        "Apple", "Samsung",
    ],

    # =====================================================
    # Spor
    # =====================================================

    ("Spor", "Fitness"): [
        "Decathlon", "Nike", "Adidas",
    ],

    ("Spor", "Koşu"): [
        "Nike", "Adidas", "Asics", "New Balance",
    ],

    ("Spor", "Bisiklet"): [
        "Bianchi", "Scott", "Carraro", "Kron",
    ],

    ("Spor", "Kamp"): [
        "Quechua", "The North Face", "Columbia",
    ],

    ("Spor", "Yüzme"): [
        "Speedo", "Arena", "Decathlon",
    ],

    # =====================================================
    # Hobi
    # =====================================================

    ("Hobi", "Kitap"): [
        "İş Bankası Kültür Yayınları",
        "Can Yayınları",
        "Yapı Kredi Yayınları",
        "Pegasus Yayınları",
        "Doğan Kitap",
        "İthaki Yayınları",
        "Epsilon Yayınları",
    ],

    ("Hobi", "Lego"): [
        "LEGO",
    ],

    ("Hobi", "Puzzle"): [
        "Ravensburger", "Anatolian", "Educa",
    ],

    ("Hobi", "Masa Oyunları"): [
        "Hasbro", "Mattel",
    ],

    ("Hobi", "Boyama"): [
        "Faber-Castell", "Staedtler",
    ],

    # =====================================================
    # Kozmetik
    # =====================================================

    ("Kozmetik", "Cilt Bakımı"): [
        "La Roche-Posay", "CeraVe",
        "Bioderma", "Vichy", "Nivea",
    ],

    ("Kozmetik", "Saç Bakımı"): [
        "Pantene", "Elseve", "Head & Shoulders",
    ],

    ("Kozmetik", "Parfüm"): [
        "Versace", "Calvin Klein", "Hugo Boss",
    ],

    ("Kozmetik", "Makyaj"): [
        "Maybelline", "L'Oréal Paris", "NYX",
    ],

    # =====================================================
    # Bebek
    # =====================================================

    ("Bebek", "Oyuncak"): [
        "LEGO", "Fisher-Price", "Hot Wheels",
    ],

    ("Bebek", "Bebek Giyim"): [
        "LC Waikiki", "Civil",
    ],

    ("Bebek", "Beslenme"): [
        "Philips Avent", "Chicco",
    ],

    ("Bebek", "Bebek Arabası"): [
        "Joie", "Chicco", "Kraft",
    ],

    # =====================================================
    # Pet Shop
    # =====================================================

    ("Pet Shop", "Kedi"): [
        "Royal Canin", "Pro Plan", "Reflex",
    ],

    ("Pet Shop", "Köpek"): [
        "Royal Canin", "Pro Plan", "Reflex",
    ],

    ("Pet Shop", "Kuş"): [
        "Vitakraft",
    ],

    ("Pet Shop", "Balık"): [
        "Sera", "Tetra",
    ],

    # =====================================================
    # Otomotiv
    # =====================================================

    ("Otomotiv", "Motor Yağı"): [
        "Castrol", "Mobil", "Shell",
    ],

    ("Otomotiv", "Lastik"): [
        "Michelin", "Goodyear",
        "Pirelli", "Bridgestone",
    ],

    ("Otomotiv", "Aksesuar"): [
        "Baseus", "Xiaomi",
    ],

    ("Otomotiv", "Temizlik"): [
        "Sonax", "Meguiar's",
    ],
}


class Command(BaseCommand):
    help = "Varsayılan kategori ve marka verilerini oluşturur."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seed işlemi başlatılıyor...\n")

        category_count = self._create_categories()
        attribute_count, value_count = self._create_attributes()
        category_attribute_count = self._create_category_attributes()
        brand_count, category_brand_count   = self._create_brands()



        self.stdout.write(
            self.style.SUCCESS(
                "\n✔ Seed işlemi başarıyla tamamlandı.\n"
                f"• {category_count} yeni kategori oluşturuldu.\n"
                f"• {attribute_count} yeni özellik oluşturuldu.\n"
                f"• {value_count} yeni özellik değeri oluşturuldu.\n"
                f"• {category_attribute_count} kategori-özellik ilişkisi oluşturuldu.\n"
                f"• {brand_count} yeni marka oluşturuldu.\n"
                f"• {category_brand_count } kategori-marka ilişkisi oluşturuldu."
            )
        )



    def _create_categories(self):
        """
        Üst kategorileri ve alt kategorileri oluşturur.

        get_or_create() kullanıldığı için komut tekrar çalıştırılabilir.
        """

        created_count = 0

        for parent_name, children in CATEGORIES.items():

            parent, created = Category.objects.get_or_create(
                name=parent_name,
                defaults={
                    "is_active": True,
                },
            )

            if created:
                created_count += 1

            for child_name in children:

                _, created = Category.objects.get_or_create(
                    name=child_name,
                    parent=parent,
                    defaults={
                        "is_active": True,
                    },
                )

                if created:
                    created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f" Kategoriler kontrol edildi ({created_count} yeni kayıt)."
            )
        )

        return created_count

    def _create_brands(self):
        """
        Markaları ve kategori-marka ilişkilerini oluşturur.

        CATEGORY_BRANDS sözlüğü kullanılır.

        get_or_create() sayesinde komut tekrar çalıştırılabilir.
        """

        brand_count = 0
        relation_count = 0

        for (parent_name, category_name), brands in CATEGORY_BRANDS.items():
            try:
                parent = Category.objects.get(name=parent_name, parent__isnull=True)
                category = Category.objects.get(name=category_name, parent=parent)
            except Category.DoesNotExist:
                continue

            for brand_name in brands:
                brand, created = Brand.objects.get_or_create(
                    name=brand_name,
                    defaults={
                        "is_active": True,
                    },
                )

                if created:
                    brand_count += 1

                _, created = CategoryBrand.objects.get_or_create(
                    category=category,
                    brand=brand,
                )

                if created:
                    relation_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f" Markalar kontrol edildi "
                f"({brand_count} yeni marka, "
                f"{relation_count} yeni kategori-marka ilişkisi)."
            )
        )

        return brand_count, relation_count
    
    def _create_attributes(self):
        """
        Özellikleri (Attribute) ve değerlerini (AttributeValue) oluşturur.

        get_or_create() sayesinde komut tekrar çalıştırılabilir.
        """

        attribute_count = 0
        value_count = 0

        for attribute_name, values in ATTRIBUTES.items():

            attribute, created = Attribute.objects.get_or_create(
                name=attribute_name,
            )

            if created:
                attribute_count += 1

            for value in values:

                _, created = AttributeValue.objects.get_or_create(
                    attribute=attribute,
                    value=value,
                )

                if created:
                    value_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f" Özellikler kontrol edildi "
                f"({attribute_count} yeni özellik, {value_count} yeni değer)."
            )
        )

        return attribute_count, value_count

    def _create_category_attributes(self):
        """
        Kategori-özellik ilişkilerini oluşturur.

        get_or_create() sayesinde komut tekrar çalıştırılabilir.
        """

        created_count = 0

        for category_name, attrs in CATEGORY_ATTRIBUTES.items():

            try:
                category = Category.objects.get(name=category_name)
            except Category.DoesNotExist:
                continue

            for order, (attribute_name, is_variant, is_required, is_visual) in enumerate(attrs):

                try:
                    attribute = Attribute.objects.get(name=attribute_name)
                except Attribute.DoesNotExist:
                    continue

                _, created = CategoryAttribute.objects.get_or_create(
                    category=category,
                    attribute=attribute,
                    defaults={
                        "sort_order": order,
                        "is_variant": is_variant,
                        "is_required": is_required,
                        "is_filterable": True,
                        "is_visual": is_visual,
                    },
                )

                if created:
                    created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f" Kategori özellikleri kontrol edildi ({created_count} yeni kayıt)."
            )
        )

        return created_count