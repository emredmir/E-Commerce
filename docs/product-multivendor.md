# Product ve Multi-Vendor Mimari

## 1. Genel Bakış

Product domain'i, platformdaki global ürün kataloğu ile satıcıların bu katalog üzerindeki satış tekliflerini birbirinden ayırır.

Temel ayrım:

```text
Product
  = Platformun ortak/global katalog ürünü

StoreProduct
  = Belirli bir Store'un belirli bir ProductVariant için satış teklifi
```

Bu ayrım sayesinde aynı ürün birçok mağaza tarafından satılabilir; her mağaza kendi fiyatını, stok miktarını, SKU bilgisini ve teklif durumunu ayrı yönetebilir.

Temel veri zinciri:

```text
Category / Brand
        ↓
     Product
        ↓
 ProductVariant
        ↓
 StoreProduct
        ↓
      Store
```

`ProductVariant`, ürünün satılabilir varyasyonunu; `StoreProduct` ise o varyantın belirli bir mağazadaki satış kaydını temsil eder.

---

## 2. Global Katalog ve Satıcı Teklifi Ayrımı

Platformdaki katalog verileri satıcıya ait değildir. `Product`, `ProductVariant`, `Attribute`, `AttributeValue`, görsel grupları ve benzeri kayıtlar ortak katalog katmanını oluşturur.

Satıcıya özgü ticari bilgiler ise `StoreProduct` üzerinde tutulur:

| Bilgi | Model |
|---|---|
| Ürün adı | `Product` |
| Genel açıklama | `Product` |
| Kategori | `Product` |
| Marka | `Product` |
| Varyant özellikleri | `ProductVariant` + `AttributeValue` |
| Mağaza | `StoreProduct.store` |
| Satış fiyatı | `StoreProduct.price` |
| Stok | `StoreProduct.stock` |
| SKU | `StoreProduct.sku` |
| Satış sayısı | `StoreProduct.sold_count` |
| Satıcı notu | `StoreProduct.seller_notes` |
| Teklif durumu | `StoreProduct.status` |

Bu tasarımın temel sonucu şudur: **aynı `ProductVariant` için farklı mağazalarda birden fazla `StoreProduct` bulunabilir.**

---

## 3. Katalog Domain'i

### 3.1 `Category`

Ürünlerin hiyerarşik kategori yapısını temsil eder.

- `parent` alanı ile self-referencing kategori ağacı kurulur.
- `is_active` ile yayındaki kategoriler ayrılır.
- `clean()` içinde kendisini ata olarak seçme ve döngü oluşturma engellenir.
- `save()` sırasında slug otomatik oluşturulur.
- Slug çakışması durumunda benzersiz bir suffix eklenir.

### 3.2 `Brand`

Global marka kataloğunu tutar.

- `name` benzersizdir.
- `slug` benzersizdir.
- Slug otomatik üretilir.

### 3.3 `CategoryBrand`

Bir markanın hangi kategoriyle ilişkilendirilebileceğini tanımlar.

`category + brand` kombinasyonu `unique_category_brand` constraint'i ile benzersizdir.

Model validasyonu, markanın yalnızca alt kategorilere bağlanmasını sağlar.

### 3.4 `BrandRequest`

Satıcının katalogda bulunmayan bir marka için talep göndermesini sağlar.

Durumlar:

```text
pending → approved
pending → rejected
```

Talebi oluşturan satıcı `seller`, inceleyen kullanıcı `reviewed_by` ile tutulur.

---

## 4. Ürün ve Varyant Modeli

### 4.1 `Product`

Global katalogdaki ana üründür.

Başlıca alanlar:

- `name`
- `slug`
- `description`
- `category`
- `brand`
- `status`
- `default_variant`
- `created_by_store`
- `normalized_name`
- `normalized_key`
- `tokens`

`normalized_name`, `normalized_key` ve `tokens`, ürün eşleştirme/search süreçlerinde kullanılmak üzere normalize edilmiş ürün adı verisini taşır.

`created_by_store`, ürünün ilk kez hangi mağazanın katkısıyla kataloğa eklendiğini temsil eder. Bu bilgi mevcut Buy Box stratejisinde owner teklifinin önceliklendirilmesinde de kullanılır.

### 4.2 `ProductVariant`

Bir `Product` altındaki gerçek varyantı temsil eder.

Örneğin:

```text
Product: iPhone 17 Pro Max

Variant A → Renk=Siyah + Depolama=256GB
Variant B → Renk=Siyah + Depolama=512GB
Variant C → Renk=Beyaz + Depolama=256GB
```

`attribute_values` Many-to-Many ilişkisidir.

`attribute_signature` property, varyantın attribute value ID'lerini sıralayarak karşılaştırılabilir bir imza üretir. Bu imza, özellikle duplicate kontrolü ve draft → katalog katkısında kullanılır.

---

## 5. EAV Attribute Yapısı

Ürün varyantlarının esnek olması için klasik sabit kolon yaklaşımı yerine EAV benzeri bir yapı kullanılır.

```text
Attribute
   ↓
AttributeValue
   ↓
ProductVariant
```

Örneğin:

```text
Attribute: Renk
    ├── Siyah
    ├── Beyaz
    └── Mavi

Attribute: Depolama
    ├── 128GB
    ├── 256GB
    └── 512GB
```

`CategoryAttribute` ise bir kategoride hangi özelliklerin kullanılacağını tanımlar.

Ek olarak:

- `is_filterable`: vitrinde filtre olarak kullanılabilir mi?
- `is_required`: bu özellik zorunlu mu?
- `is_variant`: varyant kombinasyonuna dahil mi?
- `is_visual`: görsel grubunun seçiminde etkili mi?
- `allow_custom_values`: satıcının yeni değer eklemesine izin veriliyor mu?

Bu model, farklı ürün kategorilerinin farklı varyant yapıları kullanabilmesini sağlar.

---

## 6. Varyant Görsel Mimarisi

Ürün görselleri doğrudan `ProductVariant` içine gömülmek yerine `ProductImageGroup` üzerinden modellenmiştir.

```text
Product
  ↓
ProductImageGroup
  ↓
ProductImage
```

`ProductImageGroup.visual_attribute_values`, grubun hangi görsel özelliklerle ilişkili olduğunu belirtir.

Örneğin:

```text
Image Group A
    visual values = [Renk=Siyah]

Image Group B
    visual values = [Renk=Beyaz]

Image Group C
    visual values = []
    → ortak görsel grubu
```

Bu yapı, aynı görsel grubunun birden fazla varyantla paylaşılmasını mümkün kılar.

`ProductVariant.get_thumbnail_url` içinde görsel seçiminde tam eşleşme kontrolü yapılır. Böylece örneğin:

```text
Variant: Siyah + 128GB
Group:   Siyah + 256GB
```

yanlışlıkla aynı görsel grubu olarak değerlendirilmez.

---

## 7. Satıcı Teklifi: `StoreProduct`

`StoreProduct`, multi-vendor yapının merkezindeki modeldir.

İlişki:

```text
Store 1 ─────┐
             ├── StoreProduct ── ProductVariant
Store 2 ─────┘
```

Aynı `ProductVariant` birçok mağaza tarafından satılabilir.

### Teklif benzersizliği

```text
(store, variant)
```

`unique_store_variant` constraint'i ile benzersiz tutulur.

Böylece bir mağaza aynı varyant için ikinci bir ayrı `StoreProduct` kaydı oluşturamaz; mevcut teklif güncellenir.

SKU için de mağaza bazında benzersizlik uygulanır:

```text
(store, sku)
```

Bu constraint yalnızca `sku IS NOT NULL` kayıtlar için geçerlidir.

### Satılabilir teklif

`StoreProductQuerySet.purchasable()` şu şartları birlikte arar:

```text
status = ACTIVE
stock > 0
variant.is_active = True
product.status = ACTIVE
store.is_active = True
```

Bu nedenle sadece teklifin `ACTIVE` olması tek başına satın alınabilir olduğu anlamına gelmez.

---

## 8. Teklif Durumları

`StoreProductStatus`:

```text
DRAFT
ACTIVE
OUT_OF_STOCK
ARCHIVED
```

Model `save()` davranışı ile stok ve aktiflik arasında temel bir otomatik durum geçişi uygulanır:

```text
ACTIVE + stock = 0
        ↓
OUT_OF_STOCK

OUT_OF_STOCK + stock > 0
        ↓
ACTIVE
```

Bu, stok sıfıra indiğinde teklifin yanlışlıkla satın alınabilir görünmesini azaltır.

---

## 9. Fiyat Geçmişi

`ProductPriceHistory`, `StoreProduct` fiyat değişikliklerini geçmişe dönük izlemek için kullanılır.

```text
StoreProduct
    │
    └──< ProductPriceHistory
```

`price_history` sıralaması en yeni değişiklikten eskiye doğrudur.

Bu kayıtlar, ürünün global fiyatını değil **belirli mağazanın teklif fiyatını** temsil eder.

---

## 10. Seller Product Wizard

Satıcının yeni ürün süreci `ProductDraft` üzerinden yürür. Draft, global katalog verisinden farklı olarak geçici çalışma alanıdır.

Genel akış:

```text
Seller
  ↓
Step 1: Temel Ürün Bilgileri
  ↓
Duplicate / Catalog Match
  ↓
Step 2: Varyantlar
  ↓
Step 3: Görseller
  ↓
Step 4: Fiyat / Stok / SKU / Barkod
  ↓
Step 5: İnceleme / Yayınlama
  ↓
Existing Product OR New Product
  ↓
StoreProduct
```

### Step 1 — Draft oluşturma

`DraftCreateService.create_or_get_draft()`:

1. Ürün adı normalize edilir.
2. Aynı satıcı + mağaza + kategori + normalized key için aktif draft aranır.
3. Yoksa yeni `ProductDraft` oluşturulur.
4. Yeni draft için katalog eşleşmesi aranır.
5. Eşleşme bulunursa `matched_product` ve `match_status` doldurulur.

Böylece kullanıcı aynı ürün için gereksiz şekilde birden fazla aktif draft oluşturmaz.

---

## 11. Duplicate / Catalog Match

`DuplicateProductService` katalogda mevcut ürünü bulmak için iki aşamalı yaklaşım kullanır.

### Aşama 1 — Exact normalized key

Öncelikle aynı:

```text
category
normalized_key
```

üzerinde eşleşme aranır.

Marka varsa aynı marka önceliklendirilir.

### Aşama 2 — Token benzerliği

Ürün adlarının token kümeleri karşılaştırılır.

Kullanılan mantık Jaccard benzerliğine dayanır:

```text
intersection(tokens)
--------------------
union(tokens)
```

`TOKEN_MATCH_THRESHOLD = 0.75` değerinin üzerinde kalan aday eşleşme olarak kabul edilir.

Marka aynıysa skora ek bonus uygulanır.

> Dokümantasyon notu: Mevcut `duplicate.py` içinde en iyi skor adayı hesaplanırken son dönüşte `best_product` yerine `product` değişkeninin döndürülmesi gereken bir nokta bulunuyor. Bu, dokümantasyon açısından bilerek gizlenmemiş bir mevcut kod notudur ve ileride düzeltilmelidir.

---

## 12. Step 2 — Varyant Oluşturma

`DraftVariantService` taslak varyantlarını oluşturur.

Temel mantık:

```text
Form
  ↓
Attribute / AttributeValue çözümleme
  ↓
attribute_signature
  ↓
duplicate kontrolü
  ↓
ProductDraftVariant
```

Birden fazla attribute grubu seçildiğinde Cartesian product mantığıyla kombinasyonlar da üretilebilir.

Örneğin:

```text
Renk:   Siyah, Beyaz
Beden:  M, L
```

sonucunda:

```text
Siyah + M
Siyah + L
Beyaz + M
Beyaz + L
```

kombinasyonları üretilebilir.

---

## 13. Step 3 — Görseller

Taslak görselleri doğrudan katalog görsellerine yazılmaz.

Geçici yapı:

```text
ProductDraft
    ↓
ProductDraftImageGroup
    ↓
ProductDraftImage
```

Yayınlama sırasında gerekli görsel grupları gerçek katalog modellerine aktarılır.

İlk görselin kapak olarak seçilmesi ve bir grup içinde tek `is_main=True` görsel bulunması model seviyesinde korunur.

Taslak görsellerinde `file_hash` de tutulur. Böylece aynı grubun içine aynı fiziksel dosyanın tekrar eklenmesi engellenebilir.

---

## 14. Step 4 — Ticari Teklif Bilgileri

Step 4'te taslak varyantlarına:

- fiyat
- stok
- SKU
- barkod
- varsayılan varyant bilgisi

girilir.

Bu aşamada henüz müşterinin satın aldığı gerçek `StoreProduct` kayıtlarına geçilmez; veriler `ProductDraftVariant` üzerinde tutulur.

---

## 15. Mevcut Katalog Ürününe Teklif Ekleme

Draft mevcut bir katalog ürününe eşleşmişse satıcı aynı ürünü yeniden oluşturmak yerine katalogdaki ürüne katkıda bulunabilir.

```text
ProductDraft
    ↓
matched_product
    ↓
OfferCreateService
    ↓
ProductDraftVariant
    ↓
OfferPublishService
    ↓
StoreProduct
```

Buradaki temel amaç, katalogda zaten bulunan bir ürünü tekrar `Product` olarak oluşturmak yerine satıcının o ürüne yeni bir satış teklifi eklemesidir.

---

## 16. Katalogda Olmayan Yeni Varyant

`OfferCustomVariantService`, eşleşen ürünün katalogunda olmayan yeni bir varyantın draft aşamasında oluşturulmasına izin verir.

Akış:

```text
Attribute verisi
   ↓
AttributeValue çözümleme
   ↓
Catalog duplicate kontrolü
   ↓
Draft duplicate kontrolü
   ↓
ProductDraftVariant
   ↓
ProductDraftImageGroup
```

Bu servis **doğrudan `ProductVariant` oluşturmaz**. Gerçek katalog varyantı yayınlama sürecinde `VariantContributionService` tarafından oluşturulur.

---

## 17. Yayınlama

`DraftPublishService` yayınlama sırasında draft verisini gerçek katalog kayıtlarına dönüştürür.

Temel işlem:

```text
ProductDraft
   ↓
Review validation
   ↓
Duplicate check
   ↓
transaction.atomic()
   ↓
Product
   ↓
ProductVariant
   ↓
StoreProduct
   ↓
ProductImageGroup / ProductImage
   ↓
Draft = PUBLISHED
   ↓
Search indexing callback
```

### Yeni ürün durumunda

`_publish_new_product()`:

1. `Product` oluşturur.
2. Aktif `ProductDraftVariant` kayıtlarını dolaşır.
3. Gerçek `ProductVariant` kayıtlarını oluşturur.
4. Attribute value ilişkilerini taşır.
5. Her varyant için `StoreProduct` oluşturur/günceller.
6. Varsayılan varyantı `Product.default_variant` alanına bağlar.

### Mevcut ürün durumunda

`_merge_into_existing_product()` ile draft mevcut katalog ürünüyle birleştirilir.

Aynı attribute kombinasyonu zaten varsa mevcut `ProductVariant` kullanılır.

Yoksa yeni `ProductVariant` oluşturulur.

Her iki durumda da mağaza için `StoreProduct` kaydı oluşturulur veya güncellenir.

---

## 18. Variant Contribution Mantığı

`VariantContributionService.get_or_create_variant()` aynı attribute kombinasyonuna göre katalogdaki varyantı bulur.

```text
attribute_signature
       ↓
existing ProductVariant?
   ┌───────┴───────┐
  Evet            Hayır
   │                │
   ▼                ▼
Reuse          Create Variant
                  ↓
              Copy Images
```

Böylece aynı ürün içinde aynı varyant kombinasyonu tekrar tekrar oluşturulmaz.

---

## 19. Buy Box Mimarisi

Buy Box işlemlerinin ana servisi `StorefrontOfferService`'dir.

Servis yalnızca `purchasable()` tekliflerini dikkate alır.

Mevcut strateji:

```python
BUYBOX_STRATEGY = "first_created"
```

### Mevcut varsayılan davranış

Seçili varyant için:

1. Satın alınabilir teklifler bulunur.
2. Ürünü oluşturan mağazanın (`Product.created_by_store`) teklifi varsa owner teklif önceliklidir.
3. Owner teklifi yoksa `created_at` sırasındaki ilk teklif varsayılan Buy Box olur.

Dolayısıyla mevcut sistemin gerçek davranışı **"en ucuz teklif her zaman Buy Box olur" değildir.**

Ayrıca servis içinde `lowest_price` stratejisi de desteklenir. Bu strateji kullanıldığında en düşük fiyatlı teklif seçilir.

---

## 20. Cheapest Offer ve Buy Box Ayrımı

Ürün detayında iki kavram ayrı tutulur:

```text
cheapest_offer
    = fiyat olarak en ucuz satın alınabilir teklif

buy_box / default_buybox
    = sistem stratejisine göre ana teklif
```

Bu ayrım önemlidir. `first_created` stratejisinde owner teklif Buy Box olabilirken başka bir mağazanın teklifi daha ucuz olabilir.

Müşteri farklı bir satıcı seçerse:

```text
default_buybox
       ↓
user selected offer
       ↓
active_offer
```

oluşturulur ve `is_buybox_overridden=True` ile UI'a bildirilir.

---

## 21. Liste Sayfasında Buy Box

`StorefrontOfferService` ürün listelemelerinde N+1 oluşturmadan Buy Box verisini `Subquery` ile hesaplayabilir.

Örneğin:

```text
Product
   ↓
Subquery
   ↓
StoreProduct
   ↓
buybox_price
```

Ayrıca Buy Box mağazasının ID'si ve ilgili varyant ID'si de Subquery üzerinden alınabilir.

Bu yaklaşım, her ürün için Python'da ayrı offer sorgusu çalıştırmak yerine verinin database seviyesinde annotate edilmesine yardımcı olur.

---

## 22. Product Detail Akışı

`ProductDetailService.get_page_data()` şu sırayla çalışır:

```text
Product
  ↓
build_variant_selection_context()
  ↓
selected_variant
  ↓
Buy Box / offers
  ↓
variant-specific images
  ↓
variant options
  ↓
page context
```

Seçili varyantın belirlenmesinde öncelik sırası:

1. URL'den gelen aktif varyant
2. `Product.default_variant`
3. Satın alınabilir en uygun varyant
4. İlk aktif varyant

---

## 23. Product Listing

`ProductListView` müşteri vitrini için kullanılabilir ürünleri listeler.

Filtreleme/sıralama katmanında:

- kategori
- marka
- fiyat aralığı
- manuel min/max fiyat
- renk
- sıralama

gibi seçenekler bulunur.

Bir ürünün satılabilir kabul edilmesi için ürün, varyant, teklif ve mağaza aktiflik koşulları birlikte değerlendirilir.

Liste ekranında Buy Box fiyatı Subquery üzerinden annotate edilerek fiyat filtrelerinde kullanılabilir.

---

## 24. Ürün Soru-Cevap ve Koleksiyonlar

Product domain yalnızca katalog ve satış teklifiyle sınırlı değildir.

### Koleksiyonlar

```text
ProductCollection
    ↓
ProductCollectionItem
    ↓
ProductVariant
    └── StoreProduct (opsiyonel)
```

Koleksiyon doğrudan bir satıcı teklifini değil, temel olarak varyantı favoriler/istek listesi içinde tutar. Eklenen teklif ayrıca referans olarak saklanabilir.

### Soru-Cevap

```text
ProductQuestion
    ↓
ProductAnswer

ProductQuestion
    ↓
ProductQuestionUpvote
```

Bir soru belirli bir `Store` hedefine yöneltilebilir veya genel soru olabilir.

Soru cevaplanmış kabulünü ayrı bir `is_answered` alanında senkronize etmek yerine, görünür bir `ProductAnswer` bulunmasına göre değerlendiren bir tasarım kullanılmıştır.

---

## 25. Mimari Sorumluluk Dağılımı

Product domain'de iş mantığı view'lara yığılmamıştır.

```text
View
  ↓
Service
  ↓
Model / ORM
  ↓
Database
```

Öne çıkan servisler:

| Service | Sorumluluk |
|---|---|
| `DraftCreateService` | Draft oluşturma / mevcut draft'ı bulma |
| `DraftUpdateService` | Draft temel bilgilerinin güncellenmesi |
| `DuplicateProductService` | Katalog eşleştirme |
| `DraftVariantService` | Taslak varyant yönetimi |
| `DraftImageService` | Draft görsel işlemleri |
| `OfferCreateService` | Draft teklif verilerinin hazırlanması |
| `OfferCustomVariantService` | Katalog dışı varyantın draft'a eklenmesi |
| `OfferPublishService` | Eşleşmiş ürüne StoreProduct yayınlama |
| `VariantContributionService` | Gerçek katalog varyantını bulma/oluşturma |
| `DraftPublishService` | Draft'ın gerçek katalog kayıtlarına dönüştürülmesi |
| `ProductDetailService` | Ürün detay sayfası business logic'i |
| `StorefrontOfferService` | Satılabilir teklifler ve Buy Box |
| `SearchIndexingService` | Yayınlama sonrası arama indeksleme entegrasyonu |

---

## 26. Transaction ve Veri Tutarlılığı

Ürün yayınlama ve teklif yayınlama gibi çok adımlı işlemlerde `transaction.atomic()` kullanılır.

Özellikle:

```text
Product
+ ProductVariant
+ StoreProduct
+ Draft finalization
```

işlemleri mümkün olduğunca aynı transaction sınırında tutulur.

Barkod gibi benzersiz değerlerde yalnızca önceden yapılan `exists()` kontrolüne güvenilmez; database-level `IntegrityError` durumları da ele alınır.

Bu iki katman birlikte kullanılır:

```text
Application validation
        +
Database constraint
```

---

## 27. Performans Yaklaşımı

Product domain içinde veri erişimi için özellikle şu teknikler kullanılır:

- `select_related()`
- `prefetch_related()`
- `Prefetch(..., to_attr=...)`
- `Subquery`
- `Exists`
- uygun index'ler
- database seviyesinde constraint'ler

Özellikle product detail tarafında varyant, attribute, görsel grubu ve görseller tek tek sorgulanmak yerine önceden prefetch edilerek N+1 riski azaltılır.

Product list tarafında Buy Box verisinin Subquery ile alınması da aynı performans yaklaşımının parçasıdır.

---

## 28. Ürün Domain'inin Temel Tasarım Kararları

### Global catalog + seller offer ayrımı

Aynı ürünün farklı satıcılar tarafından satılabilmesi için katalog ve ticari teklif birbirinden ayrılmıştır.

### Variant seviyesinde satış

Teklif `Product` yerine `ProductVariant` üzerinden tutulur. Böylece renk/beden/depolama gibi farklı varyantlar ayrı stok ve fiyat yönetimine sahip olur.

### Draft-first publishing

Satıcının çok adımlı ürün oluşturma süreci doğrudan canlı katalog tablolarını değiştirmek yerine draft alanında ilerler.

### Attribute signature

Varyant kombinasyonlarının karşılaştırılmasını kolaylaştırmak için attribute ID'lerinden deterministic signature üretilir.

### Strategy-driven Buy Box

Buy Box seçimi tek bir hard-coded sorguya gömülmez. `BUYBOX_STRATEGY` üzerinden farklı stratejilere geçiş için servis seviyesinde bir soyutlama bulunur.

### Onaylı / eşleşmiş katalog kullanımı

Satıcı mevcut ürünle eşleştiğinde yeniden global ürün oluşturmaya zorlanmaz; aynı katalog ürününe teklif katkısı yapabilir.

