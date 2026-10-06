# Security, Concurrency ve Transaction Mimarisi

## 1. Genel yaklaşım

Proje, güvenliği yalnızca View katmanındaki kullanıcı kontrolüne bırakmayan bir yapı kullanıyor.

Ana yaklaşım:

```text
HTTP Request
    ↓
Authentication / Authorization
    ↓
Ownership kontrolü
    ↓
Service katmanı
    ↓
transaction.atomic()
    ↓
select_for_update() / database constraints
    ↓
MSSQL
```

Bu yapı özellikle para, stok, sipariş, refund ve seller ownership gibi stateful işlemlerde önemlidir.

---

## 2. Kimlik doğrulama

Django'nun session tabanlı authentication yapısı kullanılır.

`AUTH_USER_MODEL`:

```text
accounts.CustomUser
```

Authentication backend listesinde:

```text
accounts.backends.EmailOrPhoneBackend
django.contrib.auth.backends.ModelBackend
```

bulunur.

Şifreler Django'nun password hashing altyapısı üzerinden yönetilir; uygulama kodunda düz metin şifre saklama yaklaşımı kullanılmaz.

Ayrıca Django'nun standart password validators'ları aktif:

- `UserAttributeSimilarityValidator`
- `MinimumLengthValidator`
- `CommonPasswordValidator`
- `NumericPasswordValidator`

---

## 3. Authentication ve Seller Authorization ayrımı

Authentication yalnızca kullanıcının kim olduğunu belirler.

Seller tarafındaki erişim için ayrıca authorization gerekir.

```text
Authenticated User
       ↓
Is seller?
       ↓
SellerRequiredMixin
       ↓
Which Store?
       ↓
StoreOwnerMixin
       ↓
Is requested resource owned by that Store?
```

Bu nedenle:

> Giriş yapmış olmak seller kaynağına erişmek için tek başına yeterli değildir.

---

## 4. IDOR koruması

Proje, URL'den gelen `store_slug`, `draft_id`, `offer_id`, `suborder_number` gibi identifier'ların tek başına güvenilir olmadığını kabul eder.

Örnek ownership zinciri:

```text
URL: /stores/<store_slug>/...
          ↓
StoreOwnerMixin
          ↓
Store.seller == request.user.seller_profile
          ↓
Approved Store
          ↓
View / Service
```

Product Wizard tarafında taslak ayrıca:

```text
ProductDraft.seller = request.user
ProductDraft.store = authorized store
status = DRAFT
```

ile sınırlandırılır.

Order tarafında seller işlemleri `SubOrder` ile mağaza ilişkisi üzerinden ayrıca kontrol edilir.

---

## 5. CSRF

Django'da global olarak:

```text
django.middleware.csrf.CsrfViewMiddleware
```

aktif.

HTML form'larında `{% csrf_token %}` kullanılıyor.

AJAX isteklerinde `X-CSRFToken` header'ı kullanılıyor.

Bu nedenle normal browser/session tabanlı POST işlemleri CSRF koruması altında.

### Bilinçli CSRF istisnaları

İki provider callback endpoint'i `csrf_exempt`:

```text
Iyzico3DSCallbackAPIView
IyzicoPaymentWebhookAPIView
```

Bunun nedeni üçüncü taraf provider'ın uygulamaya Django CSRF token'ı sağlayamamasıdır.

Bu endpoint'ler CSRF token'a güvenmek yerine provider doğrulaması kullanır.

---

## 6. iyzico webhook güvenliği

Webhook endpoint'inde:

```text
X-IYZ-SIGNATURE-V3
```

header'ı doğrulanır.

Ayrıca payload:

- JSON olarak parse edilir
- object olması kontrol edilir
- body boyutu 64 KB ile sınırlandırılır
- `paymentId`
- `paymentConversationId`
- event/status

gibi alanlarla işlenir.

Başarılı doğrulama sonrasında PaymentService tarafından local payment state'i finalize edilir.

Önemli prensip:

> Webhook isteğinin geldiği gerçeği tek başına güvenilir kabul edilmez; kriptografik imza doğrulanmadan ödeme sonucu uygulanmaz.

---

## 7. 3DS callback güvenliği

3DS callback endpoint'i kullanıcı session'ına dayanmaz.

Callback'ten alınan:

- `paymentId`
- `conversationId`
- `conversationData`
- status
- `mdStatus`

verileri doğrulanır.

Ardından provider tarafındaki payment completion akışı çalıştırılır.

Local `PaymentTransaction` ile provider payment ID'si karşılaştırılır.

Yanlış payment ID ile mevcut transaction'ı finalize etmeye çalışmak reddedilir.

---

## 8. Payment state race condition

3DS callback ve webhook aynı ödeme için birbirine yakın zamanda gelebilir.

Bu nedenle local payment finalization sürecinde:

```text
PaymentTransaction
       ↓
select_for_update()
       ↓
Order
       ↓
select_for_update()
```

kullanılır.

Aynı transaction aynı ödeme için iki farklı HTTP request tarafından eş zamanlı işlense bile state transition database lock ile serialize edilir.

Başarılı finalization sonrasında:

```text
PaymentTransaction = SUCCESS
Order = PAID
StockReservation = CONSUMED
```

gibi state değişiklikleri tek transaction sınırları içinde koordine edilir.

---

## 9. Idempotency

Idempotency özellikle stored card operasyonlarında açıkça modellenmiştir.

`StoredCardOperation` üzerinde:

```text
user
provider
operation_type
idempotency_key
request_fingerprint
status
```

alanları bulunur.

Unique constraint sayesinde aynı kullanıcı + provider + operation type + idempotency key kombinasyonu ikinci kez bağımsız operation olarak oluşturulamaz.

---

## 10. Request fingerprint

Aynı `Idempotency-Key` ile farklı bir işlem gönderilmesini engellemek için request fingerprint kullanılır.

Mantık:

```text
Idempotency-Key
        +
request payload
        ↓
request_fingerprint
```

Daha sonra:

```text
aynı key
   +
aynı fingerprint
   → aynı operation

aynı key
   +
farklı fingerprint
   → hata
```

Bu, istemcinin aynı idempotency key'i farklı bir request için yanlışlıkla yeniden kullanmasına karşı ek bir güvenlik katmanıdır.

---

## 11. Stok concurrency

Stok, çoklu kullanıcı ortamında en kritik yarış koşullarından biridir.

Sadece:

```python
if offer.stock >= quantity:
```

kontrolü yeterli değildir.

Çünkü iki paralel request aynı stok değerini okuyabilir.

Proje bunun yerine transaction + row-level locking kullanır.

Temel kilit sırası:

```text
Order
  ↓
StoreProduct
  ↓
StockReservation
```

Bu sıra farklı operasyonlarda tutarlı tutulmaya çalışılır.

---

## 12. `select_for_update()`

`select_for_update()` yalnızca active transaction içinde anlamlıdır.

Projede şu kritik kayıtlar kilitlenir:

- `Order`
- `StoreProduct`
- `StockReservation`
- bazı draft / image group kayıtları
- payment transaction'ları
- refund kayıtları
- stored card operation kayıtları

Amaç:

> Aynı state üzerinde aynı anda çalışan iki isteğin birbirinin ara durumunu ezmesini önlemek.

---

## 13. StockReservation lifecycle

Reservation lifecycle:

```text
              ┌──────────────┐
              │    ACTIVE    │
              └──────┬───────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       CONSUMED   RELEASED    EXPIRED
```

### `CONSUMED`

Ödeme doğrulandıktan sonra stok fiziksel olarak düşülür.

### `RELEASED`

Reservation artık stok bloklamaz.

### `EXPIRED`

Süre dolduğu için reservation geçersiz hale gelir.

Reservation consume/expire/release işlemlerinde ilgili order, store product ve reservation kayıtları lock edilir.

---

## 14. Transaction sınırları

Para ve stok state'i taşıyan işlemlerde `transaction.atomic()` yoğun biçimde kullanılmıştır.

Örnek domain'ler:

```text
Product create / publish
ProductDraft updates
Offer publish
Cart checkout preparation
Order creation
Stock reservation
Shipping
Cancellation
Payment finalization
Refund
StoredCard operations
```

Transaction'ın amacı:

```text
Operation A
   ├── DB write
   ├── DB write
   └── DB write
         ↓
     COMMIT

veya

     ROLLBACK
```

ile yarım state bırakılmasını önlemektir.

---

## 15. Database constraint'ler ile savunma

Uygulama kodundaki validation tek başına yeterli görülmemiştir.

Örnekler:

```text
unique_store_variant
unique_store_sku
unique_default_draft_variant
unique_main_product_image_per_group
unique_question_user_upvote
answer_per_question
```

gibi unique constraint'ler vardır.

Bazı kurallar conditional constraint ile ifade edilir.

Örneğin:

```text
aynı Store için tek PENDING StoreUpdateRequest
```

gibi bir business rule database seviyesinde de korunur.

Bu yaklaşımın amacı:

> İki farklı application process aynı anda aynı validation'ı geçerse bile database'in son savunma hattı olarak çalışmasıdır.

---

## 16. `IntegrityError` kullanımı

Bazı service'lerde database constraint ihlalleri kontrollü biçimde ele alınır.

Ancak önemli bir tasarım kuralı:

> Her `IntegrityError` belirli bir business case'i temsil etmiyor olabilir.

Bu nedenle bir `IntegrityError`'ı hangi constraint'in oluşturduğunu ayırt etmeden "duplicate request" veya "existing operation" kabul etmek güvenilir değildir.

İdempotency gibi kritik işlemlerde önce deterministic lookup, ardından lock ve database constraint tabanlı koruma daha güvenilir bir yaklaşımdır.

Mevcut card storage implementasyonu bu concurrency problemine özel olarak daha ayrıntılı kontrol içerir.

---

## 17. External API ve transaction ayrımı

Database transaction ile üçüncü taraf provider işlemi aynı şey değildir.

Örneğin:

```text
iyzico API call
      ↓
provider response
      ↓
local DB transaction
      ↓
local state finalization
```

yaklaşımı önemlidir.

Çünkü bir SQL transaction'ın rollback olması üçüncü taraf provider'a daha önce gönderilmiş bir HTTP isteğini geri almaz.

Bu yüzden provider sonucunu aldıktan sonra local state'in idempotent ve doğrulanabilir biçimde yazılması gerekir.

---

## 18. Logging

Ödeme tarafında loglamada payment ID, conversation ID, event type ve iyzico reference code gibi operasyonel identifier'lar kullanılır.

Düz kart numarası, CVC/CVV gibi ödeme verilerinin local database'e kaydedilmemesi tasarım prensibidir.

Stored card tarafında local DB'de provider token / metadata yaklaşımı kullanılır; kullanıcının ham kart bilgileri application modelinde tutulmaz.

---

## 19. Proxy ve Client IP

Payment service içinde `X-Forwarded-For` desteği bulunur.

Ancak kodun kendisi de şu önemli varsayımı not eder:

> `X-Forwarded-For` yalnızca güvenilir bir reverse proxy arkasında güvenilir kabul edilmelidir.

Bu nedenle production ortamında proxy/topology doğru yapılandırılmalıdır.

---

## 20. Production hardening notları

Mevcut settings'te güvenlik middleware'leri ve secret'ların environment variable'dan okunması doğru bir temel sağlar.

Ancak production checklist'inde ayrıca şu ayarlar doğrulanmalıdır:

```text
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS
SECURE_HSTS_INCLUDE_SUBDOMAINS
SECURE_HSTS_PRELOAD
```

Bunlar mevcut settings dosyasında açıkça yapılandırılmış değildir; bu nedenle README'de "aktif" özellik olarak yazılmamalıdır.

### MSSQL certificate

Mevcut database configuration:

```text
trust_server_certificate = yes
```

kullanıyor.

Bu ayar geliştirme / kontrollü ortamda bağlantı sorunlarını önleyebilir; production'da sertifika doğrulamasının bilinçli ve güvenli biçimde yapılandırılması gerekir.

### CSRF trusted origin

Settings'te bir ngrok domain'i `CSRF_TRUSTED_ORIGINS` içinde bulunuyor.

Bu tür geçici development tunnel adresleri production configuration'ında tutulmamalı; gerçek deployment domain'ine göre yönetilmelidir.

---

## 21. Güvenlik açısından mevcut güçlü taraflar

Projede aşağıdaki savunma katmanları mevcut:

- Django CSRF middleware
- password validators
- custom authentication backend
- seller authorization mixin'leri
- store ownership kontrolü
- draft ownership kontrolü
- seller order ownership kontrolü
- provider webhook signature verification
- payment ID consistency checks
- idempotency key
- request fingerprint
- database unique constraints
- transaction.atomic()
- select_for_update()
- deterministic locking yaklaşımı
- stock reservation state machine
- stored card için ham kart verisi yerine provider-side token yaklaşımı

---

## 22. Güvenlik açısından açık / iyileştirme alanları

Mevcut kodun dürüst bir değerlendirmesi için aşağıdaki maddeler ayrıca takip edilmelidir:

### Production HTTP security headers

HSTS ve secure cookie ayarları settings'te açıkça yapılandırılmamış.

### Development-specific configuration

ngrok origin ve `trust_server_certificate = yes` production'dan ayrıştırılmalı.

### Seller authorization merkezi hale getirilebilir

Farklı uygulamalarda `SellerRequiredMixin` davranışları farklıdır. Gelecekte authorization politikasının tek bir reusable katmanda standardize edilmesi faydalı olur.

### Test kapsamı

Projedeki kritik domainler için test dosyaları mevcut olsa da test kapsamının business rule'ların tamamını kapsadığı varsayılmamalıdır.

Özellikle şu concurrency senaryoları ayrı test edilmeye değerdir:

- iki paralel checkout
- aynı order için callback + webhook
- aynı stored-card idempotency key
- eş zamanlı seller offer creation
- aynı reservation'ın consume + release yarışması
- aynı store için concurrent pending update request

---

## 23. Genel güvenlik modeli

```text
                 Authentication
                       ↓
                 Authorization
                       ↓
                 Ownership
                       ↓
               Business Rules
                       ↓
                  Transaction
                       ↓
            Row-level Locking
                       ↓
             Database Constraints
                       ↓
                  MSSQL
```

Harici provider tarafında:

```text
iyzico
  ↓
Signature Verification
  ↓
Payload Validation
  ↓
Payment ID / Amount Validation
  ↓
Locked Local Transaction
  ↓
Idempotent Finalization
```

Bu yaklaşım, güvenliği tek bir mekanizma yerine birbirini tamamlayan katmanlar şeklinde kurar.
