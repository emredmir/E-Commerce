# Payment / iyzico Mimarisi

## 1. Genel Bakış

Ödeme domain'i, sipariş oluşturma işlemini doğrudan ödeme sağlayıcısına bağımlı hale getirmemek için birkaç ayrı sorumluluğa bölünmüştür.

Temel parçalar:

- `PaymentService`: Order için iyzico ödeme başlatma, 3DS completion, webhook doğrulama ve başarılı ödemenin local finalizasyonu.
- `CardStorageService`: iyzico Card Storage ile kayıtlı kart oluşturma, listeleme, senkronizasyon, silme ve varsayılan kart yönetimi.
- `RefundService`: `PaymentRefund` içindeki provider-level refund item'larını güvenli ve tekrar çalıştırılabilir şekilde işler.
- `IyzicoSubmerchantService`: seller için iyzico Marketplace submerchant onboarding ve reconciliation işlemleri.

Bu servisler HTTP response üretmek yerine domain sonucu veya domain exception üretir. HTTP katmanındaki view'lar bu sonuçları API response'una dönüştürür.

---

## 2. Temel Tasarım İlkesi: Provider Çağrısı Transaction İçinde Değil

Ödeme mimarisindeki en önemli kurallardan biri, iyzico HTTP çağrılarının `transaction.atomic()` bloğu içinde yapılmamasıdır.

Genel desen:

```text
Kısa local DB transaction
        ↓
Provider HTTP çağrısı
        ↓
Kısa local DB transaction
```

Bunun amacı uzun süre database lock tutmamak ve ağ gecikmesini veritabanı transaction'ına bağlamamaktır.

Bu özellikle:

- 3DS initialize
- 3DS completion
- webhook'taki provider retrieve
- stored card create/delete
- refund çağrıları
- submerchant onboarding

işlemlerinde kullanılır.

---

## 3. PaymentTransaction Neden Ayrı Bir Model?

`Order` siparişin yaşam döngüsünü, `PaymentTransaction` ise belirli bir ödeme denemesini temsil eder.

Bir Order altında birden fazla ödeme denemesi bulunabilir:

```text
Order
 ├── PaymentTransaction #1 → FAILED
 ├── PaymentTransaction #2 → FAILED
 └── PaymentTransaction #3 → SUCCESS
```

Bu ayrım sayesinde başarısız bir ödeme denemesi siparişin tamamının geçmişini kaybetmeden yeni bir ödeme denemesinin oluşturulmasına izin verir.

`PaymentStatus` değerleri:

- `INITIATED`
- `PENDING`
- `SUCCESS`
- `FAILED`
- `REFUNDED`
- `PARTIALLY_REFUNDED`

Ödeme denemesinin durumu ile `OrderStatus` birbirinden bağımsız tutulur.

---

## 4. 3DS Ödeme Başlatma

Endpoint:

```text
POST /orders/checkout/<order_number>/payment/
```

`CheckoutPaymentAPIView` request'i normalize eder ve `PaymentService.initialize_3ds()` çağırır.

İki ödeme yöntemi vardır:

### Yeni kart

```text
payment_method = new_card
        ↓
PaymentCardData
        ↓
iyzico 3DS initialize
```

### Kayıtlı kart

```text
payment_method = stored_card
        ↓
StoredCard
        ↓
cardUserKey + cardToken
        ↓
iyzico 3DS initialize
```

Yeni kart bilgileri `PaymentCardData` DTO'sunda tutulur ve servis tarafından provider'a gönderilir; PAN/CVC local database'e yazılmaz.

---

## 5. Payment Initialization Transaction Akışı

`PaymentService._prepare_payment()` local sipariş durumunu ve aktif stok rezervasyonlarını kontrol eder, ödeme için `PaymentTransaction(INITIATED)` oluşturur ve provider request payload'ını hazırlar.

Ardından iyzico çağrısı transaction dışında yapılır.

Başarılı initialize cevabında:

```text
iyzico response
   ↓
paymentId
conversationId
threeDSHtmlContent
signature
   ↓
local signature validation
   ↓
PaymentTransaction = PENDING
```

`threeDSHtmlContent` frontend'in 3DS ekranını başlatabilmesi için response ile geri gönderilir.

---

## 6. 3DS Initialize Signature Doğrulaması

Kod, iyzico initialize response'undaki imzayı localde tekrar hesaplar.

Mesaj bileşenleri:

```text
paymentId
conversationId
```

Hesaplanan HMAC ile provider'dan gelen signature karşılaştırılır. Eşleşmiyorsa ödeme başlatılmış olarak kabul edilmez ve `PaymentVerificationError` üretilir.

---

## 7. 3DS Callback ve Completion

Endpoint:

```text
POST /payments/iyzico/3ds/callback/
```

Callback'te en azından:

- `paymentId`
- `conversationId`
- `conversationData`
- `status`
- `mdStatus`

kontrol edilir.

3DS başarısızsa ilgili `PaymentTransaction` `FAILED` yapılır.

Başarılıysa:

```text
Iyzico3DSCallbackAPIView
        ↓
PaymentService.complete_3ds()
        ↓
local PaymentTransaction lookup + lock
        ↓
iyzico ThreedsPayment.create()
        ↓
response validation
        ↓
completion signature validation
        ↓
_finalize_successful_payment()
```

---

## 8. Completion Validation

Başarılı ödeme yalnızca provider response'unda `status=success` görülmesiyle kabul edilmez.

Kod ayrıca aşağıdaki değerleri local beklenti ile karşılaştırır:

- `paymentId`
- `conversationId`
- `basketId`
- `paidPrice`
- `price`
- `currency`
- payment item kırılımı

İyzico response'undaki `itemTransactions` da local `OrderItem` / `SubOrder` yapısıyla eşleştirilerek `PaymentTransactionItem` kayıtlarına dönüştürülür.

Bu katman özellikle refund işlemlerinde gereklidir; çünkü marketplace refund çağrıları provider-level `paymentTransactionId` üzerinden yürütülür.

---

## 9. Başarılı Ödemenin Ortak Finalizasyonu

3DS completion ve webhook SUCCESS farklı giriş yolları olsa da ikisi aynı local finalizasyon mekanizmasını kullanır: `_finalize_successful_payment()`.

Tek bir database transaction içinde ana adımlar:

```text
PaymentTransaction lock
        ↓
Order lock
        ↓
amount / currency validation
        ↓
PaymentTransactionItem persistence
        ↓
PaymentTransaction = SUCCESS
        ↓
StockReservationService.consume_order()
        ↓
Order = PAID
        ↓
CartService.clear_order_items()
```

Bu ortak mekanizma iki farklı notification path'in aynı başarılı ödeme sonucunu iki kez uygulamasını engellemeye yardımcı olur.

---

## 10. Ödeme İdempotency

`PaymentService` başarılı bir `PaymentTransaction` tekrar finalize edilmeye çalışılırsa mevcut başarılı sonucu döndürebilir.

Özellikle:

- tekrar gelen 3DS callback
- webhook + callback yarışması
- aynı webhook'un tekrar gönderilmesi

gibi durumlar için `SUCCESS` state'i terminal/idempotent bir durum olarak kullanılır.

`PaymentTransaction` tarafında database seviyesinde ayrıca:

```text
(provider, payment_id)
(provider, conversation_id)
```

benzersizlik kısıtları bulunur.

---

## 11. Webhook Mimarisi

Endpoint:

```text
POST /orders/payment/webhook/iyzico/
```

Webhook endpoint'i browser/session authentication gerektirmez ve CSRF token kullanmaz. Güvenlik, provider signature doğrulaması ile sağlanır.

Webhook katmanı:

1. body boyutunu sınırlar (`64 KiB`).
2. JSON body'yi parse eder.
3. `X-IYZ-SIGNATURE-V3` header'ını alır.
4. `iyziEventType`, `paymentId`, `paymentConversationId`, `status` alanlarını doğrular.
5. webhook signature'ını doğrular.
6. yapılandırılmış `merchantId` varsa ek olarak doğrular.
7. local `PaymentTransaction` kaydını `conversation_id` ile bulur.
8. SUCCESS durumunda provider `payment/detail` retrieve çağrısı yapar.
9. remote sonucu validate eder ve ortak finalizasyon servisini çağırır.

Provider çağrısı transaction dışında gerçekleştirilir.

---

## 12. Webhook Signature

`X-IYZ-SIGNATURE-V3` için kodda HMAC-SHA256 kullanılır.

Mesaj:

```text
secretKey
+ iyziEventType
+ paymentId
+ paymentConversationId
+ status
```

İmzalar `hmac.compare_digest()` ile karşılaştırılır.

Bu doğrulama, imzasız veya değiştirilmiş webhook body'lerinin ödeme durumunu değiştirmesini engellemeyi amaçlar.

---

## 13. Webhook SUCCESS Neden Doğrudan PAID Yapmıyor?

Webhook SUCCESS tek başına local siparişi başarılı kabul etmek için kullanılmaz.

Kod önce iyzico `payment/detail` çağrısı ile remote ödeme durumunu tekrar sorgular ve:

- payment status
- amount
- currency
- basket
- item transactions
- provider response signature

gibi bilgileri doğrular.

Ardından `_finalize_successful_payment()` çalışır.

Bu yaklaşım webhook payload'ını tek başına nihai finansal gerçeklik olarak kullanmak yerine provider'dan tekrar doğrulanmış ödeme detayına dayanır.

---

## 14. Webhook / Callback Yarışı

Aynı ödeme için 3DS callback ve webhook birbirine çok yakın zamanlarda gelebilir.

Her iki yol da `PaymentTransaction.select_for_update()` kullanır ve final durumda:

```text
PaymentTransaction.SUCCESS
```

kontrolü yapar.

Böylece başarılı ödeme state'inin ikinci kez yazılması yerine mevcut başarılı sonuç idempotent olarak kullanılabilir.

---

## 15. Stored Card Architecture

Kayıtlı kartlar iyzico Card Storage tarafından tokenize edilir.

Local modelde:

```text
PaymentCustomer
    │
    └── StoredCard
```

bulunur.

`StoredCard` içinde:

- provider card token
- BIN
- son 4 hane
- kart tipi
- kart markası
- kart ailesi
- banka bilgileri
- son kullanma ay/yıl
- local default/active state

tutulur.

Ancak:

```text
PAN / full card number
CVC
```

database'e kaydedilmez.

---

## 16. StoredCardOperation ve Idempotency

Kart create/delete işlemleri için `StoredCardOperation` adında ayrı bir operation kaydı vardır.

Önemli alanlar:

- `idempotency_key`
- `request_fingerprint`
- `status`
- `operation_type`
- `stored_card`
- `provider_card_token`
- `provider_customer_key`
- `provider_external_id`
- hata bilgileri

Create/Delete operation state'leri provider sonucu belirsiz olduğunda doğrudan tekrar çağrı yapmaya izin vermez.

Özellikle:

```text
PENDING
PROVIDER_SUCCEEDED
RECONCILIATION_REQUIRED
```

durumları yeni bir provider çağrısını körlemesine başlatmak yerine reconciliation akışına yönlendirilir.

---

## 17. Request Fingerprint

Aynı `Idempotency-Key` ile farklı kart verisi gönderilmesi kabul edilmez.

Kod, request içeriğinin bir fingerprint'ini üretip `StoredCardOperation.request_fingerprint` alanında saklar.

Bu sayede:

```text
Idempotency-Key = ABC
Request #1 → kart A
Request #2 → kart B
```

durumunda ikinci isteğin ilk operation'ın sonucuymuş gibi kullanılmasının önüne geçilir.

---

## 18. Stored Card Reconciliation

Provider çağrısı başarılı olmuş ancak local transaction sırasında hata oluşmuşsa provider ile local state arasında tutarsızlık oluşabilir.

Kod bunu normal bir `FAILED` gibi ele almak yerine açıkça:

```text
RECONCILIATION_REQUIRED
```

durumuyla işaretler.

Reconciliation gerektiğinde provider tarafı sorgulanır ve local `StoredCard` durumu yeniden senkronize edilir.

---

## 19. Varsayılan Kart

`set_default_card()` transaction içinde çalışır.

Akış:

```text
user + card lock
        ↓
PaymentCustomer lock
        ↓
tüm kartların is_default = false
        ↓
seçilen kart is_default = true
```

Database'de ayrıca yalnızca bir aktif varsayılan kart bulunmasını sağlayan conditional unique constraint vardır.

---

## 20. Refund Architecture

Refund iki seviyeli modellenmiştir:

```text
PaymentRefund
      │
      └── PaymentRefundItem
               │
               └── PaymentTransactionItem
```

`PaymentRefund` logical refund'ı temsil eder.

`PaymentRefundItem` ise iyzico tarafında gerçekleşen provider-level refund operation'ını temsil eder.

Bu ayrım, tek bir logical refund'ın birden fazla iyzico payment transaction item'ına bölünebilmesini sağlar.

---

## 21. Refund Validation

Refund başlamadan önce kod:

- refund ile payment transaction'ın eşleşmesini
- refund ile suborder'ın eşleşmesini
- suborder'ın doğru Order'a ait olmasını
- suborder'ın `CANCELLED` durumda olmasını
- provider'ın `iyzico` olmasını
- currency eşleşmesini
- refund amount'ın pozitif olmasını
- payment transaction'ın `SUCCESS` veya `PARTIALLY_REFUNDED` durumda olmasını

kontrol eder.

`SUBORDER_CANCELLATION` tipinde kargo iadesinin açık olması ayrıca beklenir.

---

## 22. Refund Item Claim

`RefundService` aynı refund içindeki provider işlemlerini sırayla claim eder.

Lock sırası açık olarak:

```text
Order
  ↓
SubOrder
  ↓
PaymentTransaction
  ↓
PaymentRefund / PaymentRefundItem
```

şeklindedir.

Bu sıra cancellation ile de uyumlu tutularak deadlock riskinin azaltılması amaçlanmıştır.

---

## 23. Refund Provider Çağrısı

Provider çağrısı transaction dışında yapılır.

Öncesinde `PaymentRefundItem`:

```text
PENDING → PROCESSING
```

durumuna alınır.

Provider çağrısı başarılıysa ilgili item `SUCCESS` olur.

Başarısızlık sonucu tekrar denenebilir bir durumsa item `FAILED + retryable=True` ile tutulabilir.

Remote sonucun belirsiz olduğu durumlarda ise:

```text
RECONCILIATION_REQUIRED
```

kullanılır.

---

## 24. Refund Reconciliation

Provider çağrısı sırasında network timeout veya local persistence hatası meydana gelirse işlemin iyzico tarafında gerçekleşip gerçekleşmediği kesin değildir.

Bu durumda otomatik olarak tekrar refund çağrısı yapmak yerine item reconciliation durumuna alınır.

`PROCESSING` durumda çok uzun kalan item'lar da timeout sonrasında `RECONCILIATION_REQUIRED` yapılır.

---

## 25. Refund Aggregate State

`PaymentRefund.status`, kendisine bağlı `PaymentRefundItem` kayıtlarından türetilir.

Kabaca:

```text
RECONCILIATION_REQUIRED varsa
    → RECONCILIATION_REQUIRED

PENDING / PROCESSING varsa
    → PENDING

tüm item'lar SUCCESS ise
    → SUCCESS

retryable FAILED varsa
    → PENDING

aksi halde
    → FAILED
```

Benzer şekilde başarılı refund toplamı, ilgili `PaymentTransaction` için:

```text
tam iade    → REFUNDED
kısmi iade  → PARTIALLY_REFUNDED
```

durumuna geçişi sağlar.

---

## 26. Seller / Marketplace Payment

Multi-vendor yapı nedeniyle ödeme kalemleri seller/suborder seviyesine kadar izlenebilir.

`PaymentTransactionItem` hem:

- `SubOrder`
- `OrderItem`

ile ilişkilidir ve provider transaction ID'sini saklar.

Bu yapı gelecekteki refund, seller settlement ve finansal mutabakat işlemlerinin hangi seller/order item'ına ait olduğunu izleyebilmesini sağlar.

Ayrıca `IyzicoSubmerchantService`, seller'ın iyzico Marketplace submerchant onboarding sürecini ayrı bir lifecycle olarak yönetir.

---

## 27. Submerchant Onboarding ve Reconciliation

Seller onboarding akışı genel olarak:

```text
local seller validation
        ↓
PENDING
        ↓
iyzico CREATE
        ├── success → ACTIVE
        ├── timeout / malformed response
        │        ↓
        │   RETRIEVE / reconciliation
        └── unknown → RECONCILIATION_REQUIRED
```

Önemli tasarım ilkeleri:

- stable `subMerchantExternalId` reconciliation anahtarıdır.
- belirsiz CREATE sonucu körlemesine tekrar oluşturulmaz.
- stale `PENDING` kayıtları doğrudan yeni CREATE işlemine çevrilmez.
- provider HTTP çağrıları transaction dışında yapılır.

---

## 28. Güvenlik Kuralları

Ödeme domain'inin temel güvenlik kuralları:

1. PAN/CVC localde saklanmaz.
2. Stored card endpoint'leri yalnızca authenticated kullanıcıya açıktır.
3. Seller/customer verisi request'ten doğrudan provider payload'ına aktarılmadan önce normalize ve validate edilir.
4. 3DS initialize/completion signature doğrulanır.
5. Webhook `X-IYZ-SIGNATURE-V3` ile doğrulanır.
6. Webhook merchantId, konfigüre edilmişse ayrıca kontrol edilir.
7. Payment ID ve conversation ID local kayıt ile eşleştirilir.
8. Amount ve currency provider cevabında tekrar doğrulanır.
9. Provider HTTP çağrıları uzun DB transaction'ları içinde tutulmaz.
10. Belirsiz remote sonuçlar `RECONCILIATION_REQUIRED` ile explicit hale getirilir.

---

## 29. API Özeti

```text
POST /orders/checkout/<order_number>/payment/
    → 3DS ödeme başlatma

POST /payments/iyzico/3ds/callback/
    → 3DS callback / completion

POST /orders/payment/webhook/iyzico/
    → iyzico webhook

GET  /orders/payment/cards/
    → kayıtlı kartları listeleme

POST /orders/payment/cards/
    → kayıtlı kart oluşturma

DELETE /orders/payment/cards/<card_id>/
    → kayıtlı kart silme

POST /orders/payment/cards/<card_id>/default/
    → varsayılan kart seçme
```

---

## 30. Mimari Değerlendirme

Ödeme katmanının öne çıkan tasarım kararları:

- payment attempt ile order state'i ayrıdır.
- provider çağrısı ile local DB transaction ayrıdır.
- callback ve webhook ortak finalizasyon mekanizmasını paylaşır.
- ödeme item seviyesine kadar provider transaction kimlikleri saklanır.
- kart saklama ayrı bir operation/idempotency modeli kullanır.
- refund logical operation ve provider item seviyesinde ayrıştırılmıştır.
- belirsiz provider sonuçları otomatik tekrar yerine reconciliation gerektirir.
- DB constraints ve row-level locking ile yarış durumları azaltılmaya çalışılır.

Bu yapı, özellikle multi-vendor marketplace'te ödeme, refund ve ileride seller settlement süreçlerinin ayrı lifecycle'lar olarak yönetilebilmesini sağlar.
