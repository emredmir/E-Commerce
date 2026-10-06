# Checkout ve Order Mimarisi

## 1. Genel Bakış

Bu domain, müşterinin seçili `CartItem` kayıtlarından gerçek bir `Order` oluşturulmasını, multi-vendor yapıda siparişin mağazalara göre `SubOrder` kayıtlarına ayrılmasını, sipariş anındaki kritik bilgilerin snapshot olarak saklanmasını ve stok rezervasyonunun yönetilmesini kapsar.

Temel yapı şöyledir:

```text
Cart
  ↓
OrderService.create_from_cart()
  ↓
Order
  ├── SubOrder (Store A)
  │     └── OrderItem
  ├── SubOrder (Store B)
  │     └── OrderItem
  └── StockReservation
```

Checkout ile payment birbirinden ayrılmıştır. `CheckoutCreateOrderAPIView` ödeme başlatmaz; önce sipariş ve rezervasyon oluşturur. Ödeme işlemi sonraki endpoint üzerinden `PaymentService` tarafından başlatılır.

---

## 2. Katman Sorumlulukları

### `CheckoutPageView`

Checkout HTML sayfasını oluşturur.

Başlıca görevleri:

- Kullanıcının veya guest session'ın cart'ını almak.
- Seçili ürünleri mağaza bazında gruplamak.
- Mağaza bazında ürün ara toplamlarını hesaplamak.
- `ShippingService.calculate_shipping()` ile kargo tutarını hesaplamak.
- Checkout toplamını kullanıcıya göstermek.
- Authentication durumuna göre kayıtlı adresleri ve kayıtlı kartları göstermek.

Bu view order oluşturmaz ve stock reservation oluşturmaz.

### `CheckoutCreateOrderAPIView`

Checkout submit endpoint'idir:

```text
POST /orders/checkout/create/
```

Bu endpoint:

1. JSON body'yi parse eder.
2. Cart'ı alır.
3. Shipping ve billing adreslerini çözer.
4. Guest / authenticated kullanıcı ayrımını yapar.
5. Seçilecek `CartItem` ID'lerini belirler.
6. Currency bilgisini alır.
7. `OrderService.create_from_cart()` çağırır.
8. Guest kullanıcı için oluşturulan sipariş numarasını session'a yazar.

Ödeme başlatmaz.

Başarılı sonuçta:

```text
Order.status = PENDING_PAYMENT
StockReservation.status = ACTIVE
```

olur.

### `OrderService`

Order domain business logic'inin merkezidir.

Sorumlulukları:

- Cart ownership kontrolü.
- Checkout item seçimi ve kilitlenmesi.
- `StoreProduct` availability doğrulaması.
- Mağaza bazında gruplama.
- Order ve SubOrder toplamlarının hesaplanması.
- `Order` oluşturulması.
- `SubOrder` oluşturulması.
- `OrderItem` snapshot'larının oluşturulması.
- Müşteri ve adres snapshot'larının saklanması.
- `StockReservationService.reserve_order()` çağrısı.
- Customer / seller order access kontrolü.
- SubOrder status transition kontrolü.

### `StockReservationService`

Stok üzerinde nihai karar veren servistir.

Order oluşturma sırasında reservation yaratır; başarılı payment sonrasında reservation'ları consume ederek fiziksel stoğu düşürür.

Lifecycle:

```text
ACTIVE
 ├──> CONSUMED
 ├──> RELEASED
 └──> EXPIRED
```

### `ShippingService`

Kargo ücretini hesaplar ve `SubOrder` fulfillment state'ini yönetir.

Bu service:

- Kargo ücretini hesaplar.
- `PREPARING → SHIPPED` geçişini yapar.
- Kargo firması ve tracking numarasını kaydeder.
- `SHIPPED → DELIVERED` geçişini yapar.

Gerçek bir kargo firması API entegrasyonu bu service'in mevcut sorumluluğu değildir.

### `InvoiceService`

`SubOrder` için invoice oluşturur ve seller / buyer / financial snapshot üretir.

Mevcut akışta `PENDING → PREPARING` geçişi sırasında `InvoiceService.create_for_suborder()` çağrılır.

### `CancellationService`

Ödenmiş ve iptal edilebilir durumdaki bir `SubOrder` için:

- başarılı payment'ı doğrular,
- tüketilmiş reservation'ları geri yükler,
- `PaymentRefund(PENDING)` oluşturur,
- `SubOrderCancellation` snapshot'ı oluşturur,
- `SubOrder → CANCELLED` geçişini yapar.

Provider refund HTTP çağrısı bu service'in sorumluluğu değildir; refund domain'i ayrı service'te çalışır.

---

## 3. Checkout Veri Akışı

Checkout sırasında `OrderService.create_from_cart()` aşağıdaki sırayı kullanır:

```text
1. Currency normalize / validate
        ↓
2. Cart ownership validate
        ↓
3. Shipping / billing address normalize
        ↓
4. Customer email / phone normalize
        ↓
5. Selected CartItem kayıtlarını lock et
        ↓
6. StoreProduct kayıtlarını lock et
        ↓
7. Product / StoreProduct availability validate
        ↓
8. Item'ları Store bazında grupla
        ↓
9. Order totals hesapla
        ↓
10. Order oluştur
        ↓
11. SubOrder + OrderItem oluştur
        ↓
12. StockReservationService.reserve_order()
        ↓
13. Order döndür
```

Metot `@transaction.atomic` ile çalışır. Reservation oluşturma başarısız olursa Order, SubOrder ve OrderItem kayıtları da rollback olur.

---

## 4. Checkout Item Locking

`OrderService`, checkout'taki `CartItem` kayıtlarını `select_for_update()` ile lock eder.

Kayıtlar:

```text
store_product_id ASC
    ↓
id ASC
```

sırasıyla işlenir.

Bu deterministik sıralama concurrent işlemlerde ters lock alma riskini azaltmak için önemlidir.

Daha sonra ilgili `StoreProduct` kayıtları yine `select_for_update()` ile lock edilir.

Bu lock, reservation'ın kendisi değildir. Amacı checkout sırasında sipariş snapshot'ı oluşturulurken `StoreProduct` verisinin başka bir transaction tarafından değiştirilmesini engellemektir.

Nihai stock availability ve reservation kararı `StockReservationService` tarafından verilir.

---

## 5. Multi-Vendor Sipariş Bölme

Müşteri tek checkout yaptığında sistem tek bir `Order` oluşturur; ancak ürünler farklı mağazalara aitse aynı `Order` altında birden fazla `SubOrder` oluşturulur.

Örnek:

```text
Customer Cart
│
├── Store A → Product X
├── Store A → Product Y
└── Store B → Product Z

             ↓

Order #TX-...
│
├── SubOrder #SUB-... (Store A)
│   ├── OrderItem X
│   └── OrderItem Y
│
└── SubOrder #SUB-... (Store B)
    └── OrderItem Z
```

Aynı `Order` içinde aynı mağaza için ikinci bir `SubOrder` oluşturulamaz. Modelde:

```text
UniqueConstraint(order, store)
```

kullanılmıştır.

Bu yapı customer için tek sipariş deneyimi verirken seller tarafında mağaza bazlı fulfillment sağlar.

---

## 6. Snapshot Stratejisi

Sipariş geçmişi canlı katalog verisine bağımlı bırakılmamıştır.

### Order snapshot

`Order` içinde sipariş anındaki:

- customer email
- customer phone
- shipping address
- billing address
- subtotal
- discount
- shipping
- tax
- total
- currency
- discount code snapshot

saklanır.

Böylece kullanıcı profilini veya adresini daha sonra değiştirse bile geçmiş sipariş aynı kalır.

### SubOrder snapshot

`SubOrder` içinde en azından mağazanın sipariş anındaki görünen adı (`store_name_snapshot`) ve mağaza bazlı finansal değerler saklanır.

### OrderItem snapshot

`OrderItem`, canlı `StoreProduct` referansına ek olarak sipariş anındaki kritik bilgileri saklar:

- ürün adı
- varyant bilgisi
- varyantın görüntü metni
- SKU
- barcode
- quantity
- unit price
- discount
- tax
- total
- ürün görseli snapshot'ı

`store_product` referansı yine tutulur; ancak tarihsel gösterim için snapshot alanları esas alınabilir.

### Görsel snapshot

Sipariş anında kullanılan ürün görseli fiziksel dosya olarak `OrderItem.image_snapshot` alanına kopyalanabilir.

Bu sayede kaynak `ProductImage` sonradan değişse veya kaldırılmış olsa bile sipariş geçmişindeki görsel korunur.

---

## 7. Adres Snapshot

Checkout sırasında kaynak `Address` modeli doğrudan Order'a bağlanmak yerine `AddressData` DTO'su üzerinden normalized veri alınır.

```text
Address model
    ↓
AddressData
    ↓
normalize / validate
    ↓
Order.shipping_* / Order.billing_*
```

Bu yaklaşım iki fayda sağlar:

1. Sipariş geçmişi kullanıcının gelecekteki adres değişikliklerinden etkilenmez.
2. `OrderService`, checkout verisinin `Address` modeline doğrudan bağımlılığını azaltır.

Ayrıca billing adresi verilmezse checkout create endpoint'i shipping adresini billing adresi olarak kullanır.

---

## 8. Stok Rezervasyonu

Order oluşturulduğunda fiziksel stok hemen düşürülmez.

Önce geçici reservation oluşturulur:

```text
OrderItem
   ↓
StockReservation
status = ACTIVE
expires_at = now + 15 minutes
```

`StockReservationService.RESERVATION_DURATION` mevcut kodda 15 dakikadır.

Bu yaklaşımın amacı payment tamamlanana kadar ürünün başka bir checkout tarafından tüketilmesini kontrol etmektir.

### Reservation consume

Başarılı payment sonrasında:

```text
ACTIVE reservation
        ↓
physical stock -= quantity
sold_count += quantity
        ↓
Reservation = CONSUMED
        ↓
Order = PAID
```

### Reservation release

İşlem iptal edilirse veya reservation süresi dolarsa reservation:

```text
ACTIVE → RELEASED
```

veya:

```text
ACTIVE → EXPIRED
```

durumuna geçebilir.

Release sırasında fiziksel stok tekrar düşürülmez; çünkü henüz consume edilmemiştir.

### Consumed reservation restore

Ödenmiş bir `SubOrder` iptal edildiğinde, daha önce consume edilmiş reservation'lar geri yüklenebilir:

```text
CONSUMED
   ↓
RELEASED

stock += quantity
sold_count -= quantity
```

Bu işlemi doğrudan `CancellationService` uygulamaz; `StockReservationService.restore_consumed_suborder()` sorumludur.

---

## 9. Payment ile Handoff

Checkout ve payment ayrı aşamalardır.

### Aşama 1 — Order creation

```text
Cart
 ↓
OrderService
 ↓
Order = PENDING_PAYMENT
 ↓
StockReservation = ACTIVE
```

### Aşama 2 — Payment initialization

```text
Order(PENDING_PAYMENT)
 ↓
PaymentService.initialize_3ds()
 ↓
PaymentTransaction(INITIATED)
 ↓
iyzico
 ↓
PaymentTransaction(PENDING)
 ↓
3DS HTML
```

Bu ikinci aşamanın provider detayları PHASE 7'de ayrıca açıklanacaktır.

### Aşama 3 — Payment completion

Başarılı ödeme doğrulandıktan sonra `PaymentService` ortak finalization mantığı üzerinden:

```text
PaymentTransaction = SUCCESS
        ↓
StockReservationService.consume_order()
        ↓
Order = PAID
        ↓
CartService.clear_order_items()
```

adımlarını uygular.

Cart temizleme işlemi başarısız olursa exception loglanır; başarılı payment ve reservation consume rollback edilmez. Bu bilinçli bir hata izolasyonu tercihidir.

---

## 10. Order ve SubOrder Durumları

### OrderStatus

Ana Order için modelde aşağıdaki durumlar vardır:

```text
PENDING_PAYMENT
PAID
PREPARING
PARTIALLY_SHIPPED
SHIPPED
DELIVERED
CANCELLED
EXPIRED
COMPLETED
```

Mevcut akışta checkout sonrası başlangıç durumu `PENDING_PAYMENT`, başarılı payment sonrası doğrudan `PAID` olarak ayarlanır.

Ana Order'ın daha sonraki fulfillment durumlarının otomatik aggregate edildiği merkezi bir state transition servisi mevcut kodda bulunmamaktadır. Bu nedenle dokümantasyonda `SubOrder` yaşam döngüsü ile `Order` yaşam döngüsü birbirinden ayrı ele alınmıştır.

### SubOrderStatus

Seller fulfillment akışı:

```text
PENDING
  ↓
PREPARING
  ↓
SHIPPED
  ↓
DELIVERED
```

İptal akışı:

```text
PENDING ────────┐
                │
PREPARING ──────┴──→ CANCELLED
```

`OrderService.SUBORDER_STATUS_TRANSITIONS` mevcut kodda yalnızca:

- `PENDING → PREPARING`
- `PREPARING → SHIPPED`
- `SHIPPED → DELIVERED`

geçişlerine izin verir. Cancellation ayrı `CancellationService` tarafından yönetilir.

---

## 11. Invoice Oluşturma

Mevcut seller fulfillment akışında `PENDING → PREPARING` geçişi sırasında:

```text
SubOrder
   ↓
InvoiceService.create_for_suborder()
   ↓
Invoice
   ↓
InvoiceItem[]
   ↓
SubOrder = PREPARING
```

Invoice içinde seller ve buyer bilgileri snapshot olarak tutulur.

Bunun amacı seller profilindeki veya müşteri bilgilerindeki sonraki değişikliklerin geçmiş faturayı değiştirmemesidir.

---

## 12. Shipping

`ShippingService.calculate_shipping()` mevcut kuralları uygular:

```text
subtotal <= 0
    → 0 TL

0 < subtotal < 750 TL
    → 99,99 TL

subtotal >= 750 TL
    → ücretsiz
```

Checkout sayfasında shipping mağaza bazında hesaplanır.

Bu nedenle multi-vendor checkout'ta her `SubOrder` kendi mağaza ara toplamına göre kargo tutarı taşıyabilir.

---

## 13. Cancellation Handoff

Cancellation domain'i Order domain'inin doğal devamıdır ancak refund işlemi ayrı tutulmuştur.

Mevcut iptal akışı:

```text
SubOrder
   ↓
validate cancellable state
   ↓
find successful payment
   ↓
calculate SubOrder refund amount
   ↓
restore consumed stock
   ↓
PaymentRefund(PENDING)
   ↓
SubOrderCancellation
   ↓
SubOrder = CANCELLED
```

`PaymentRefund` kaydı burada provider çağrısı yapılmadan oluşturulur. Gerçek provider refund işlemi `RefundService` tarafından daha sonra işlenir.

Bu ayrım transaction sınırlarını ve provider HTTP çağrılarını daha kontrollü hale getirir.

---

## 14. Guest Checkout

Sistem authenticated kullanıcı dışında guest checkout'ı da destekleyecek şekilde tasarlanmıştır.

Guest checkout'ta:

- `user = None`
- `session_key` kullanılır.
- shipping / billing adresi request body'den alınabilir.
- customer email / phone request'ten alınabilir.
- oluşturulan `order_number` session'a `checkout_order_number` anahtarıyla yazılır.

Başarı sayfasındaki guest erişimi bu session bilgisi üzerinden kontrol edilir.

Bu nedenle guest order erişiminde yalnızca URL'deki `order_number` yeterli değildir.

---

## 15. Yetkilendirme ve Order Access

Order erişimi iki ana senaryoya ayrılır:

### Customer

Authenticated kullanıcı yalnızca kendi `Order` kayıtlarına erişebilir.

Guest kullanıcı için başarı sayfasında session'daki `checkout_order_number` ile eşleşme aranır.

### Seller

Seller, ana Order'ın tamamını görmek yerine kendi `Store` kaynağına bağlı `SubOrder` kayıtları üzerinden çalışır.

Bu ayrım multi-vendor izolasyonun temel parçalarından biridir.

---

## 16. Transaction Sınırları

Order domain'inde transaction sınırları bilinçli olarak ayrılmıştır.

### Order creation

`OrderService.create_from_cart()`:

```python
@transaction.atomic
```

kullanır.

Aynı transaction içinde:

```text
Order
SubOrder
OrderItem
StockReservation
```

oluşturulur.

### Payment provider çağrıları

iyzico HTTP çağrıları DB transaction'ı açık tutulmadan gerçekleştirilir.

Bu tercih:

- uzun transaction'ları engellemek,
- DB lock'larını provider latency'sine bağlamamak,
- dış servis gecikmesinin DB transaction'ı bloklamasını önlemek

içindir.

### Final payment state

Provider doğrulamasından sonra kısa bir DB transaction içinde payment/order/reservation finalization yapılır.

---

## 17. Idempotency ve Yarış Durumları

Order domain'i ile payment domain'i arasında aynı işlemin tekrar gelmesi ihtimali dikkate alınmıştır.

Örneğin payment completion tarafında aynı başarılı transaction tekrar işlendiğinde `PaymentStatus.SUCCESS` kontrolü sonucu önceki başarılı sonuç tekrar döndürülebilir.

Stock reservation tarafında da:

- order lock,
- StoreProduct lock,
- active reservation lock

kullanılarak concurrent işlemlerde tek bir stok kararının oluşması hedeflenir.

Lock sıralarının deterministik tutulması deadlock riskini azaltmak için önemlidir.

---

## 18. Hata Yönetimi

Order service'leri HTTP response üretmez; domain exception'ları fırlatır.

`BaseOrderAPIView` bu exception'ları HTTP durum kodlarına dönüştürür.

Örneğin:

```text
InsufficientStockError
    → 409 Conflict

InvalidOrderStateError
    → 409 Conflict

EmptyOrderError
    → 400 Bad Request

ProductUnavailableError
    → 400 Bad Request

OrderNotFoundError
    → 404 Not Found

PaymentGatewayError
    → 502 Bad Gateway
```

Böylece business logic ile HTTP presentation katmanı birbirinden ayrılmıştır.

---

## 19. Mevcut Mimari Özeti

```text
Checkout UI
    ↓
CheckoutCreateOrderAPIView
    ↓
OrderService
    ├── Cart validation
    ├── Address snapshot
    ├── Item locking
    ├── Store grouping
    ├── Order totals
    ├── Order / SubOrder / OrderItem creation
    └── StockReservationService
              ↓
        StockReservation

Order(PENDING_PAYMENT)
    ↓
PaymentService
    ↓
PaymentTransaction
    ↓
Provider
    ↓
SUCCESS
    ↓
StockReservationService.consume_order()
    ↓
Order(PAID)
    ↓
Cart cleanup
```

Bu yapı, checkout'ın sipariş oluşturma kısmını payment provider çağrısından ayırırken stok tutarlılığı ve multi-vendor sipariş ayrıştırmasını aynı domain sınırları içinde korur.
