# Veritabanı Tasarımı

> Bu belge, projenin mevcut Django model tanımlarından çıkarılmış **mantıksal veritabanı dokümantasyonudur**. Açıklamalar Türkçe, kaynak koddaki model/alan/constraint/index isimleri aynen bırakılmıştır.

## 1. Kapsam ve Kaynak

Bu dokümanda veri modeli için temel kaynak `accounts/models.py`, `store/models.py`, `products/models.py`, `cart/models.py` ve `orders/models.py` dosyalarıdır. Projede **44 kalıcı Django modeli** ve bu modeller tarafından kullanılan durum/enum tanımları bulunmaktadır.

Bu aşamada fiziksel MSSQL veritabanına bağlanılarak canlı şema okunmamıştır. Dolayısıyla diyagramlar **mevcut model kodunun mantıksal şemasını** gösterir. Migration geçmişi fiziksel şemanın evrimini belgelemek için ayrıca repository içinde tutulmaktadır.

## 2. Veritabanı Teknolojisi

```text
Django Models / ORM
        │
        ▼
   mssql-django
        │
        ▼
     pyodbc
        │
        ▼
Microsoft SQL Server
```

`settings.py` içerisinde MSSQL backend kullanılmakta ve bağlantı ODBC üzerinden kurulmaktadır. Model tarafında explicit `db_table` tanımları bulunmadığı için Django'nun varsayılan tablo isimlendirme yaklaşımı esas alınır.

## 3. Domain Dağılımı

| Uygulama | Sorumluluk | Model sayısı |
|---|---|---:|
| `accounts` | Kimlik, kullanıcı ve satıcı profili | 4 |
| `store` | Mağaza ve mağaza talepleri | 2 |
| `products` | Katalog, varyant, teklif, taslak ve ürün etkileşimleri | 22 |
| `cart` | Sepet | 2 |
| `orders` | Sipariş, stok, fatura, ödeme, kayıtlı kart, iade ve iptal | 14 |

### Temel domain ilişkisi

```text
CustomUser
   │
   ├── Address
   ├── SellerProfile
   │      └── Store
   │
   └── ProductCollection / ProductQuestion / Cart / Order / Payment

Global Catalog
   │
   ├── Product
   │     └── ProductVariant
   │            └── StoreProduct   ← mağazanın gerçek teklifi / stoğu
   │
   └── Product Draft / Image / Q&A

Cart
   │
   └── CartItem → StoreProduct
                │
                ▼
             Checkout
                │
                ▼
               Order
                │
           ┌────┴────┐
           ▼         ▼
       SubOrder   PaymentTransaction
           │
           ├── OrderItem
           ├── StockReservation
           └── Invoice

PaymentTransaction
   └── PaymentTransactionItem
            │
            └── PaymentRefund → PaymentRefundItem
```

## 4. Temel Veritabanı Tasarım Kararları

### 4.1 Global ürün ile satıcı teklifinin ayrılması

`Product` global katalog nesnesidir. Satıcının fiyatı, stoğu, SKU'su ve teklif durumu `StoreProduct` içerisinde tutulur. Böylece aynı ürün birden fazla mağaza tarafından farklı fiyat ve stok bilgileriyle satılabilir.

```text
Product
   │
   └── ProductVariant
          │
          ├── StoreProduct (Store A)
          ├── StoreProduct (Store B)
          └── StoreProduct (Store C)
```

### 4.2 Multi-vendor sipariş ayrımı

Bir müşterinin tek checkout işleminde birden fazla mağazadan ürün satın alması için `Order` ana kayıt, `SubOrder` ise mağaza bazlı alt kayıt olarak kullanılır. Aynı `Order` altında aynı `Store` için yalnızca tek `SubOrder` bulunmasını `unique_store_per_order` constraint'i garanti eder.

### 4.3 Snapshot yaklaşımı

Geçmiş sipariş ve fatura kayıtlarının canlı katalog/satıcı bilgilerine bağımlı kalmaması için kritik alanlar snapshot olarak saklanır. Özellikle `Order`, `SubOrder`, `OrderItem`, `Invoice` ve `InvoiceItem` üzerinde bu yaklaşım belirgindir.

Örneğin `StoreProduct.price` daha sonra değişse bile `OrderItem.unit_price` sipariş anındaki fiyatı korur. Benzer şekilde `store_name_snapshot`, `product_name_snapshot`, `variant_snapshot`, `sku_snapshot` ve fatura üzerindeki seller/buyer alanları tarihsel kaydı korur.

### 4.4 Delete davranışı

İlişkilerde `CASCADE`, `PROTECT` ve `SET_NULL` bilinçli olarak farklı amaçlarla kullanılmıştır:

- `CASCADE`: alt kaydın üst kayıtla birlikte yaşam döngüsünün bitmesi istendiğinde kullanılır.
- `PROTECT`: tarihsel veya finansal kayıtların yanlışlıkla silinmesini engellemek için kullanılır.
- `SET_NULL`: referans artık mevcut olmasa bile tarihsel kaydın korunmasının istendiği ilişkilerde kullanılır.

Özellikle sipariş, ödeme, refund ve invoice katmanında `PROTECT` kullanımı tarihsel bütünlüğü koruma açısından önemlidir.

### 4.5 Database-level integrity

İş kurallarının yalnızca application/service layer'da bırakılmaması için model seviyesinde `UniqueConstraint`, conditional unique constraint ve `CheckConstraint` kullanılmıştır. Böylece eşzamanlı isteklerde database seviyesinde de koruma sağlanır.

### 4.6 Index stratejisi

Listeleme, filtreleme, kullanıcı bazlı erişim, status sorguları ve ödeme/refund işlemlerinde kullanılan alanlar için composite index'ler tanımlanmıştır. Örnekler arasında `StoreProduct(variant, status, price)`, `Order(user, created_at)`, `PaymentTransaction(order, created_at)` ve `ProductQuestion(product, target_store, is_visible)` bulunur.

## 5. Durum ve Enum Tanımları

### `SellerType` (accounts)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PERSONAL` | `PERSONAL` | Bireysel |
| `PRIVATE_COMPANY` | `PRIVATE_COMPANY` | Şahıs Şirketi |
| `LIMITED_OR_JOINT_STOCK_COMPANY` | `LIMITED_OR_JOINT_STOCK_COMPANY` | Limited / Anonim Şirket |

### `IyzicoOnboardingStatus` (accounts)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `NOT_STARTED` | `not_started` | Başlatılmadı |
| `PENDING` | `pending` | Bekliyor |
| `ACTIVE` | `active` | Aktif |
| `FAILED` | `failed` | Başarısız |
| `RECONCILIATION_REQUIRED` | `reconciliation_required` | Mutabakat Gerekli |
| `SUSPENDED` | `suspended` | Askıya Alındı |

### `StoreStatus` (store)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PENDING` | `pending` | Onay Bekliyor |
| `APPROVED` | `approved` | Onaylandı |
| `REJECTED` | `rejected` | Reddedildi |
| `SUSPENDED` | `suspended` | Askıya Alındı |
| `ARCHIVED` | `archived` | Arşivlendi |

### `ProductStatus` (products)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `DRAFT` | `('draft', _('Taslak'))` |  |
| `ACTIVE` | `('active', _('Yayında'))` |  |
| `ARCHIVED` | `('archived', _('Arşivlendi'))` |  |

### `StoreProductStatus` (products)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `ACTIVE` | `active` | Yayında |
| `DRAFT` | `draft` | Taslak |
| `OUT_OF_STOCK` | `out_of_stock` | Tükendi |
| `ARCHIVED` | `archived` | Arşivlendi |

### `QuestionTopic` (products)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `GENERAL` | `general` | Genel |
| `PRICE` | `price` | Fiyat / İndirim |
| `PAYMENT` | `payment` | Ödeme |
| `AVAILABILITY` | `availability` | Stok / Bulunabilirlik |
| `SHIPPING` | `shipping` | Kargo / Teslimat |
| `WARRANTY` | `warranty` | Garanti / İade |
| `GIFT` | `gift` | Hediye Gönderimi |
| `MODEL_VARIANT` | `model_variant` | Model / Versiyon Farklılıkları |
| `FEATURES` | `features` | Fonksiyon / Özellikler |
| `CONTENT` | `content` | Ürün İçeriği |
| `MATERIAL` | `material` | Malzeme |
| `COLOR` | `color` | Renk Seçenekleri |
| `SIZE` | `size` | Boyut / Ölçü |
| `WEIGHT` | `weight` | Ağırlık |
| `QUANTITY` | `quantity` | Adet / Miktar |
| `ORIGIN` | `origin` | Menşei / Üretim Yeri |
| `USAGE_AREA` | `usage_area` | Kullanım Alanları |
| `USAGE_INSTRUCTIONS` | `usage_instructions` | Kullanım Talimatları |
| `INSTALLATION` | `installation` | Kurulum / Montaj |
| `CARE` | `care` | Bakım / Temizlik |
| `STORAGE` | `storage` | Saklama Koşulları |
| `COMPATIBILITY` | `compatibility` | Uyumluluk |
| `SUITABILITY` | `suitability` | Uygunluk |
| `AGE` | `age` | Yaş Uygunluğu |
| `SAFETY` | `safety` | Güvenlik |
| `ALLERGY` | `allergy` | Alerji / Hassasiyet |
| `PERFORMANCE` | `performance` | Performans |
| `EXPIRY` | `expiry` | Son Kullanma Tarihi |
| `CERTIFICATION` | `certification` | Sertifika / Standartlar |

### `OrderStatus` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PENDING_PAYMENT` | `pending_payment` | Ödeme Bekliyor |
| `PAID` | `paid` | Ödendi |
| `PREPARING` | `preparing` | Hazırlanıyor |
| `PARTIALLY_SHIPPED` | `partially_shipped` | Kısmen Kargolandı |
| `SHIPPED` | `shipped` | Kargolandı |
| `DELIVERED` | `delivered` | Teslim Edildi |
| `CANCELLED` | `cancelled` | İptal Edildi |
| `EXPIRED` | `expired` | Süresi Doldu |
| `COMPLETED` | `completed` | Tamamlandı |

### `SubOrderStatus` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PENDING` | `pending` | Bekliyor |
| `PREPARING` | `preparing` | Hazırlanıyor |
| `SHIPPED` | `shipped` | Kargolandı |
| `DELIVERED` | `delivered` | Teslim Edildi |
| `CANCELLED` | `cancelled` | İptal Edildi |

### `PaymentStatus` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `INITIATED` | `initiated` | Başlatıldı |
| `PENDING` | `pending` | Bekliyor |
| `SUCCESS` | `success` | Başarılı |
| `FAILED` | `failed` | Başarısız |
| `REFUNDED` | `refunded` | İade Edildi |
| `PARTIALLY_REFUNDED` | `partially_refunded` | Kısmen İade Edildi |

### `RefundStatus` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PENDING` | `pending` | Bekliyor |
| `PROCESSING` | `processing` | İşleniyor |
| `SUCCESS` | `success` | Başarılı |
| `FAILED` | `failed` | Başarısız |
| `RECONCILIATION_REQUIRED` | `reconciliation_required` | Mutabakat Gerekiyor |

### `ReservationStatus` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `ACTIVE` | `active` | Aktif (Kilitli) |
| `CONSUMED` | `consumed` | Tüketildi (Satın Alındı) |
| `RELEASED` | `released` | Serbest Bırakıldı (İptal) |
| `EXPIRED` | `expired` | Süresi Doldu |

### `CargoCompany` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `YURTICI` | `yurtici` | Yurtiçi Kargo |
| `ARAS` | `aras` | Aras Kargo |
| `SURAT` | `surat` | Sürat Kargo |
| `PTT` | `ptt` | PTT Kargo |
| `UPS` | `ups` | UPS |
| `DHL` | `dhl` | DHL |
| `KOLAYGELSIN` | `kolaygelsin` | Kolay Gelsin |

### `PaymentTransactionItemType` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PRODUCT` | `product` | Ürün |
| `SHIPPING` | `shipping` | Kargo |

### `StoredCardOperationType` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `CREATE` | `CREATE` | Create |
| `DELETE` | `DELETE` | Delete |

### `StoredCardOperationStatus` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `PENDING` | `PENDING` | Pending |
| `PROVIDER_SUCCEEDED` | `PROVIDER_SUCCEEDED` | Provider Succeeded |
| `SUCCESS` | `SUCCESS` | Success |
| `FAILED` | `FAILED` | Failed |
| `RECONCILIATION_REQUIRED` | `RECONCILIATION_REQUIRED` | Reconciliation Required |

### `RefundType` (orders)

| Sabit | DB değeri | Görünen anlam |
|---|---|---|
| `SUBORDER_CANCELLATION` | `suborder_cancellation` | Alt Sipariş İptali |
| `CUSTOMER_REQUEST` | `customer_request` | Müşteri Talebi |
| `SELLER_FAULT` | `seller_fault` | Satıcı Kaynaklı |

## 6.1. Kimlik ve Satıcı Alanı

### `accounts`

#### `CustomUser`

**Amaç:** Django'nun AbstractUser sınıfından türetilen özel kullanıcı modelidir. Varsayılan username alanı kaldırılmış; email ve phone_number üzerinden kullanıcı kimliği desteklenmiştir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `email` | `string` | UNIQUE=True; NULL=True; BLANK=True | Email bilgisini tutar. |
| `phone_number` | `string` | max_length=15; UNIQUE=True; NULL=True; BLANK=True | Phone number bilgisini tutar. |
| `is_seller` | `boolean` | default=False | Durum/özellik bayrağını tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

#### `Address`

**Amaç:** Kullanıcının teslimat ve benzeri adres kayıtlarını tutar. Bir kullanıcı birden fazla adres kaydedebilir ve aynı anda tek bir varsayılan adres mantığı uygulanır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='addresses' | CustomUser ile olan ilişkiyi tutar. |
| `title` | `string` | max_length=100 | Title bilgisini tutar. |
| `full_name` | `string` | max_length=255 | Full name bilgisini tutar. |
| `phone_number` | `string` | max_length=15 | Phone number bilgisini tutar. |
| `address_line1` | `string` | max_length=255 | Address line1 bilgisini tutar. |
| `address_line2` | `string` | max_length=255; BLANK=True | Address line2 bilgisini tutar. |
| `city` | `string` | max_length=100 | City bilgisini tutar. |
| `state` | `string` | max_length=100 | State bilgisini tutar. |
| `postal_code` | `string` | max_length=20 | Postal code bilgisini tutar. |
| `is_default` | `boolean` | default=False | Durum/özellik bayrağını tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-is_default', '-id']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['user', 'title'], name='unique_user_title')]
  ```

#### `SellerProfile`

**Amaç:** Kullanıcının satıcı kimliği, ticari/yasal bilgileri ve iyzico sub-merchant/onboarding durumunu tutar.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='seller_profile' | CustomUser ile olan ilişkiyi tutar. |
| `seller_type` | `string` | max_length=40; default=SellerType.PERSONAL | Seller type bilgisini tutar. |
| `company_name` | `string` | max_length=255; BLANK=True | Company name bilgisini tutar. |
| `legal_company_title` | `string` | max_length=255; BLANK=True | Legal company title bilgisini tutar. |
| `company_address` | `text` |  | Company address bilgisini tutar. |
| `company_phone` | `string` | max_length=15; NULL=True; BLANK=True | Company phone bilgisini tutar. |
| `tax_office` | `string` | max_length=150; BLANK=True | Tax office bilgisini tutar. |
| `tax_number` | `string` | max_length=50; BLANK=True | Tax number bilgisini tutar. |
| `identity_number` | `string` | max_length=20; BLANK=True | Identity number bilgisini tutar. |
| `iban` | `string` | max_length=34 | IBAN bilgisini tutar. |
| `is_approved` | `boolean` | default=False | Durum/özellik bayrağını tutar. |
| `iyzico_submerchant_external_id` | `string` | max_length=100; UNIQUE=True; editable=False | Iyzico submerchant external id bilgisini tutar. |
| `iyzico_submerchant_key` | `string` | max_length=255; NULL=True; BLANK=True | Iyzico submerchant key bilgisini tutar. |
| `iyzico_onboarding_started_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |
| `iyzico_onboarding_status` | `string` | max_length=30; default=IyzicoOnboardingStatus.NOT_STARTED | Iyzico onboarding status bilgisini tutar. |
| `iyzico_onboarded_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |
| `iyzico_last_sync_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |
| `iyzico_last_error_code` | `string` | max_length=50; BLANK=True | Iyzico last error code bilgisini tutar. |
| `iyzico_last_error_message` | `text` | BLANK=True | Iyzico last error message bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

#### `SellerProfileUpdateRequest`

**Amaç:** Satıcı profilindeki belirli bilgilerin doğrudan değiştirilmesi yerine onay sürecine alınan değişiklik talebini tutar.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `seller_profile` | `bigint` | FK → SellerProfile; on_delete=CASCADE; related_name='change_requests' | SellerProfile ile olan ilişkiyi tutar. |
| `new_company_name` | `string` | max_length=255; BLANK=True | Yeni Şirket Adı bilgisini tutar. |
| `new_company_address` | `text` | BLANK=True | Yeni Şirket Adresi bilgisini tutar. |
| `new_company_phone` | `string` | max_length=15; NULL=True; BLANK=True | Yeni Telefon bilgisini tutar. |
| `new_iban` | `string` | max_length=34; BLANK=True | Yeni IBAN bilgisini tutar. |
| `status` | `string` | max_length=10; default='pending' | Status bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

**`STATUS_CHOICES`:** `pending` (Onay Bekliyor), `approved` (Onaylandı), `rejected` (Reddedildi).

## 6.2. Mağaza Alanı

### `store`

#### `Store`

**Amaç:** Bir SellerProfile altında faaliyet gösteren mağazayı temsil eder. Mağazanın yayın/askı/arşiv gibi yaşam döngüsü durumlarını da taşır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `seller` | `bigint` | FK → SellerProfile; on_delete=CASCADE; related_name='stores' | SellerProfile ile olan ilişkiyi tutar. |
| `store_name` | `string` | max_length=255 | Mağaza Adı bilgisini tutar. |
| `slug` | `string` | UNIQUE=True; BLANK=True | Slug bilgisini tutar. |
| `logo` | `image` | NULL=True; BLANK=True | Logo bilgisini tutar. |
| `banner` | `image` | NULL=True; BLANK=True | Banner bilgisini tutar. |
| `contact_email` | `string` |  | Mağaza E-posta bilgisini tutar. |
| `contact_phone` | `string` | max_length=15; BLANK=True | Mağaza Telefon bilgisini tutar. |
| `address` | `string` | max_length=255; BLANK=True | Adres Satırı bilgisini tutar. |
| `status` | `string` | max_length=10; default=StoreStatus.PENDING | Status bilgisini tutar. |
| `is_active` | `boolean` | default=False | Durum/özellik bayrağını tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `approved_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

#### `StoreUpdateRequest`

**Amaç:** Mağaza bilgilerinde yapılmak istenen değişiklikleri onaya sunan talep kaydıdır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `store` | `bigint` | FK → Store; on_delete=CASCADE; related_name='change_requests' | Store ile olan ilişkiyi tutar. |
| `new_store_name` | `string` | max_length=255; BLANK=True | Yeni Mağaza Adı bilgisini tutar. |
| `new_logo` | `image` | NULL=True; BLANK=True | Talep edilen yeni değeri tutar. |
| `new_banner` | `image` | NULL=True; BLANK=True | Talep edilen yeni değeri tutar. |
| `new_contact_email` | `string` | BLANK=True | Yeni E-posta bilgisini tutar. |
| `new_contact_phone` | `string` | max_length=15; BLANK=True | Yeni Telefon bilgisini tutar. |
| `new_address` | `string` | max_length=255; BLANK=True | Yeni Adres Satırı bilgisini tutar. |
| `status` | `string` | max_length=10; default=StoreStatus.PENDING | Status bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['store'], condition=Q(status=StoreStatus.PENDING), name='unique_pending_store_update_request')]
  ```

## 6.3. Ürün Kataloğu ve Ürün İşlemleri

### `products`

#### `Category`

**Amaç:** Ürün kataloğunun hiyerarşik kategori ağacını temsil eder.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `name` | `string` | max_length=255 | Kategori Adı bilgisini tutar. |
| `slug` | `string` | UNIQUE=True; BLANK=True | Slug bilgisini tutar. |
| `parent` | `bigint` | FK → self; on_delete=SET_NULL; related_name='children'; NULL=True; BLANK=True | Üst Kategori bilgisini tutar. |
| `is_active` | `boolean` | default=True | Aktif mi? bilgisini tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

#### `Brand`

**Amaç:** Katalogdaki markayı temsil eder.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `name` | `string` | max_length=255; UNIQUE=True | Marka Adı bilgisini tutar. |
| `slug` | `string` | UNIQUE=True; BLANK=True | Slug bilgisini tutar. |
| `is_active` | `boolean` | default=True | Aktif mi? bilgisini tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

#### `CategoryBrand`

**Amaç:** Bir markanın hangi kategori altında kullanılabileceğini belirleyen ilişki modelidir.

**Model notu:** Bir markanın hangi kategorilerde kullanılabileceğini belirtir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `category` | `bigint` | FK → Category; on_delete=CASCADE; related_name='category_brands' | Category ile olan ilişkiyi tutar. |
| `brand` | `bigint` | FK → Brand; on_delete=CASCADE; related_name='brand_categories' | Brand ile olan ilişkiyi tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `('category__name', 'brand__name')`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=('category', 'brand'), name='unique_category_brand')]
  ```

#### `BrandRequest`

**Amaç:** Satıcının sisteme yeni bir marka eklenmesini talep ettiği iş akışını temsil eder.

**Model notu:** Satıcının sisteme yeni marka eklenmesini talep etmesi.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `seller` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='brand_requests' | CustomUser ile olan ilişkiyi tutar. |
| `category` | `bigint` | FK → Category; on_delete=CASCADE; related_name='brand_requests' | Category ile olan ilişkiyi tutar. |
| `brand_name` | `string` | max_length=120 | Brand name bilgisini tutar. |
| `note` | `text` | BLANK=True | Note bilgisini tutar. |
| `status` | `string` | max_length=20; INDEX=True; default=Status.PENDING | Status bilgisini tutar. |
| `reviewed_by` | `bigint` | FK → CustomUser; on_delete=SET_NULL; related_name='reviewed_brand_requests'; NULL=True; BLANK=True | CustomUser ile olan ilişkiyi tutar. |
| `reviewed_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `last_activity_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `('-created_at',)`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=('seller', 'category', 'brand_name'), name='unique_brand_request_per_seller')]
  ```

#### `Product`

**Amaç:** Satıcılardan bağımsız global katalog ürününü temsil eder. Satılabilir envanter doğrudan bu modelde değil, ProductVariant + StoreProduct katmanındadır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `name` | `string` | max_length=255 | Ürün Adı bilgisini tutar. |
| `slug` | `string` | max_length=255; UNIQUE=True; BLANK=True | Slug bilgisini tutar. |
| `description` | `text` |  | Genel Ürün Açıklaması bilgisini tutar. |
| `category` | `bigint` | FK → Category; on_delete=SET_NULL; related_name='products'; NULL=True | Category ile olan ilişkiyi tutar. |
| `brand` | `bigint` | FK → Brand; on_delete=SET_NULL; related_name='products'; NULL=True; BLANK=True | Brand ile olan ilişkiyi tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `status` | `string` | max_length=20; INDEX=True; default=ProductStatus.DRAFT | Status bilgisini tutar. |
| `default_variant` | `bigint` | FK → ProductVariant; on_delete=SET_NULL; related_name='default_for_product'; NULL=True; BLANK=True | ProductVariant ile olan ilişkiyi tutar. |
| `created_by_store` | `bigint` | FK → Store; on_delete=SET_NULL; related_name='created_products'; NULL=True; BLANK=True | Oluşturan Mağaza bilgisini tutar. |
| `normalized_name` | `string` | max_length=255; INDEX=True; editable=False | Normalized name bilgisini tutar. |
| `normalized_key` | `string` | max_length=255; INDEX=True; editable=False | Normalized key bilgisini tutar. |
| `tokens` | `json` | editable=False; default=list | Ürün Kelimeleri bilgisini tutar. |

**Meta / veritabanı kuralları:**

Bu model için `Meta` seviyesinde ayrıca belirtilmiş `ordering`, `constraints`, `indexes` veya `unique_together` bulunmuyor.

#### `Attribute`

**Amaç:** Renk, beden, kapasite gibi ürün özelliklerinin türünü temsil eder.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `name` | `string` | max_length=100; UNIQUE=True | Özellik Adı (Örn: Renk, Beden) bilgisini tutar. |
| `is_active` | `boolean` | default=True | Aktif mi? bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['name']`

#### `CategoryAttribute`

**Amaç:** Bir kategori için hangi özelliklerin kullanılacağını ve bu özelliklerin filtre/varyant/zorunluluk davranışını tanımlar.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `category` | `bigint` | FK → Category; on_delete=CASCADE; related_name='category_attributes' | Kategori bilgisini tutar. |
| `attribute` | `bigint` | FK → Attribute; on_delete=CASCADE; related_name='category_attributes' | Özellik bilgisini tutar. |
| `sort_order` | `integer` | default=0 | Sıralama bilgisini tutar. |
| `is_filterable` | `boolean` | default=True | Filtreleme yapılabilir mi? bilgisini tutar. |
| `is_required` | `boolean` | default=False | Zorunlu mu? bilgisini tutar. |
| `is_variant` | `boolean` | default=True | Varyant oluşturuyor mu? bilgisini tutar. |
| `is_visual` | `boolean` | default=False | Görselleri Değiştirir mi? bilgisini tutar. |
| `allow_custom_values` | `boolean` | default=False | Satıcı yeni değer ekleyebilir mi? bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['sort_order', 'attribute__name']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['category', 'attribute'], name='unique_category_attribute')]
  ```

#### `AttributeValue`

**Amaç:** Bir Attribute için kullanılabilir somut değeri temsil eder; örneğin Renk → Siyah.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `attribute` | `bigint` | FK → Attribute; on_delete=CASCADE; related_name='values' | Attribute ile olan ilişkiyi tutar. |
| `value` | `string` | max_length=100 | Değer (Örn: Kırmızı, XL, 128GB) bilgisini tutar. |
| `is_active` | `boolean` | default=True | Durum/özellik bayrağını tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['attribute', 'value'], name='unique_attribute_value')]
  ```

#### `ProductVariant`

**Amaç:** Global ürünün barkod ve özellik değerleriyle tanımlanan satılabilir varyantını temsil eder.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `product` | `bigint` | FK → Product; on_delete=CASCADE; related_name='variants' | Product ile olan ilişkiyi tutar. |
| `barcode` | `string` | max_length=50; UNIQUE=True; NULL=True; BLANK=True | Barkod (EAN/UPC) bilgisini tutar. |
| `attribute_values` | `bigint` | M2M ↔ AttributeValue; related_name='variants'; BLANK=True | AttributeValue ile çoktan çoğa ilişkiyi tutar. |
| `is_active` | `boolean` | default=True | Varyant Aktif mi? bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['id']`

#### `ProductImageGroup`

**Amaç:** Bir ürünün, belirli görsel özellik değerleriyle ilişkili ortak görsel grubunu temsil eder.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `product` | `bigint` | FK → Product; on_delete=CASCADE; related_name='image_groups' | Product ile olan ilişkiyi tutar. |
| `visual_attribute_values` | `bigint` | M2M ↔ AttributeValue; related_name='image_groups'; BLANK=True | AttributeValue ile çoktan çoğa ilişkiyi tutar. |
| `sort_order` | `integer` | default=0 | Sort order bilgisini tutar. |
| `is_active` | `boolean` | default=True | Durum/özellik bayrağını tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['sort_order', 'id']`
- **indexes:**
  ```python
  [models.Index(fields=['product', 'sort_order']), models.Index(fields=['product', 'is_active'])]
  ```

#### `ProductImage`

**Amaç:** Bir ProductImageGroup içindeki ürün görselidir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `group` | `bigint` | FK → ProductImageGroup; on_delete=CASCADE; related_name='images' | ProductImageGroup ile olan ilişkiyi tutar. |
| `image` | `image` |  | Image bilgisini tutar. |
| `alt_text` | `string` | max_length=255; BLANK=True | Alt Metin bilgisini tutar. |
| `is_main` | `boolean` | default=False | Kapak Fotoğrafı mı? bilgisini tutar. |
| `sort_order` | `integer` | default=0 | Sıralama bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['sort_order', 'id']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['group'], condition=Q(is_main=True), name='unique_main_product_image_per_group')]
  ```

#### `StoreProduct`

**Amaç:** Belirli bir mağazanın belirli bir ProductVariant için verdiği satış teklifini/envanter kaydını temsil eder. Fiyat ve stok burada tutulur.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `store` | `bigint` | FK → Store; on_delete=CASCADE; related_name='store_products' | Store ile olan ilişkiyi tutar. |
| `variant` | `bigint` | FK → ProductVariant; on_delete=CASCADE; related_name='store_offers' | ProductVariant ile olan ilişkiyi tutar. |
| `sku` | `string` | max_length=100; INDEX=True; NULL=True; BLANK=True | Satıcı Stok Kodu (SKU) bilgisini tutar. |
| `price` | `decimal` |  | Satış Fiyatı bilgisini tutar. |
| `stock` | `integer` | default=0 | Stok Adedi bilgisini tutar. |
| `sold_count` | `integer` | default=0 | Satış Sayısı bilgisini tutar. |
| `seller_notes` | `text` | BLANK=True | Satıcıya Özel Notlar bilgisini tutar. |
| `status` | `string` | max_length=20; default=StoreProductStatus.DRAFT | Status bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['store', 'variant'], name='unique_store_variant'), models.UniqueConstraint(fields=['store', 'sku'], name='unique_store_sku', condition=Q(sku__isnull=False))]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['variant', 'status', 'price']), models.Index(fields=['store', 'status'])]
  ```

#### `ProductPriceHistory`

**Amaç:** StoreProduct fiyatındaki geçmiş değişiklikleri zaman sırasıyla tutar.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `store_product` | `bigint` | FK → StoreProduct; on_delete=CASCADE; related_name='price_history' | StoreProduct ile olan ilişkiyi tutar. |
| `price` | `decimal` |  | Değişen Fiyat bilgisini tutar. |
| `created_at` | `datetime` |  | Değişim Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`

#### `ProductDraft`

**Amaç:** Satıcının ürün yayınlama sihirbazı sırasında oluşturduğu geçici ürün taslağıdır. Eşleşme ve yayınlama durumlarını da taşır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `seller` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='product_drafts' | Satıcı bilgisini tutar. |
| `store` | `bigint` | FK → Store; on_delete=CASCADE; related_name='product_drafts' | Mağaza bilgisini tutar. |
| `name` | `string` | max_length=255 | Ürün Adı bilgisini tutar. |
| `category` | `bigint` | FK → Category; on_delete=PROTECT; related_name='product_drafts' | Kategori bilgisini tutar. |
| `brand` | `bigint` | FK → Brand; on_delete=PROTECT; related_name='product_drafts'; NULL=True; BLANK=True | Marka bilgisini tutar. |
| `description` | `text` | BLANK=True | Açıklama bilgisini tutar. |
| `normalized_name` | `string` | max_length=255; INDEX=True; editable=False | Normalized name bilgisini tutar. |
| `normalized_key` | `string` | max_length=255; INDEX=True; editable=False | Normalized key bilgisini tutar. |
| `tokens` | `json` | editable=False; default=list | Ürün Kelimeleri bilgisini tutar. |
| `matched_product` | `bigint` | FK → Product; on_delete=SET_NULL; related_name='matched_drafts'; NULL=True; BLANK=True | Product ile olan ilişkiyi tutar. |
| `match_status` | `string` | max_length=20; INDEX=True; default=MatchStatus.NONE | Eşleşme Durumu bilgisini tutar. |
| `published_product` | `bigint` | FK → Product; on_delete=SET_NULL; related_name='drafts'; NULL=True; BLANK=True | Product ile olan ilişkiyi tutar. |
| `last_completed_step` | `smallint` | default=1 | Last completed step bilgisini tutar. |
| `current_step` | `smallint` | default=1 | Mevcut Adım bilgisini tutar. |
| `status` | `string` | max_length=20; INDEX=True; default=Status.DRAFT | Durum bilgisini tutar. |
| `completed_at` | `datetime` | NULL=True; BLANK=True | Tamamlanma Tarihi bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-updated_at']`
- **constraints:**
  ```python
  [models.CheckConstraint(check=Q(current_step__gte=1) & Q(current_step__lte=5), name='valid_product_draft_step'), models.UniqueConstraint(fields=['seller', 'store', 'category', 'normalized_key'], condition=Q(status='draft'), name='unique_active_product_draft')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['seller', 'status']), models.Index(fields=['seller', 'match_status']), models.Index(fields=['store', 'status']), models.Index(fields=['category', 'brand']), models.Index(fields=['normalized_name', 'brand', 'category']), models.Index(fields=['seller', 'updated_at'])]
  ```

#### `ProductDraftVariant`

**Amaç:** ProductDraft içinde geçici olarak oluşturulan varyanttır; yayınlama sırasında gerçek ProductVariant kayıtlarına dönüştürülür.

**Model notu:** Wizard sırasında oluşturulan geçici varyant. Ürün yayınlandığında bunlardan gerçek ProductVariant oluşturulur.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `draft` | `bigint` | FK → ProductDraft; on_delete=CASCADE; related_name='variants' | Taslak bilgisini tutar. |
| `sku` | `string` | max_length=100; INDEX=True; NULL=True; BLANK=True | Satıcı Stok Kodu (SKU) bilgisini tutar. |
| `barcode` | `string` | max_length=50; INDEX=True; NULL=True; BLANK=True | Barkod bilgisini tutar. |
| `price` | `decimal` | default=0 | Satış Fiyatı bilgisini tutar. |
| `stock` | `integer` | default=0 | Stok bilgisini tutar. |
| `is_default` | `boolean` | default=False | Varsayılan Varyant bilgisini tutar. |
| `attribute_values` | `bigint` | M2M ↔ AttributeValue; related_name='draft_variants'; BLANK=True | Özellikler bilgisini tutar. |
| `sort_order` | `integer` | default=0 | Sıralama bilgisini tutar. |
| `is_active` | `boolean` | default=True | Aktif bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['sort_order', 'id']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['draft', 'sort_order'], name='unique_draft_variant_sort_order'), models.UniqueConstraint(fields=['draft', 'barcode'], condition=Q(barcode__isnull=False), name='unique_draft_variant_barcode'), models.UniqueConstraint(fields=['draft', 'sku'], condition=Q(sku__isnull=False), name='unique_draft_variant_sku'), models.UniqueConstraint(fields=['draft'], condition=Q(is_default=True), name='unique_default_draft_variant')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['draft', 'is_active']), models.Index(fields=['draft', 'is_default']), models.Index(fields=['draft', 'sort_order'])]
  ```

#### `ProductDraftImageGroup`

**Amaç:** Ürün taslağında varyant/görsel özelliklerine göre gruplanan geçici görsel grubudur.

**Model notu:** Aynı görselleri paylaşan varyant grubunu temsil eder. Örnek: - Ortak görseller - Renk = Kırmızı - Renk = Siyah - Renk = Siyah + Boyut = 55"

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `draft` | `bigint` | FK → ProductDraft; on_delete=CASCADE; related_name='image_groups' | Taslak bilgisini tutar. |
| `visual_attribute_values` | `bigint` | M2M ↔ AttributeValue; related_name='draft_image_groups'; BLANK=True | Görsel Özellikleri bilgisini tutar. |
| `sort_order` | `integer` | default=0 | Sıralama bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `is_active` | `boolean` | default=True | Aktif mi? bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['sort_order', 'id']`
- **indexes:**
  ```python
  [models.Index(fields=['draft', 'sort_order']), models.Index(fields=['draft', 'is_active'])]
  ```

#### `ProductDraftImage`

**Amaç:** ProductDraftImageGroup içindeki taslak görseldir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `group` | `bigint` | FK → ProductDraftImageGroup; on_delete=CASCADE; related_name='images' | Görsel Grubu bilgisini tutar. |
| `image` | `image` |  | Görsel bilgisini tutar. |
| `alt_text` | `string` | max_length=255; BLANK=True | Alt Metin bilgisini tutar. |
| `sort_order` | `integer` | default=0 | Sıralama bilgisini tutar. |
| `is_active` | `boolean` | default=True | Aktif mi? bilgisini tutar. |
| `is_main` | `boolean` | default=False | Kapak Görseli mi? bilgisini tutar. |
| `file_hash` | `string` | max_length=32; INDEX=True; NULL=True; BLANK=True; editable=False | Dosya Parmak İzi (MD5) bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['sort_order', 'id']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['group'], condition=Q(is_main=True), name='unique_main_draft_image_per_group'), models.UniqueConstraint(fields=['group', 'file_hash'], condition=Q(file_hash__isnull=False), name='unique_image_hash_per_group')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['group', 'sort_order']), models.Index(fields=['group', 'is_active'])]
  ```

#### `ProductCollection`

**Amaç:** Kullanıcının oluşturduğu favori/istek listesi koleksiyonudur.

**Model notu:** Kullanıcının oluşturduğu favori/istek listeleri (Koleksiyonlar).

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='collections' | Kullanıcı bilgisini tutar. |
| `name` | `string` | max_length=100 | Liste Adı bilgisini tutar. |
| `is_default` | `boolean` | default=False | Varsayılan Favori Listesi mi? bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-is_default', '-created_at']`
- **unique_together:** `('user', 'name')`

#### `ProductCollectionItem`

**Amaç:** Bir ProductVariant’ın bir ProductCollection içine eklenmesini temsil eder; isteğe bağlı olarak eklenen StoreProduct teklifini de referanslayabilir.

**Model notu:** Listelerin içine eklenen ürünler. (Satıcı teklifine değil, doğrudan Global Katalog Ürününe bağlanır)

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `collection` | `bigint` | FK → ProductCollection; on_delete=CASCADE; related_name='items' | ProductCollection ile olan ilişkiyi tutar. |
| `variant` | `bigint` | FK → ProductVariant; on_delete=CASCADE; related_name='collection_items' | Varyant bilgisini tutar. |
| `offer` | `bigint` | FK → StoreProduct; on_delete=SET_NULL; related_name='favorited_by'; NULL=True; BLANK=True | Eklenen Teklif bilgisini tutar. |
| `added_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-added_at']`
- **unique_together:** `('collection', 'variant')`

#### `ProductQuestion`

**Amaç:** Kullanıcının ürün hakkında sorduğu soruyu temsil eder. Soru genel olabilir veya belirli bir mağazaya yöneltilebilir.

**Model notu:** Kullanıcının ürün hakkında sorduğu soru. target_store: NULL -> Genel soru. Store -> Belirli bir mağazaya yöneltilmiş soru. is_visible: Sorunun vitrinde gösterilip gösterilmeyeceğini belirtir. Cevaplanma durumu DB'de ayrıca tutulmaz. Product Detail'da gösterilebilmesi için: - is_visible=True - en az bir ProductAnswer.is_visible=True olması gerekir. Böylece cevap görünürlüğü değiştiğinde ayrıca is_answered senkronizasyonu yapmak gerekmez.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `product` | `bigint` | FK → Product; on_delete=CASCADE; related_name='questions' | Product ile olan ilişkiyi tutar. |
| `user` | `bigint` | FK → CustomUser; on_delete=SET_NULL; related_name='product_questions'; NULL=True; BLANK=True | CustomUser ile olan ilişkiyi tutar. |
| `target_store` | `bigint` | FK → Store; on_delete=SET_NULL; related_name='received_questions'; NULL=True; BLANK=True | Store ile olan ilişkiyi tutar. |
| `variant_context` | `bigint` | FK → ProductVariant; on_delete=SET_NULL; related_name='asked_questions'; NULL=True; BLANK=True | Sorunun Sorulduğu Varyant bilgisini tutar. |
| `topic` | `string` | max_length=30; default=QuestionTopic.GENERAL | Konu bilgisini tutar. |
| `text` | `text` | max_length=1000 | Soru Metni bilgisini tutar. |
| `is_visible` | `boolean` | default=True | Vitrinde Görünsün mü? bilgisini tutar. |
| `is_anonymous` | `boolean` | default=False | İsmimi Gizle bilgisini tutar. |
| `upvotes` | `integer` | default=0 | Faydalı Bulma Sayısı bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **indexes:**
  ```python
  [models.Index(fields=['product', 'is_visible', '-upvotes', '-created_at'], name='question_product_list'), models.Index(fields=['product', 'target_store', 'is_visible'], name='question_product_store'), models.Index(fields=['product', 'topic', 'is_visible'], name='question_product_topic')]
  ```

#### `ProductQuestionUpvote`

**Amaç:** Bir kullanıcının bir ProductQuestion için verdiği tekil faydalı oy kaydıdır.

**Model notu:** Bir kullanıcının bir soruya verdiği faydalı oy. Aynı kullanıcı aynı soruya yalnızca bir kez oy verebilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `question` | `bigint` | FK → ProductQuestion; on_delete=CASCADE; related_name='upvote_records' | ProductQuestion ile olan ilişkiyi tutar. |
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='product_question_upvotes' | CustomUser ile olan ilişkiyi tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['question', 'user'], name='unique_question_user_upvote')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['user', 'question'], name='upvote_user_question')]
  ```

#### `ProductAnswer`

**Amaç:** Bir mağazanın ürün sorusuna verdiği cevabı temsil eder.

**Model notu:** Satıcının soruya verdiği cevap. is_visible=False: Cevap vitrinde gösterilmez. Soru Product Detail'da yalnızca en az bir görünür cevabı varsa gösterilir. ProductAnswer.save() içerisinde Question state'i değiştirilmez. Cevap görünürlüğü tamamen kendi alanı üzerinden değerlendirilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `question` | `bigint` | FK → ProductQuestion; on_delete=CASCADE; related_name='answers' | ProductQuestion ile olan ilişkiyi tutar. |
| `store` | `bigint` | FK → Store; on_delete=CASCADE; related_name='given_answers' | Store ile olan ilişkiyi tutar. |
| `user` | `bigint` | FK → CustomUser; on_delete=SET_NULL; related_name='product_answers'; NULL=True; BLANK=True | CustomUser ile olan ilişkiyi tutar. |
| `text` | `text` | max_length=2000 | Cevap Metni bilgisini tutar. |
| `is_visible` | `boolean` | default=True | Vitrinde Görünsün mü? bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `is_read_by_user` | `boolean` | default=False | Müşteri Okudu mu? bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['created_at']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['question', 'store'], name='answer_per_question')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['question', 'is_visible', 'created_at'], name='answer_question_visible'), models.Index(fields=['store', 'is_visible', 'created_at'], name='answer_store_visible')]
  ```

## 6.4. Sepet Alanı

### `cart`

#### `Cart`

**Amaç:** Kimliği doğrulanmış kullanıcıya veya misafir session key'ine ait aktif alışveriş sepetidir.

**Model notu:** Kullanıcının aktif alışveriş sepeti.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='cart'; NULL=True; BLANK=True | CustomUser ile olan ilişkiyi tutar. |
| `session_key` | `string` | max_length=40; INDEX=True; NULL=True; BLANK=True | Session key bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(user__isnull=False, session_key__isnull=True) | models.Q(user__isnull=True, session_key__isnull=False), name='cart_user_xor_session'), models.UniqueConstraint(fields=['session_key'], condition=models.Q(session_key__isnull=False), name='unique_cart_session')]
  ```

#### `CartItem`

**Amaç:** Sepetteki tek StoreProduct satırıdır. Güncel fiyat StoreProduct üzerinden okunur; kullanıcının gördüğü son fiyat last_seen_price olarak tutulur.

**Model notu:** Sepetteki StoreProduct satırı. Fiyat burada sabit tutulmaz. StoreProduct.price üzerinden güncel fiyat alınır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `cart` | `bigint` | FK → Cart; on_delete=CASCADE; related_name='items' | Cart ile olan ilişkiyi tutar. |
| `store_product` | `bigint` | FK → StoreProduct; on_delete=CASCADE; related_name='cart_items' | StoreProduct ile olan ilişkiyi tutar. |
| `quantity` | `integer` | default=1 | Adet bilgisini tutar. |
| `is_selected` | `boolean` | default=True | Satın Alınacak mı? bilgisini tutar. |
| `last_seen_price` | `decimal` | NULL=True; BLANK=True | Kullanıcının sepette en son gördüğü fiyatı tutar; StoreProduct.price ile karşılaştırılarak fiyat değişikliği tespit edilir. |
| `added_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-added_at']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['cart', 'store_product'], name='unique_cart_store_product')]
  ```

## 6.5. Sipariş ve Ödeme Alanı

### `orders`

#### `Order`

**Amaç:** Tek checkout/ödeme akışında oluşturulan ana sipariştir. Multi-vendor yapıda mağaza bazlı SubOrder kayıtlarının üst kaydıdır.

**Model notu:** Müşterinin tek checkout / tek ödeme sürecindeki ana siparişi. Multi-vendor yapı: Order ├── SubOrder (Store A) │ ├── OrderItem │ └── OrderItem │ ├── SubOrder (Store B) │ └── OrderItem │ └── PaymentTransaction Customer: Order'ın tamamını görebilir. Seller: Yalnızca kendi Store'una ait SubOrder'ı görebilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `order_number` | `string` | max_length=32; UNIQUE=True; editable=False; default=generate_order_number | Sipariş Numarası bilgisini tutar. |
| `user` | `bigint` | FK → CustomUser; on_delete=SET_NULL; related_name='orders'; NULL=True; BLANK=True | Müşteri bilgisini tutar. |
| `buyer_identity_number` | `string` | max_length=50; BLANK=True | Buyer identity number bilgisini tutar. |
| `customer_email` | `string` | NULL=True; BLANK=True | Müşteri E-posta bilgisini tutar. |
| `customer_phone` | `string` | max_length=20; NULL=True; BLANK=True | Müşteri Telefonu bilgisini tutar. |
| `subtotal` | `decimal` | default=Decimal('0.00') | Ara Toplam bilgisini tutar. |
| `discount_amount` | `decimal` | default=Decimal('0.00') | İndirim Tutarı bilgisini tutar. |
| `shipping_amount` | `decimal` | default=Decimal('0.00') | Kargo Tutarı bilgisini tutar. |
| `tax_amount` | `decimal` | default=Decimal('0.00') | Vergi Tutarı bilgisini tutar. |
| `total_amount` | `decimal` | default=Decimal('0.00') | Genel Toplam bilgisini tutar. |
| `currency` | `string` | max_length=3; default='TRY' | Para Birimi bilgisini tutar. |
| `discount_code_snapshot` | `string` | max_length=100; BLANK=True; default='' | Sipariş oluşturulduğu anda kullanılan indirim kodunun snapshot değeridir. |
| `status` | `string` | max_length=30; INDEX=True; default=OrderStatus.PENDING_PAYMENT | Sipariş Durumu bilgisini tutar. |
| `shipping_full_name` | `string` | max_length=255 | Teslimat Alıcısı bilgisini tutar. |
| `shipping_phone` | `string` | max_length=20 | Teslimat Telefonu bilgisini tutar. |
| `shipping_address_line1` | `string` | max_length=255 | Teslimat Adresi bilgisini tutar. |
| `shipping_address_line2` | `string` | max_length=255; BLANK=True; default='' | Teslimat Adresi 2 bilgisini tutar. |
| `shipping_city` | `string` | max_length=100 | Teslimat Şehri bilgisini tutar. |
| `shipping_state` | `string` | max_length=100 | Teslimat İlçesi bilgisini tutar. |
| `shipping_postal_code` | `string` | max_length=20 | Teslimat Posta Kodu bilgisini tutar. |
| `billing_full_name` | `string` | max_length=255 | Fatura Adı bilgisini tutar. |
| `billing_phone` | `string` | max_length=20 | Fatura Telefonu bilgisini tutar. |
| `billing_address_line1` | `string` | max_length=255 | Fatura Adresi bilgisini tutar. |
| `billing_address_line2` | `string` | max_length=255; BLANK=True; default='' | Fatura Adresi 2 bilgisini tutar. |
| `billing_city` | `string` | max_length=100 | Fatura Şehri bilgisini tutar. |
| `billing_state` | `string` | max_length=100 | Fatura İlçesi bilgisini tutar. |
| `billing_postal_code` | `string` | max_length=20 | Fatura Posta Kodu bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |
| `paid_at` | `datetime` | NULL=True; BLANK=True | Ödeme Tarihi bilgisini tutar. |
| `completed_at` | `datetime` | NULL=True; BLANK=True | Tamamlanma Tarihi bilgisini tutar. |
| `cancelled_at` | `datetime` | NULL=True; BLANK=True | İptal Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(subtotal__gte=0), name='order_subtotal_gte_0'), models.CheckConstraint(condition=models.Q(discount_amount__gte=0), name='order_discount_gte_0'), models.CheckConstraint(condition=models.Q(shipping_amount__gte=0), name='order_shipping_gte_0'), models.CheckConstraint(condition=models.Q(tax_amount__gte=0), name='order_tax_gte_0'), models.CheckConstraint(condition=models.Q(total_amount__gte=0), name='order_total_gte_0')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['user', '-created_at'], name='order_user_created_idx'), models.Index(fields=['status', '-created_at'], name='order_status_created_idx')]
  ```

#### `SubOrder`

**Amaç:** Bir Order içindeki tek bir mağazaya ait sipariş alt kaydıdır.

**Model notu:** Bir Order içerisindeki tek bir mağazaya ait sipariş. Aynı Order içerisinde aynı Store için yalnızca bir SubOrder olabilir. Customer: Ana Order üzerinden tüm SubOrder'ları görebilir. Seller: Yalnızca kendisine ait Store'un SubOrder'ını görebilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `order` | `bigint` | FK → Order; on_delete=CASCADE; related_name='sub_orders' | Ana Sipariş bilgisini tutar. |
| `store` | `bigint` | FK → Store; on_delete=PROTECT; related_name='received_orders' | Mağaza bilgisini tutar. |
| `suborder_number` | `string` | max_length=32; UNIQUE=True; editable=False; default=generate_suborder_number | Alt Sipariş Numarası bilgisini tutar. |
| `store_name_snapshot` | `string` | max_length=255 | Sipariş anında mağazanın görünen adını tarihsel olarak korur. |
| `subtotal` | `decimal` | default=Decimal('0.00') | Ara Toplam bilgisini tutar. |
| `discount_amount` | `decimal` | default=Decimal('0.00') | İndirim Tutarı bilgisini tutar. |
| `shipping_amount` | `decimal` | default=Decimal('0.00') | Kargo Tutarı bilgisini tutar. |
| `tax_amount` | `decimal` | default=Decimal('0.00') | Vergi Tutarı bilgisini tutar. |
| `total_amount` | `decimal` | default=Decimal('0.00') | Genel Toplam bilgisini tutar. |
| `status` | `string` | max_length=30; INDEX=True; default=SubOrderStatus.PENDING | Mağaza Sipariş Durumu bilgisini tutar. |
| `cargo_company` | `string` | max_length=30; BLANK=True; default='' | Kargo Firması bilgisini tutar. |
| `cargo_tracking_number` | `string` | max_length=100; INDEX=True; BLANK=True; default='' | Kargo Takip Numarası bilgisini tutar. |
| `shipped_at` | `datetime` | NULL=True; BLANK=True | Kargoya Verilme Tarihi bilgisini tutar. |
| `delivered_at` | `datetime` | NULL=True; BLANK=True | Teslim Tarihi bilgisini tutar. |
| `cancelled_at` | `datetime` | NULL=True; BLANK=True | İptal Tarihi bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['order', 'store'], name='unique_store_per_order'), models.CheckConstraint(condition=models.Q(subtotal__gte=0), name='suborder_subtotal_gte_0'), models.CheckConstraint(condition=models.Q(discount_amount__gte=0), name='suborder_discount_gte_0'), models.CheckConstraint(condition=models.Q(shipping_amount__gte=0), name='suborder_shipping_gte_0'), models.CheckConstraint(condition=models.Q(tax_amount__gte=0), name='suborder_tax_gte_0'), models.CheckConstraint(condition=models.Q(total_amount__gte=0), name='suborder_total_gte_0')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['store', '-created_at'], name='suborder_store_created_idx'), models.Index(fields=['status', '-created_at'], name='suborder_status_created_idx')]
  ```

#### `OrderItem`

**Amaç:** Gerçekte satın alınan kalemin tarihsel sipariş kaydıdır. Canlı StoreProduct bağlantısının yanında sipariş anındaki kritik bilgiler snapshot olarak tutulur.

**Model notu:** Gerçekte satın alınan ürünün sipariş snapshot kaydı. StoreProduct canlı bir referans olarak tutulur ancak geçmiş sipariş için gerekli kritik bilgiler ayrıca snapshot'lanır. StoreProduct: - fiyat değiştirebilir - SKU değiştirebilir - arşivlenebilir OrderItem: sipariş anındaki bilgileri korur.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `sub_order` | `bigint` | FK → SubOrder; on_delete=CASCADE; related_name='items' | Mağaza Siparişi bilgisini tutar. |
| `store_product` | `bigint` | FK → StoreProduct; on_delete=PROTECT; related_name='order_items' | Mağaza Ürünü bilgisini tutar. |
| `source_cart_item` | `bigint` | FK → CartItem; on_delete=SET_NULL; related_name='+'; NULL=True; BLANK=True | Checkout sırasında oluşturulan OrderItem'ın kaynak CartItem kaydını tutar; CartItem silinirse SET_NULL ile korunur. |
| `source_cart_item_updated_at` | `datetime` | NULL=True; BLANK=True | Checkout anında kaynak CartItem'ın updated_at değerinin snapshot'ıdır. |
| `image_snapshot` | `image` | NULL=True; BLANK=True | Sipariş kaleminin sipariş anındaki ürün görselinin snapshot kopyasıdır. |
| `product_name_snapshot` | `string` | max_length=255 | Sipariş anındaki ürün adının snapshot kopyasıdır. |
| `variant_snapshot` | `json` | BLANK=True; default=dict | Sipariş anındaki varyant özelliklerinin yapılandırılmış snapshot verisidir. |
| `variant_display` | `string` | max_length=500; BLANK=True; default='' | Sipariş anındaki varyantın kullanıcı arayüzünde gösterilecek metin halidir. |
| `sku_snapshot` | `string` | max_length=100; BLANK=True; default='' | Sipariş anındaki SKU değerinin snapshot kopyasıdır. |
| `barcode_snapshot` | `string` | max_length=50; BLANK=True; default='' | Sipariş anındaki barkod değerinin snapshot kopyasıdır. |
| `quantity` | `integer` | default=1 | Adet bilgisini tutar. |
| `unit_price` | `decimal` |  | Birim Fiyat bilgisini tutar. |
| `discount_amount` | `decimal` | default=Decimal('0.00') | İndirim bilgisini tutar. |
| `tax_amount` | `decimal` | default=Decimal('0.00') | Vergi bilgisini tutar. |
| `total_amount` | `decimal` |  | Satır Toplamı bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['id']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(quantity__gte=1), name='orderitem_quantity_gte_1'), models.CheckConstraint(condition=models.Q(unit_price__gte=0), name='orderitem_unit_price_gte_0'), models.CheckConstraint(condition=models.Q(discount_amount__gte=0), name='orderitem_discount_gte_0'), models.CheckConstraint(condition=models.Q(tax_amount__gte=0), name='orderitem_tax_gte_0'), models.CheckConstraint(condition=models.Q(total_amount__gte=0), name='orderitem_total_gte_0')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['sub_order', 'id'], name='orderitem_suborder_idx')]
  ```

#### `StockReservation`

**Amaç:** Checkout/ödeme sırasında OrderItem için geçici stok rezervasyonunu tutar.

**Model notu:** Checkout / ödeme sürecinde OrderItem için stoğu geçici olarak rezerve eder. İlişki: Order ↓ SubOrder ↓ OrderItem ↓ StockReservation store_product ayrıca tutulmaz. Çünkü OrderItem zaten StoreProduct'a bağlıdır. Aynı StoreProduct bilgisini iki farklı yerde tutmak veri tutarsızlığı oluşturabilir. Lifecycle: ACTIVE ├──> CONSUMED ├──> RELEASED └──> EXPIRED

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `order_item` | `bigint` | FK → OrderItem; on_delete=CASCADE; related_name='reservations' | Sipariş Kalemi bilgisini tutar. |
| `quantity` | `integer` |  | Rezerve Adet bilgisini tutar. |
| `status` | `string` | max_length=20; INDEX=True; default=ReservationStatus.ACTIVE | Rezervasyon Durumu bilgisini tutar. |
| `expires_at` | `datetime` | INDEX=True | Rezervasyon Bitiş Tarihi bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(quantity__gte=1), name='reservation_quantity_gte_1'), models.UniqueConstraint(fields=['order_item'], condition=models.Q(status=ReservationStatus.ACTIVE), name='unique_active_reservation_per_item')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['status', 'expires_at'], name='reservation_status_exp_idx'), models.Index(fields=['order_item', 'status'], name='reservation_item_status_idx')]
  ```

#### `Invoice`

**Amaç:** Bir SubOrder için oluşturulan faturanın snapshot kaydıdır. Seller ve buyer bilgileri ile finansal değerleri fatura anındaki haliyle saklar.

**Model notu:** Seller tarafından SubOrder için oluşturulan fatura kaydı. Invoice, oluşturulduğu andaki: - seller legal bilgilerini, - buyer billing bilgilerini, - SubOrder finansal değerlerini snapshot olarak saklar. Invoice oluşturulduktan sonra kaynak modellerdeki değişiklikler mevcut invoice kaydını değiştirmemelidir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `suborder` | `bigint` | FK → SubOrder; on_delete=PROTECT; related_name='invoice' | Alt Sipariş bilgisini tutar. |
| `invoice_number` | `string` | max_length=50; UNIQUE=True; INDEX=True | Fatura Numarası bilgisini tutar. |
| `issued_at` | `datetime` |  | Düzenlenme Tarihi bilgisini tutar. |
| `seller_display_name` | `string` | max_length=255 | Satıcı Görünen Adı bilgisini tutar. |
| `seller_legal_company_title` | `string` | max_length=255; BLANK=True | Fatura oluşturulduğu anda satıcının yasal unvanının snapshot kopyasıdır. |
| `seller_address` | `text` |  | Satıcı Adresi bilgisini tutar. |
| `seller_phone` | `string` | max_length=20; BLANK=True | Satıcı Telefonu bilgisini tutar. |
| `seller_tax_office` | `string` | max_length=150; BLANK=True | Vergi Dairesi bilgisini tutar. |
| `seller_tax_number` | `string` | max_length=50; BLANK=True | Vergi Numarası bilgisini tutar. |
| `seller_identity_number` | `string` | max_length=20; BLANK=True | Satıcı T.C. Kimlik Numarası bilgisini tutar. |
| `buyer_full_name` | `string` | max_length=255 | Alıcı Adı Soyadı bilgisini tutar. |
| `buyer_phone` | `string` | max_length=20; BLANK=True | Alıcı Telefonu bilgisini tutar. |
| `buyer_email` | `string` | BLANK=True | Alıcı E-posta bilgisini tutar. |
| `buyer_identity_number` | `string` | max_length=20; BLANK=True | Alıcı T.C. Kimlik / Vergi Numarası bilgisini tutar. |
| `buyer_billing_address_line1` | `string` | max_length=255 | Fatura Adresi bilgisini tutar. |
| `buyer_billing_address_line2` | `string` | max_length=255; BLANK=True | Fatura Adresi 2 bilgisini tutar. |
| `buyer_billing_city` | `string` | max_length=100 | Fatura Şehri bilgisini tutar. |
| `buyer_billing_state` | `string` | max_length=100 | Fatura İlçesi bilgisini tutar. |
| `buyer_billing_postal_code` | `string` | max_length=20 | Fatura Posta Kodu bilgisini tutar. |
| `subtotal` | `decimal` |  | Ara Toplam bilgisini tutar. |
| `discount_amount` | `decimal` | default=Decimal('0.00') | İndirim Tutarı bilgisini tutar. |
| `shipping_amount` | `decimal` | default=Decimal('0.00') | Kargo Tutarı bilgisini tutar. |
| `tax_amount` | `decimal` | default=Decimal('0.00') | Vergi Tutarı bilgisini tutar. |
| `total_amount` | `decimal` |  | Genel Toplam bilgisini tutar. |
| `currency` | `string` | max_length=3 | Para Birimi bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-issued_at', '-pk']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(subtotal__gte=0), name='invoice_subtotal_gte_0'), models.CheckConstraint(condition=models.Q(discount_amount__gte=0), name='invoice_discount_gte_0'), models.CheckConstraint(condition=models.Q(shipping_amount__gte=0), name='invoice_shipping_gte_0'), models.CheckConstraint(condition=models.Q(tax_amount__gte=0), name='invoice_tax_gte_0'), models.CheckConstraint(condition=models.Q(total_amount__gte=0), name='invoice_total_gte_0')]
  ```

#### `InvoiceItem`

**Amaç:** Invoice içindeki tek bir ürün satırının değiştirilemez tarihsel snapshot kaydıdır.

**Model notu:** Invoice üzerinde yer alan ürün satırı. InvoiceItem, Invoice oluşturulduğu andaki OrderItem bilgilerinin immutable snapshot'ını temsil eder. Kaynak OrderItem sonradan değişse veya silinse bile InvoiceItem kendi tarihsel verisini korur.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `invoice` | `bigint` | FK → Invoice; on_delete=PROTECT; related_name='items' | Fatura bilgisini tutar. |
| `product_name` | `string` | max_length=255 | Ürün Adı bilgisini tutar. |
| `variant_display` | `string` | max_length=500; BLANK=True | Sipariş anındaki varyantın kullanıcı arayüzünde gösterilecek metin halidir. |
| `sku` | `string` | max_length=100; BLANK=True | SKU bilgisini tutar. |
| `barcode` | `string` | max_length=100; BLANK=True | Barkod bilgisini tutar. |
| `quantity` | `integer` |  | Miktar bilgisini tutar. |
| `unit_price` | `decimal` |  | Birim Fiyat bilgisini tutar. |
| `discount_amount` | `decimal` | default=Decimal('0.00') | İndirim Tutarı bilgisini tutar. |
| `tax_amount` | `decimal` | default=Decimal('0.00') | Vergi Tutarı bilgisini tutar. |
| `total_amount` | `decimal` |  | Toplam Tutar bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['pk']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='invoice_item_quantity_gt_0'), models.CheckConstraint(condition=models.Q(unit_price__gte=0), name='invoice_item_unit_price_gte_0'), models.CheckConstraint(condition=models.Q(discount_amount__gte=0), name='invoice_item_discount_gte_0'), models.CheckConstraint(condition=models.Q(tax_amount__gte=0), name='invoice_item_tax_gte_0'), models.CheckConstraint(condition=models.Q(total_amount__gte=0), name='invoice_item_total_gte_0')]
  ```

#### `PaymentTransaction`

**Amaç:** Bir Order için gerçekleştirilen tek bir ödeme denemesini temsil eder. Aynı Order altında birden fazla deneme olabilir.

**Model notu:** Bir Order için yapılan tek bir ödeme denemesi. Aynı Order altında birden fazla PaymentTransaction olabilir. Örnek: Order ├── Payment #1 -> FAILED ├── Payment #2 -> FAILED └── Payment #3 -> SUCCESS PaymentTransaction: ödeme attempt'ini temsil eder. Order: siparişin genel durumunu temsil eder.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `order` | `bigint` | FK → Order; on_delete=CASCADE; related_name='payment_transactions' | Sipariş bilgisini tutar. |
| `provider` | `string` | max_length=50; INDEX=True; default='iyzico' | Ödeme Sağlayıcı bilgisini tutar. |
| `payment_id` | `string` | max_length=100; NULL=True; BLANK=True | Provider Payment ID bilgisini tutar. |
| `conversation_id` | `string` | max_length=100 | Provider ile yapılan işlem için sistem tarafından izlenen benzersiz iletişim/işlem anahtarıdır. |
| `basket_id` | `string` | max_length=100; NULL=True; BLANK=True | Basket ID bilgisini tutar. |
| `status` | `string` | max_length=30; INDEX=True; default=PaymentStatus.INITIATED | Ödeme Durumu bilgisini tutar. |
| `fraud_status` | `integer` | NULL=True; BLANK=True | Fraud Status bilgisini tutar. |
| `paid_price` | `decimal` | NULL=True; BLANK=True | Ödenen Tutar bilgisini tutar. |
| `currency` | `string` | max_length=3; default='TRY' | Para Birimi bilgisini tutar. |
| `installment_count` | `smallint` | default=1 | Taksit Sayısı bilgisini tutar. |
| `last_four_digits` | `string` | max_length=4; NULL=True; BLANK=True | Kart Son 4 Hane bilgisini tutar. |
| `card_type` | `string` | max_length=50; NULL=True; BLANK=True | Kart Tipi bilgisini tutar. |
| `card_association` | `string` | max_length=50; NULL=True; BLANK=True | Kart Markası bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |
| `succeeded_at` | `datetime` | NULL=True; BLANK=True | Başarılı Olma Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['provider', 'payment_id'], condition=models.Q(payment_id__isnull=False), name='unique_provider_payment'), models.UniqueConstraint(fields=['provider', 'conversation_id'], name='unique_provider_conversation'), models.CheckConstraint(condition=models.Q(paid_price__gte=0) | models.Q(paid_price__isnull=True), name='payment_paid_price_gte_0')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['order', '-created_at'], name='payment_order_created_idx'), models.Index(fields=['status', '-created_at'], name='payment_status_created_idx')]
  ```

#### `PaymentTransactionItem`

**Amaç:** Bir PaymentTransaction içindeki provider tarafı ürün/kargo kalem eşlemesini ve tutarını tutar.

**Model notu:** Bir PaymentTransaction içindeki iyzico basket/payment split'ini temsil eder. Örnek: PaymentTransaction ├── OrderItem #15 → iyzico transaction 12345 ├── OrderItem #16 → iyzico transaction 12346 └── OrderItem #17 → iyzico transaction 12347 iyzico marketplace refund işlemi paymentTransactionId üzerinden yapıldığı için bu ilişki localde saklanmalıdır.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `payment_transaction` | `bigint` | FK → PaymentTransaction; on_delete=PROTECT; related_name='items' | Ödeme İşlemi bilgisini tutar. |
| `suborder` | `bigint` | FK → SubOrder; on_delete=PROTECT; related_name='payment_transaction_items' | SubOrder ile olan ilişkiyi tutar. |
| `order_item` | `bigint` | FK → OrderItem; on_delete=PROTECT; related_name='payment_transaction_items'; NULL=True; BLANK=True | Sipariş Kalemi bilgisini tutar. |
| `item_type` | `string` | max_length=20 | Item type bilgisini tutar. |
| `provider_item_id` | `string` | max_length=100 | Ödeme sağlayıcısındaki basket/payment item kimliğidir. |
| `provider_transaction_id` | `string` | max_length=100 | Ödeme sağlayıcısındaki işlem kimliğidir. |
| `price` | `decimal` |  | Provider Kalem Tutarı bilgisini tutar. |
| `paid_price` | `decimal` |  | Tahsil Edilen Kalem Tutarı bilgisini tutar. |
| `transaction_status` | `integer` | NULL=True; BLANK=True | Provider Transaction Status bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['payment_transaction', 'provider_item_id'], name='unique_payment_provider_item'), models.UniqueConstraint(fields=['payment_transaction', 'provider_transaction_id'], name='unique_payment_provider_tx'), models.UniqueConstraint(fields=['provider_transaction_id'], name='unique_provider_transaction_id'), models.UniqueConstraint(fields=['payment_transaction', 'order_item'], condition=models.Q(order_item__isnull=False), name='unique_payment_order_item'), models.UniqueConstraint(fields=['payment_transaction', 'suborder'], condition=models.Q(item_type='shipping'), name='unique_payment_shipping_suborder'), models.CheckConstraint(condition=models.Q(item_type='product', order_item__isnull=False) | models.Q(item_type='shipping', order_item__isnull=True), name='payment_item_type_shape')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['payment_transaction', 'suborder'], name='ptxitem_pay_sub_idx'), models.Index(fields=['payment_transaction', 'order_item'], name='ptxitem_pay_ord_idx')]
  ```

#### `PaymentCustomer`

**Amaç:** Kullanıcının ödeme sağlayıcısındaki müşteri kimliğini temsil eder.

**Model notu:** Kullanıcının ödeme sağlayıcısındaki müşteri kimliğini temsil eder. iyzico Card Storage tarafında: provider_customer_key = cardUserKey Bir kullanıcı birden fazla kayıtlı karta sahip olabilir. Tüm kartlar aynı cardUserKey altında tutulabilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='payment_customers' | Kullanıcı bilgisini tutar. |
| `provider` | `string` | max_length=50; INDEX=True; default='iyzico' | Ödeme Sağlayıcı bilgisini tutar. |
| `provider_customer_key` | `string` | max_length=255 | Ödeme sağlayıcısındaki müşteri kimliğidir. |
| `provider_external_id` | `string` | max_length=255; BLANK=True; default='' | Provider External ID bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['user', 'provider'], name='unique_payment_customer_provider'), models.UniqueConstraint(fields=['provider', 'provider_customer_key'], name='unique_provider_customer_key')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['user', 'provider'], name='paycust_user_prov_idx')]
  ```

#### `StoredCard`

**Amaç:** Ödeme sağlayıcısı tarafından tokenize edilmiş kayıtlı kartı temsil eder. PAN ve CVC gibi hassas kart verileri tutulmaz.

**Model notu:** iyzico Card Storage tarafından tokenize edilmiş kayıtlı kart. ÖNEMLİ: Burada: - PAN / card number - CVC tutulmaz. Yalnızca provider'ın verdiği token ve kart metadata'sı tutulur.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `payment_customer` | `bigint` | FK → PaymentCustomer; on_delete=CASCADE; related_name='stored_cards' | Ödeme Müşterisi bilgisini tutar. |
| `provider_card_token` | `string` | max_length=255 | Ödeme sağlayıcısının tokenize edilmiş kart referansıdır; ham kart numarası değildir. |
| `card_alias` | `string` | max_length=293; BLANK=True; default='' | Kart Takma Adı bilgisini tutar. |
| `bin_number` | `string` | max_length=8; BLANK=True; default='' | BIN bilgisini tutar. |
| `last_four_digits` | `string` | max_length=4 | Son 4 Hane bilgisini tutar. |
| `card_type` | `string` | max_length=50; BLANK=True; default='' | Kart Tipi bilgisini tutar. |
| `card_association` | `string` | max_length=50; BLANK=True; default='' | Kart Markası bilgisini tutar. |
| `card_family` | `string` | max_length=100; BLANK=True; default='' | Kart Ailesi bilgisini tutar. |
| `card_bank_code` | `integer` | NULL=True; BLANK=True | Banka Kodu bilgisini tutar. |
| `card_bank_name` | `string` | max_length=150; BLANK=True; default='' | Banka Adı bilgisini tutar. |
| `expire_month` | `string` | max_length=2; BLANK=True; default='' | Son Kullanma Ayı bilgisini tutar. |
| `expire_year` | `string` | max_length=4; BLANK=True; default='' | Son Kullanma Yılı bilgisini tutar. |
| `is_default` | `boolean` | default=False | Varsayılan Kart bilgisini tutar. |
| `is_active` | `boolean` | INDEX=True; default=True | Aktif bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-is_default', '-created_at', '-pk']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['payment_customer', 'provider_card_token'], name='unique_stcard_token'), models.UniqueConstraint(fields=['payment_customer'], condition=Q(is_active=True, is_default=True), name='unique_act_def_stcard')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['payment_customer', 'is_active'], name='stcard_cust_active_idx'), models.Index(fields=['payment_customer', 'is_default'], name='stcard_cust_default_idx')]
  ```

#### `StoredCardOperation`

**Amaç:** Kayıtlı kart oluşturma/silme işlemlerinin idempotency ve provider senkronizasyon durumunu tutar.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `user` | `bigint` | FK → CustomUser; on_delete=CASCADE; related_name='stored_card_operations' | CustomUser ile olan ilişkiyi tutar. |
| `provider` | `string` | max_length=30; default='iyzico' | Provider bilgisini tutar. |
| `operation_type` | `string` | max_length=20 | Operation type bilgisini tutar. |
| `status` | `string` | max_length=32; default=StoredCardOperationStatus.PENDING | Status bilgisini tutar. |
| `idempotency_key` | `string` | max_length=128 | Aynı mantıksal kart işleminin birden fazla kez uygulanmasını önlemek için kullanılan benzersiz işlem anahtarıdır. |
| `request_fingerprint` | `string` | max_length=64 | Gelen isteğin aynı istek olup olmadığını karşılaştırmak için kullanılan parmak izi değeridir. |
| `stored_card` | `bigint` | FK → StoredCard; on_delete=PROTECT; related_name='operations'; NULL=True; BLANK=True | StoredCard ile olan ilişkiyi tutar. |
| `make_default` | `boolean` | default=False | Make default bilgisini tutar. |
| `provider_card_token` | `string` | max_length=255; NULL=True; BLANK=True | Ödeme sağlayıcısının tokenize edilmiş kart referansıdır; ham kart numarası değildir. |
| `provider_customer_key` | `string` | max_length=64; NULL=True; BLANK=True | Ödeme sağlayıcısındaki müşteri kimliğidir. |
| `provider_external_id` | `string` | max_length=255; NULL=True; BLANK=True | Ödeme sağlayıcısından gelen ilgili tanımlayıcı/metaveriyi tutar. |
| `error_code` | `string` | max_length=100; BLANK=True | Error code bilgisini tutar. |
| `error_message` | `string` | max_length=500; BLANK=True | Error message bilgisini tutar. |
| `completed_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at', '-pk']`
- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['user', 'provider', 'operation_type', 'idempotency_key'], name='stcard_idmptncy_key'), models.UniqueConstraint(fields=['user', 'provider', 'operation_type'], condition=Q(operation_type=StoredCardOperationType.CREATE, status__in=[StoredCardOperationStatus.PENDING, StoredCardOperationStatus.PROVIDER_SUCCEEDED, StoredCardOperationStatus.RECONCILIATION_REQUIRED]), name='active_stcard_create'), models.UniqueConstraint(fields=['stored_card', 'provider', 'operation_type'], condition=Q(operation_type=StoredCardOperationType.DELETE, status__in=[StoredCardOperationStatus.PENDING, StoredCardOperationStatus.PROVIDER_SUCCEEDED, StoredCardOperationStatus.RECONCILIATION_REQUIRED]), name='active_stcard_delete'), models.CheckConstraint(condition=Q(operation_type=StoredCardOperationType.CREATE) | Q(operation_type=StoredCardOperationType.DELETE, stored_card__isnull=False), name='stcard_target_valid')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['user', 'operation_type', 'status', 'created_at'], name='op_user_state_idx'), models.Index(fields=['stored_card', 'operation_type', 'status'], name='op_card_state_idx')]
  ```

#### `PaymentRefund`

**Amaç:** Bir PaymentTransaction üzerinden gerçekleştirilen mantıksal iade kaydıdır. Bir ödeme için birden fazla refund bulunabilir.

**Model notu:** PaymentTransaction üzerinden gerçekleştirilen refund kaydı. Bir payment birden fazla refund içerebilir. Örnek: Payment = 1.000 TL Refund #1 = 200 TL Refund #2 = 300 TL Toplam refund = 500 TL

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `payment_transaction` | `bigint` | FK → PaymentTransaction; on_delete=PROTECT; related_name='refunds' | Ödeme İşlemi bilgisini tutar. |
| `suborder` | `bigint` | FK → SubOrder; on_delete=PROTECT; related_name='payment_refunds' | Alt Sipariş bilgisini tutar. |
| `refund_reference` | `string` | max_length=100; UNIQUE=True; editable=False; default=generate_refund_reference | Refund Referansı bilgisini tutar. |
| `amount` | `decimal` |  | İade Tutarı bilgisini tutar. |
| `currency` | `string` | max_length=3; default='TRY' | Para Birimi bilgisini tutar. |
| `status` | `string` | max_length=30; INDEX=True; default=RefundStatus.PENDING | İade Durumu bilgisini tutar. |
| `reason` | `string` | max_length=255; BLANK=True; default='' | İade Nedeni bilgisini tutar. |
| `refund_type` | `string` | max_length=30 | İade Türü bilgisini tutar. |
| `refund_shipping` | `boolean` | default=False | Kargo Ücreti İade Ediliyor bilgisini tutar. |
| `created_at` | `datetime` |  | Oluşturulma Tarihi bilgisini tutar. |
| `updated_at` | `datetime` |  | Güncellenme Tarihi bilgisini tutar. |
| `completed_at` | `datetime` | NULL=True; BLANK=True | İade Tamamlanma Tarihi bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-created_at']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(amount__gt=0), name='refund_amount_gt_0')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['payment_transaction', '-created_at'], name='refund_payment_created_idx'), models.Index(fields=['status', '-created_at'], name='refund_status_created_idx')]
  ```

#### `PaymentRefundItem`

**Amaç:** Bir PaymentRefund içindeki provider seviyesindeki tekil iade kalemidir.

**Model notu:** Bir PaymentRefund içerisindeki provider-level refund işlemidir. Tek bir logical refund birden fazla iyzico basket item'ına bölünebilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `payment_refund` | `bigint` | FK → PaymentRefund; on_delete=PROTECT; related_name='items' | Ödeme İadesi bilgisini tutar. |
| `payment_transaction_item` | `bigint` | FK → PaymentTransactionItem; on_delete=PROTECT; related_name='refund_items' | Ödeme İşlem Kalemi bilgisini tutar. |
| `amount` | `decimal` |  | İade Tutarı bilgisini tutar. |
| `provider_refund_id` | `string` | max_length=255; INDEX=True; NULL=True; BLANK=True | Provider Refund ID bilgisini tutar. |
| `status` | `string` | max_length=30; INDEX=True; default=RefundStatus.PENDING | İade Durumu bilgisini tutar. |
| `conversation_id` | `string` | max_length=100; UNIQUE=True | Provider ile yapılan işlem için sistem tarafından izlenen benzersiz iletişim/işlem anahtarıdır. |
| `retryable` | `boolean` | NULL=True; BLANK=True | Tekrar Denenebilir bilgisini tutar. |
| `provider_error_code` | `string` | max_length=100; BLANK=True; default='' | Ödeme sağlayıcısından gelen ilgili tanımlayıcı/metaveriyi tutar. |
| `provider_error_message` | `string` | max_length=500; BLANK=True; default='' | Ödeme sağlayıcısından gelen ilgili tanımlayıcı/metaveriyi tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `updated_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |
| `completed_at` | `datetime` | NULL=True; BLANK=True | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **constraints:**
  ```python
  [models.UniqueConstraint(fields=['payment_refund', 'payment_transaction_item'], name='unique_refund_transaction_item'), models.CheckConstraint(condition=models.Q(amount__gt=0), name='refund_item_amount_gt_0')]
  ```
- **indexes:**
  ```python
  [models.Index(fields=['status', '-created_at'], name='refunditem_status_created_idx'), models.Index(fields=['payment_transaction_item', 'status'], name='ptxitem_status_idx')]
  ```

#### `SubOrderCancellation`

**Amaç:** Bir SubOrder'ın iptal edilmesinin tarihsel kaydıdır. İade işlemi ayrı bir PaymentRefund domain kaydıdır.

**Model notu:** Bir SubOrder'ın seller/system tarafından iptal edilmesinin tarihsel kaydı. Cancellation ile PaymentRefund ayrı domain kayıtlarıdır. Refund henüz oluşturulmamış veya tamamlanmamış olabilir.

| Alan | Tür | İlişki / DB kuralı | Açıklama |
|---|---|---|---|
| `suborder` | `bigint` | FK → SubOrder; on_delete=PROTECT; related_name='cancellation' | Alt Sipariş bilgisini tutar. |
| `cancelled_by` | `bigint` | FK → CustomUser; on_delete=PROTECT; related_name='suborder_cancellations'; NULL=True; BLANK=True | İptal Eden bilgisini tutar. |
| `reason` | `string` | max_length=255 | İptal Nedeni bilgisini tutar. |
| `refund_amount` | `decimal` |  | İade Tutarı bilgisini tutar. |
| `payment_refund` | `bigint` | FK → PaymentRefund; on_delete=PROTECT; related_name='cancellation'; NULL=True; BLANK=True | Ödeme İadesi bilgisini tutar. |
| `cancelled_at` | `datetime` |  | İptal Tarihi bilgisini tutar. |
| `created_at` | `datetime` |  | Tarih ve zaman bilgisini tutar. |

**Meta / veritabanı kuralları:**

- **ordering:** `['-cancelled_at', '-pk']`
- **constraints:**
  ```python
  [models.CheckConstraint(condition=models.Q(refund_amount__gte=0), name='suborder_cancel_refund_gte_0')]
  ```

## 7. `CustomUser` Hakkında Ek Not

`CustomUser`, `AbstractUser` üzerinden miras aldığı için aşağıdaki Django alanları da fiziksel veritabanında bulunur: `password`, `last_login`, `is_superuser`, `first_name`, `last_name`, `is_staff`, `is_active`, `date_joined`, `groups`, `user_permissions`. Proje `username = None` ile varsayılan username alanını kaldırmaktadır.

`USERNAME_FIELD = "email"` olarak tanımlıdır; uygulama ayrıca email veya telefon ile authentication sağlayan özel backend kullanır. `email` ve `phone_number` nullable/optional olsa da model `clean()` metodunda en az bir iletişim bilgisinin bulunması beklenir.

## 8. Many-to-Many İlişkiler

Django tarafından aşağıdaki Many-to-Many ilişkiler için ara tablolar otomatik oluşturulur. ER diyagramlarında okunabilirliği artırmak için bu ara tablolar ayrı entity olarak çizilmek yerine doğrudan M2M ilişki olarak gösterilmiştir.

| Model | M2M alanı | Hedef |
|---|---|---|
| `ProductVariant` | `attribute_values` | `AttributeValue` |
| `ProductImageGroup` | `visual_attribute_values` | `AttributeValue` |
| `ProductDraftVariant` | `attribute_values` | `AttributeValue` |
| `ProductDraftImageGroup` | `visual_attribute_values` | `AttributeValue` |

## 9. Önemli Constraint Kuralları

Kodda tanımlanan constraint'lerin tam listesi ilgili model başlıklarının altında verilmiştir. Özellikle aşağıdaki kurallar mimarinin temel veri bütünlüğü kurallarıdır:

| Constraint | İş kuralı |
|---|---|
| `unique_user_title` | Aynı kullanıcı aynı başlıkta birden fazla adres oluşturamaz. |
| `unique_pending_store_update_request` | Aynı mağaza için aynı anda yalnızca bir `PENDING` güncelleme talebi olabilir. |
| `unique_category_brand` | Aynı kategori-marka çifti tekilleştirilir. |
| `unique_store_variant` | Aynı mağaza aynı varyant için yalnızca bir aktif teklif/enventar kaydı taşıyabilir. |
| `unique_store_sku` | Aynı mağaza içerisindeki SKU değerleri tekilleştirilir. |
| `unique_cart_store_product` | Aynı StoreProduct aynı sepette tek satırda bulunur. |
| `unique_store_per_order` | Bir Order içerisinde aynı Store için yalnızca bir SubOrder bulunur. |
| `unique_active_reservation_per_item` | Aynı OrderItem için aynı anda yalnızca bir aktif stok rezervasyonu bulunabilir. |
| `stcard_idmptncy_key` | Aynı kullanıcı/provider/işlem tipi/idempotency key kombinasyonu tekildir. |
| `unique_refund_transaction_item` | Aynı refund içinde aynı PaymentTransactionItem yalnızca bir kez bulunur. |

## 10. ER Diyagramları

Bütün 44 modeli tek bir diyagramda çizmek mümkün olsa da GitHub üzerinde okunabilirliği ciddi şekilde düşürür. Bu nedenle diyagramlar domain bazında ayrılmıştır:

- [Genel veritabanı ilişkileri](diagrams/database-er.md)
- [Kimlik ve mağaza ER diyagramı](diagrams/identity-store-er.md)
- [Katalog ve satıcı teklifleri ER diyagramı](diagrams/catalog-core-er.md)
- [Ürün taslağı, koleksiyon ve soru-cevap ER diyagramı](diagrams/product-engagement-er.md)
- [Sepet ER diyagramı](diagrams/cart-er.md)
- [Sipariş ve fatura ER diyagramı](diagrams/order-er.md)
- [Ödeme, kayıtlı kart ve refund ER diyagramı](diagrams/payment-er.md)

GitHub'da `.md` dosyasını normal görünümde açtığında `mermaid` blokları grafik olarak render edilir. `Raw` görünümünde ise Mermaid kaynak kodunu görürsün.

## 11. Dokümantasyon Sınırı

Bu belge model kodunun mevcut haliyle mantıksal şemayı açıklar. Fiziksel SQL Server execution planları, gerçek tablo boyutları, canlı index fragmentation durumu ve production query planları bu belgeye dahil değildir; bunlar ayrı bir performans/deployment dokümantasyonunun konusudur.