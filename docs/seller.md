# Seller Architecture

## 1. Genel Bakış

Projede satıcı kavramı tek bir uygulama içinde izole edilmemiştir. Seller domain'i birden fazla Django uygulamasına dağıtılmış durumdadır:

```text
accounts
   └── SellerProfile
          │
          ▼
store
   └── Store
          │
          ├── products
          │      └── StoreProduct / ProductDraft / Offer
          │
          └── orders
                 └── SubOrder / OrderItem
```

Bu yaklaşım, satıcının yalnızca "mağaza sahibi" olarak değil; ürün kataloğuna katkı veren, teklif yayınlayan, sipariş işleyen ve ödeme altyapısına bağlı bir aktör olarak ele alınmasını sağlar.

---

## 2. Seller Domain Bileşenleri

### `CustomUser`

Kullanıcının temel kimlik kaydıdır. Seller tarafında özellikle:

- `is_seller`
- kullanıcı kimliği
- email / telefon

bilgileri kullanılır.

`is_seller`, seller erişiminin üst seviyedeki göstergelerinden biridir.

### `SellerProfile`

`CustomUser` ile `OneToOne` ilişkilidir.

Satıcıya ait:

- seller type
- şirket / resmi unvan
- adres
- telefon
- vergi bilgileri
- kimlik bilgisi
- IBAN
- platform onay durumu
- iyzico submerchant bilgileri
- onboarding durumu

gibi bilgileri taşır.

### `SellerProfileUpdateRequest`

Onaylanmış bir seller profilinin doğrudan ezilmesini önlemek için değişiklikleri ayrı bir talep kaydında tutar.

```text
SellerProfile
      │
      └── SellerProfileUpdateRequest
              ├── new_company_name
              ├── new_company_address
              ├── new_company_phone
              ├── new_iban
              └── status
```

Mevcut uygulamada satıcı profili onaylandıktan sonra yapılan şirket/IBAN değişiklikleri önce bu talep kaydına yazılır.

---

## 3. Satıcı Başvuru Akışı

Başlangıç akışı:

```text
Giriş yapmış kullanıcı
        ↓
BecomeASellerView
        ↓
BecomeASellerForm
        ↓
SellerProfile
        ↓
is_approved = False
        ↓
Admin incelemesi
```

Henüz onaylanmamış bir profil için form üzerinden yapılan değişiklikler mevcut `SellerProfile` üzerinde güncellenebilir.

Onaylanmış bir profil için ise:

```text
Form
 ↓
SellerProfileUpdateRequest
 ↓
Admin approval
 ↓
SellerProfile'a uygula
```

modeli kullanılır.

---

## 4. Platform Satıcı Onayı

Satıcı onayı `SellerApprovalService` üzerinden yürütülebilir.

Temel akış:

```text
SellerProfile
      ↓
SellerApprovalService.approve()
      ↓
SellerProfile.is_approved = True
      ↓
IyzicoSubmerchantService.create()
      ↓
iyzico onboarding / submerchant
```

`SellerApprovalService`, HTTP response veya template üretmek yerine satıcı onay domain'ini yönetir.

Django Admin'deki `SellerProfileAdmin` bu service'i operasyonel giriş noktası olarak kullanır.

### Platform onayı ve iyzico onboarding ayrımı

Satıcının platform tarafından onaylanması ile iyzico onboarding durumu aynı kavram değildir.

```text
Platform approval
    +
iyzico onboarding
```

birbiriyle ilişkili iki farklı durumdur.

`SellerProfile` üzerinde ayrıca:

- `iyzico_onboarding_status`
- `iyzico_onboarding_started_at`
- `iyzico_onboarded_at`
- `iyzico_last_sync_at`
- `iyzico_last_error_code`
- `iyzico_last_error_message`

alanları bulunur.

---

## 5. `is_seller` Senkronizasyonu

`accounts/signals.py` içindeki `post_save` sinyali, `SellerProfile.is_approved` ile `CustomUser.is_seller` alanını senkronlar.

```text
SellerProfile.is_approved = True
            ↓
      post_save signal
            ↓
CustomUser.is_seller = True
```

Tersi durumda:

```text
SellerProfile.is_approved = False
            ↓
CustomUser.is_seller = False
```

Bu nedenle seller authorization tarafında iki farklı seviyeden söz edilir:

1. Seller profilinin varlığı
2. Onaylı seller olma durumu

### Mevcut kod notu

`store/views.py` içinde tanımlanan `SellerRequiredMixin`, authenticated kullanıcıda `seller_profile` bulunmasını kontrol eder; `products/mixins.py` içindeki `SellerRequiredMixin` ise `request.user.is_seller` kontrolü yapar.

Bu nedenle seller erişim kuralı uygulama genelinde tam olarak tek bir mixin ile merkezi değildir.

Bu davranış dokümantasyonda mevcut kod olarak belirtilmektedir; sistemin niyet edilen iş kuralı ayrıca gözden geçirilebilir.

---

## 6. Store Domain

Satıcı bir veya daha fazla `Store` sahibi olabilir.

İlişki:

```text
SellerProfile
     │
     └── 1:N ── Store
```

### Mağaza limitleri

Mevcut `Store.clean()` ve `StoreCreateView` kontrollerine göre:

- arşivlenenler dahil toplam mağaza sayısı en fazla 5
- aynı anda arşivlenmemiş mağaza sayısı en fazla 3

olacak şekilde sınır uygulanır.

---

## 7. Store Durumları

`StoreStatus`:

```text
PENDING
APPROVED
REJECTED
SUSPENDED
ARCHIVED
```

Genel olarak:

```text
PENDING
  │
  ├── APPROVED
  │      │
  │      ├── SUSPENDED
  │      │
  │      └── ARCHIVED
  │
  └── REJECTED
          │
          └── PENDING
```

durumları kullanılır.

`Store.save()` içinde:

```text
status == APPROVED
        ↓
is_active = True
```

ilişkisi korunur.

Onay ilk kez gerçekleştiğinde `approved_at` set edilir.

---

## 8. Mağaza Güncelleme Talebi

Onaylı mağazanın mevcut bilgileri doğrudan değiştirilmez. Bunun yerine:

```text
Approved Store
      ↓
StoreUpdateRequest
      ↓
Admin approval
      ↓
Store'a uygula
```

kullanılır.

`StoreUpdateRequest` içinde:

- `new_store_name`
- `new_logo`
- `new_banner`
- `new_contact_email`
- `new_contact_phone`
- `new_address`
- `status`
- `created_at`

bulunur.

Ayrıca veritabanında aynı mağaza için aynı anda yalnızca bir `PENDING` update request bulunmasına izin veren conditional unique constraint vardır.

---

## 9. Store Ownership

Satıcının kendi mağazası dışındaki mağazalara erişmesini engelleyen temel mekanizma `StoreOwnerMixin`'dir.

Kontrol mantığı:

```text
URL store_slug
      ↓
Store
      ↓
seller = request.user.seller_profile
      ↓
status = APPROVED
```

Eşleşme yoksa kullanıcı mağaza listesine yönlendirilir.

Bu kontrol özellikle seller tarafındaki:

- inventory
- offer
- product wizard
- order panel
- order detail
- order status
- order cancellation

işlemlerinin sınırlandırılmasında kullanılır.

---

## 10. Seller Product Operations

Seller'ın ürün tarafındaki operasyonları `products` uygulamasındadır.

Başlıca seller işlemleri:

- Product Wizard
- mevcut katalog ürünüyle eşleşme
- varyant oluşturma
- ürün görselleri
- teklif oluşturma
- özel varyant oluşturma
- teklif yayınlama
- inventory listeleme
- teklif güncelleme
- teklif arşivleme

Bu işlemlerin önemli kısmında:

```text
SellerRequiredMixin
        +
StoreOwnerMixin
```

kullanılır.

Dolayısıyla seller, doğrudan başka bir mağazanın ürün/offer kaydını URL üzerinden hedefleyemez.

---

## 11. Seller Offer Modeli

Global katalog ürünü ile seller'a ait satış teklifi farklı kavramlardır.

```text
Product
   ↓
ProductVariant
   ↓
StoreProduct
   ↓
Store
```

Burada `StoreProduct` belirli bir seller mağazasının belirli bir varyant için satış teklifidir.

Seller tarafında şu bilgiler güncellenebilir:

- fiyat
- stok
- SKU
- durum
- teklif ile ilişkili diğer satış bilgileri

Bu nedenle aynı katalog ürünü üzerinde birden fazla mağazanın ayrı `StoreProduct` kaydı bulunabilir.

---

## 12. Seller Inventory

`StoreProductListView` yalnızca mevcut mağazaya ait teklifleri getirir:

```text
StoreProduct.objects.filter(
    store=store
)
```

ve product / variant / category / brand / attribute / image ilişkilerini `select_related()` ve `prefetch_related()` ile optimize eder.

Seller panelinde ayrıca:

- status filtering
- text search
- pagination
- draft ürünlerin gösterimi

bulunur.

---

## 13. Seller Questions & Answers

Seller, kendi mağazasına yöneltilen soruların yanında tüm satıcılara açık global soruları da görebilir.

Kural:

```text
target_store = Store A
    → yalnızca Store A cevaplayabilir

target_store = NULL
    → tüm uygun satıcılar cevaplayabilir
```

`StoreQuestionsListView` bu görünürlüğü queryset seviyesinde filtreler.

Cevap oluşturma işlemi `ProductQAService.create_answer()` üzerinden gerçekleştirilir.

---

## 14. Seller Order Management

Multi-vendor mimaride seller doğrudan ana `Order`'ı yönetmez.

Seller için temel çalışma birimi:

```text
SubOrder
```

şeklindedir.

```text
Customer
   ↓
Order
   ├── SubOrder (Store A) ← Seller A
   └── SubOrder (Store B) ← Seller B
```

Seller A yalnızca Store A'ya ait `SubOrder` kaydını görebilir.

Bu nedenle seller order panelinde `SubOrder` temel model olarak kullanılır.

---

## 15. Seller Order Operations

Satıcı panelinde mevcut işlemler:

- sipariş listesi
- durum filtresi
- sipariş/müşteri/ürün araması
- sipariş detayını görüntüleme
- `PREPARING` durumuna geçiş
- kargo bilgisi girme
- `SHIPPED` durumuna geçiş
- `DELIVERED` durumuna geçiş
- uygun durumlarda iptal
- invoice görüntüleme

### Status işlemleri

```text
PENDING
  ↓
PREPARING
  ↓
SHIPPED
  ↓
DELIVERED
```

İptal uygun durumda:

```text
PENDING / PREPARING
        ↓
     CANCELLED
```

şeklindedir.

Durum değişiklikleri service katmanına aktarılır:

```text
OrderService
ShippingService
CancellationService
```

---

## 16. Seller Order Authorization

Seller sipariş detayına erişirken iki katman vardır:

```text
SellerRequiredMixin
        ↓
StoreOwnerMixin
        ↓
OrderService.get_suborder_for_store()
        ↓
SubOrder
```

Bu yaklaşım seller'ın:

```text
/store-A/orders/SUB-...
```

üzerinden:

```text
/store-B/orders/SUB-...
```

gibi başka bir mağazaya ait siparişi görmesini engeller.

Bu kontrol yalnızca URL'deki `store_slug` değerine güvenmez; `SubOrder` ile mağaza ilişkisini service seviyesinde de sınırlar.

---

## 17. Seller Cancellation / Refund

Seller iptal akışı:

```text
Seller
  ↓
StoreOrderCancellationView
  ↓
OrderService.get_suborder_for_store()
  ↓
CancellationService.cancel_suborder()
  ↓
SubOrder = CANCELLED
  ↓
PaymentRefund
  ↓
RefundService.process_refund()
```

Böylece:

- seller authorization
- cancellation business logic
- provider refund

farklı katmanlarda tutulur.

Refund işlemi başarısız olursa iptal ile refund sonucu birbirinden ayrı yönetilebilir.

---

## 18. Shipping

Seller'ın kargo işlemleri `ShippingService` ile yönetilir.

```text
PREPARING
   ↓
cargo company + tracking number
   ↓
SHIPPED
   ↓
delivered_at
   ↓
DELIVERED
```

`ShippingService`:

- seller authorization yapmaz
- HTTP response üretmez
- provider kargo API'si çağırmaz

Yalnızca domain operasyonlarını yürütür.

---

## 19. Django Admin'in Seller Domainindeki Rolü

Admin paneli seller tarafında önemli bir operasyon alanıdır.

### SellerProfileAdmin

- seller listesi
- approval
- iyzico onboarding başlatılması
- onboarding durumunun görüntülenmesi
- arama ve filtreleme

### StoreAdmin

- mağaza onayı
- mağazayı askıya alma
- mağaza reddetme
- mağazayı aktive etme
- mağazayı pasifleştirme

### StoreUpdateRequestAdmin

- değişiklik talebini onaylama
- değişiklik talebini reddetme
- onaylanan değişiklikleri `Store` üzerine uygulama

Bu nedenle seller lifecycle yalnızca müşteri-facing view'lardan oluşmaz; admin operasyonları da sistemin bir parçasıdır.

---

## 20. Seller ve Platform Arasındaki Sorumluluk Sınırı

### Seller tarafı

- profil başvurusu
- mağaza oluşturma
- mağaza bilgileri
- ürün wizard
- katalog katkısı
- teklif ve stok yönetimi
- soru cevaplama
- sipariş işleme
- kargo / teslimat
- uygun siparişleri iptal etme

### Platform / Admin tarafı

- seller approval
- iyzico onboarding operasyonu
- store approval
- store suspension / rejection
- store update request approval
- seller profile update request approval

Bu ayrım, multi-vendor platform ile merchant operasyonlarının birbirinden ayrılmasını sağlar.

---

## 21. Mevcut Sistem ve Gelecek Aşamalar

Mevcut kodda seller tarafında:

- seller approval
- store management
- product / offer management
- inventory
- order management
- cancellation
- shipping
- refund bağlantısı

bulunmaktadır.

Seller settlement / payout domain'i ise ayrı bir sonraki aşama olarak ele alınmalıdır.

Bu nedenle dokümantasyonda henüz uygulanmamış settlement davranışları mevcut sistemin parçasıymış gibi gösterilmemelidir.

---

## 22. Mimari Özet

Seller domain'inin genel akışı:

```text
CustomUser
    ↓
SellerProfile
    ↓
Platform Approval
    ↓
is_seller
    ↓
Store
    ↓
Product / ProductVariant
    ↓
StoreProduct
    ↓
Customer Order
    ↓
SubOrder
    ↓
Shipping / Cancellation / Refund
```

Yetkilendirme sınırı ise:

```text
Seller identity
    ↓
SellerRequiredMixin
    ↓
Store ownership
    ↓
StoreOwnerMixin
    ↓
Domain Service authorization
```

şeklindedir.
