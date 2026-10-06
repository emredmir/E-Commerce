# Cart Architecture

## 1. Genel Bakış

Cart domain'i, kullanıcının satın almak istediği `StoreProduct` kayıtlarını geçici olarak yönetir. Cart katmanı hem giriş yapmış kullanıcıları hem de misafir kullanıcıları destekler.

Cart domain'inin temel yapı taşları:

- `Cart`: Kullanıcının veya misafir oturumunun aktif sepeti.
- `CartItem`: Sepetteki belirli bir `StoreProduct` için miktar ve seçim durumu.
- `CartService`: Sepetin business/domain logic'inin merkezi service katmanı.
- `BaseCartAPIView` ve türetilen view'lar: HTTP/JSON sınırını yönetir; business logic içermez.
- `StockReservationService`: Checkout doğrulamasında gerçek satın alınabilir stok miktarını hesaplamak için kullanılır.

Temel mimari akış:

```text
HTTP Request
    ↓
Cart API / View
    ↓
CartService
    ↓
Cart / CartItem / StoreProduct
    ↓
MSSQL
```

`CartService` HTTP response üretmez, template render etmez ve Django messages kullanmaz. Bunun yerine domain exception'ları (`CartError` türevleri) fırlatır. HTTP katmanı bu exception'ları JSON response'lara dönüştürür.

---

## 2. Veri Modeli

### 2.1 `Cart`

`Cart`, bir alışveriş sepetinin sahibi ile oturum tabanlı misafir sepeti ayrımını temsil eder.

Sahiplik modeli XOR mantığıyla korunur:

```text
Authenticated cart:
    user != NULL
    session_key == NULL

Guest cart:
    user == NULL
    session_key != NULL
```

Database seviyesinde `cart_user_xor_session` CheckConstraint ile bir sepetin aynı anda hem kullanıcıya hem session'a ait olması veya ikisinin de boş olması engellenir.

Authenticated kullanıcı için `user` alanı `OneToOneField` olduğu için bir kullanıcının tek bir aktif `Cart` kaydı vardır.

Guest kullanıcılar için `session_key` üzerinde koşullu unique constraint bulunur. Böylece aynı session için birden fazla guest cart oluşması engellenir.

### 2.2 `CartItem`

`CartItem`, doğrudan `StoreProduct` kaydına bağlanır. Buradaki önemli tasarım kararı, sepetin global `Product` yerine **satıcının gerçek teklifini** tutmasıdır.

```text
Product
   ↓
ProductVariant
   ↓
StoreProduct  ← CartItem → Cart
```

Bu sayede sepet satırı yalnızca "hangi ürün" bilgisini değil, aynı zamanda hangi mağazanın hangi teklifinin satın alınacağını temsil eder.

`CartItem` üzerinde `unique_cart_store_product` constraint'i vardır. Aynı `Cart` içinde aynı `StoreProduct` için ikinci bir satır oluşturulamaz; yeni ekleme mevcut satırın `quantity` değerini artırır.

---

## 3. Fiyat Modeli

`CartItem` üzerinde kalıcı birim fiyat tutulmaz.

Güncel fiyat:

```python
CartItem.unit_price == CartItem.store_product.price
```

şeklinde `StoreProduct.price` üzerinden okunur.

`last_seen_price` farklı bir amaca hizmet eder: kullanıcının ilgili fiyat değişikliğini en son gördüğü fiyatı saklar.

Dolayısıyla:

```text
StoreProduct.price
        =
   güncel gerçek fiyat

CartItem.last_seen_price
        =
 kullanıcının gördüğü eski fiyat
```

Fiyat değişikliği:

```text
last_seen_price != StoreProduct.price
```

olduğunda `CartItem.price_changed` true olur.

Bu tasarım, sepette eski fiyatı satış fiyatı gibi kullanmak yerine güncel teklif fiyatını esas alır ve kullanıcıya fiyat değişikliğini açıkça gösterebilir.

---

## 4. CartService Sorumlulukları

`CartService` aşağıdaki domain işlemlerini merkezileştirir:

| Method | Sorumluluk |
|---|---|
| `get_or_create_cart()` | Authenticated veya guest cart bulma/oluşturma |
| `add_to_cart()` | Ürünü sepete ekleme veya mevcut miktarı artırma |
| `update_item_quantity()` | Miktar güncelleme; `quantity <= 0` ise silme |
| `remove_item()` | Sepet satırını kaldırma |
| `toggle_item_selection()` | Checkout için satır seçimini değiştirme |
| `merge_guest_cart()` | Guest cart'ı kullanıcı cart'ına birleştirme |
| `get_price_changes()` | Kullanıcının henüz görmediği fiyat değişikliklerini bulma |
| `mark_price_changes_as_seen()` | Görülen fiyat değişikliklerini güncel fiyatla eşitleme |
| `validate_cart_for_checkout()` | Checkout öncesi selected item doğrulaması |
| `get_cart_summary()` | API için temel sepet özeti |
| `get_cart_context_data()` | Cart sayfası için gruplanmış ve hesaplanmış veri |
| `clear_order_items()` | Başarılı ödeme sonrasında güvenli cart temizliği |
| `lock_stock_for_checkout()` | Gerçek checkout transaction'ında final lock + validation |

---

## 5. Sepete Ürün Ekleme

`add_to_cart()` işlemi transaction içerisinde yürütülür ve lock sırası deterministiktir:

```text
Cart
  ↓
StoreProduct
  ↓
CartItem
```

İşlem sırası:

1. `quantity` integer'a çevrilir ve en az 1 olması sağlanır.
2. `Cart` `select_for_update()` ile kilitlenir.
3. `StoreProduct` `select_for_update()` ile kilitlenir.
4. Teklifin aktifliği, mağaza/variant/product aktifliği ve fiziksel stok kontrol edilir.
5. Aynı `CartItem` varsa yeni miktar hesaplanır ve stok sınırı kontrol edilir.
6. Yeni satır oluşturuluyorsa `last_seen_price`, o anki `StoreProduct.price` değerinden başlatılır.
7. Database unique constraint kaynaklı yarış durumları `IntegrityError` ile yakalanarak domain seviyesinde `CartOperationError` üretilir.

Önemli nokta: mevcut `CartItem` tekrar eklendiğinde `last_seen_price` değiştirilmez. Böylece kullanıcının henüz görmediği fiyat değişikliği kaybolmaz.

---

## 6. Miktar Güncelleme

`update_item_quantity()` içinde `quantity <= 0` doğrudan silme anlamına gelir.

Pozitif miktar için:

```text
Cart lock
   ↓
CartItem read
   ↓
StoreProduct lock
   ↓
stok / aktiflik kontrolü
   ↓
CartItem lock
   ↓
final consistency check
   ↓
quantity update
```

`CartItem.store_product_id` ile kilitlenen `StoreProduct.id` arasında final consistency check yapılması, işlem sırasında beklenmeyen ilişki değişikliklerine karşı ek savunma sağlar.

---

## 7. Selection Mantığı

`is_selected`, bir `CartItem`'ın checkout'a dahil edilip edilmeyeceğini belirtir.

Bir satır tekrar seçildiğinde aşağıdaki koşullar sağlanmalıdır:

- `StoreProduct` aktif olmalı.
- Store aktif olmalı.
- Variant aktif olmalı.
- Product aktif olmalı.
- Fiziksel stok sıfırdan büyük olmalı.

Selection işlemi miktarı değiştirmez; yalnızca `is_selected` state'ini günceller.

---

## 8. Guest Cart → User Cart Merge

Kullanıcı giriş yaptığında guest cart, authenticated kullanıcı cart'ı ile birleştirilebilir.

Birleştirme sırasında aynı `StoreProduct` her iki cart'ta da varsa miktarlar toplanır. Sonuç stok sınırını aşarsa kullanıcı tarafındaki miktar:

```text
min(guest_quantity + user_quantity, available_stock)
```

değerine indirilir.

Guest cart'taki ürün artık mevcut değilse veya satışa uygun değilse guest satırı kaldırılır.

Yeni guest satırı geçerli ve stokluysa doğrudan user cart'a taşınır.

Merge işlemi sırasında deadlock riskini azaltmak için global lock sırası deterministik tutulur:

```text
Cart ID ASC
      ↓
StoreProduct ID ASC
      ↓
Guest CartItem
      ↓
User CartItem
```

Her iki cart'ın ID'leri önce sıralanır ve cart row lock'ları küçükten büyüğe alınır.

---

## 9. Fiyat Değişikliği Takibi

Fiyat değişiklikleri kullanıcı deneyimi ve checkout doğruluğu için iki ayrı seviyede ele alınır.

### `get_price_changes()`

Salt-okunur çalışır ve:

```text
item_id
product_name
old_price
new_price
```

bilgilerini döndürür.

### `mark_price_changes_as_seen()`

Kullanıcının fiyat değişikliğini gördüğü kabul edildiğinde `last_seen_price`, güncel `StoreProduct.price` değerine eşitlenir.

Lock sırası:

```text
Cart → CartItem
```

### Cart detay sayfası davranışı

Mevcut `CartDetailView`, context oluşturulduktan sonra fiyat değişiklikleri varsa `mark_price_changes_as_seen()` çağırır. Bu nedenle cart sayfasını görüntülemek, mevcut uygulama davranışında fiyat değişikliklerinin "görüldü" kabul edilmesine neden olur.

---

## 10. Checkout Öncesi Validation

`validate_cart_for_checkout()` selected `CartItem` kayıtlarını doğrular.

Kontrol edilen başlıca koşullar:

```text
StoreProduct mevcut mu?
        ↓
StoreProduct aktif mi?
        ↓
Store aktif mi?
        ↓
Variant aktif mi?
        ↓
Product aktif mi?
        ↓
Available stock yeterli mi?
        ↓
Fiyat değişmiş mi?
```

Kullanılan stok değeri yalnızca `StoreProduct.stock` değildir. `StockReservationService.get_available_stock()` ile o anda gerçekten satın alınabilir stok hesaplanır.

Metodun sonucu:

```python
{
    "is_valid": bool,
    "warnings": [...],
    "price_changes": [...],
}
```

Bir `StoreProduct` artık mevcut değilse, aktif değilse veya stok tamamen bitmişse ilgili `CartItem` seçimi kaldırılır.

Yeterli available stock yoksa warning üretilir ve `is_valid=False` yapılır; miktar otomatik olarak stok seviyesine çekilmez.

### Final checkout garantisi değildir

Bu validation kullanıcı arayüzüne geri bildirim vermek içindir. Kullanıcı validation ile ödeme arasındaki sürede stok başka bir işlem tarafından tüketilebileceği için gerçek checkout transaction'ında final validation tekrar yapılmalıdır.

---

## 11. Final Checkout Lock

`lock_stock_for_checkout()` kendi transaction'ını açmaz.

Bu method, gerçek checkout transaction'ının içinde çağrılmalıdır:

```python
with transaction.atomic():
    result = CartService.lock_stock_for_checkout(cart)
    # Order oluştur
    # OrderItem oluştur
    # Stock reservation / stock update
    # Payment workflow
    # Cart cleanup
```

Method şu işlemleri yapar:

1. Cart'ı lock eder.
2. Selected item kimliklerini çıkarır.
3. İlgili `StoreProduct` kayıtlarını ID ASC sırasıyla lock eder.
4. Selected `CartItem` kayıtlarını lock eder.
5. Ürünün hâlâ aktif ve satışa uygun olduğunu tekrar kontrol eder.
6. `StockReservationService.get_available_stock()` ile final available stock kontrolü yapar.
7. Uygunsa checkout transaction'ında kullanılmak üzere lock'lu instance'ları döndürür.

Methodun kendisi:

- stok düşürmez,
- Order oluşturmaz,
- ödeme yapmaz.

Bu ayrım checkout orchestration'ının Cart domain'i dışında tutulmasını sağlar.

---

## 12. Başarılı Ödeme Sonrasında Cart Temizleme

`clear_order_items()` yalnızca checkout sırasında satın alınan ve checkout sonrasında değişmemiş olan `CartItem` kayıtlarını siler.

Güvenlik kontrolü:

```text
OrderItem.source_cart_item_updated_at
                  ==
CartItem.updated_at
```

eşleşiyorsa cart item checkout snapshot'ından sonra değiştirilmemiş kabul edilir ve silinebilir.

Eşleşmiyorsa cart item'a dokunulmaz.

Bunun amacı, başarılı bir checkout sonrasında kullanıcının checkout sürecinden sonra sepet üzerinde yaptığı yeni değişikliklerin yanlışlıkla silinmesini engellemektir.

---

## 13. HTTP / Service Katman Ayrımı

Cart API endpoint'leri `BaseCartAPIView` üzerinden ortak response ve exception handling davranışı paylaşır.

```text
                ┌────────────────────────┐
                │   BaseCartAPIView      │
                ├────────────────────────┤
                │ JSON parse             │
                │ success response       │
                │ error response         │
                │ exception → HTTP       │
                │ unexpected error log   │
                └────────────┬───────────┘
                             │
                             ▼
                       CartService
```

Beklenen domain hataları kullanıcıya uygun HTTP response'a çevrilir. Beklenmeyen hatalar loglanır ve genel bir `500` response döndürülür.

Bu ayrım sayesinde `CartService` doğrudan Django HTTP katmanına bağımlı değildir.

---

## 14. Cart Endpoint'leri

| Method | Endpoint | Amaç |
|---|---|---|
| GET | `/cart/` | Cart sayfası |
| GET | `/cart/api/data/` | Güncel cart JSON + mini-cart HTML |
| POST | `/cart/api/add/` | `StoreProduct` sepete ekleme |
| POST | `/cart/api/update/<item_id>/` | Miktar güncelleme |
| DELETE | `/cart/api/remove/<item_id>/` | CartItem silme |
| POST | `/cart/api/toggle-selection/<item_id>/` | Selection state değiştirme |
| POST | `/cart/api/acknowledge-price/` | Fiyat değişikliklerini görüldü işaretleme |
| POST | `/cart/api/validate-checkout/` | Checkout öncesi validation |

---

## 15. Performans ve Query Optimizasyonu

Cart servisinde ilişkisel veri ihtiyacı olan read operasyonlarında `select_related()` ve gerektiğinde `prefetch_related()` kullanılır.

Örnekler:

```text
CartItem
  └── StoreProduct
        ├── Store
        ├── Variant
        │     └── Product
        └── Product
```

Bu ilişkiler tek tek sorgulanmak yerine ilgili read path'lerinde önceden yüklenir.

Cart sayfasında product image'ları, attribute value'ları ve diğer ilişkili veriler de `prefetch_related()` ile hazırlanır.

---

## 16. Concurrency ve Deadlock Önleme

Cart domain'inde concurrency en kritik konulardan biridir; özellikle aynı `StoreProduct` için iki farklı kullanıcının aynı anda stok kontrolü yapması durumunda yalnızca uygulama seviyesindeki `if stock >= quantity` kontrolü yeterli değildir.

Bu nedenle stock-sensitive işlemler transaction + row-level lock kullanır.

Standart sıra:

```text
Cart
 ↓
StoreProduct
 ↓
CartItem
```

Birden fazla kayıt varsa ID bazında deterministik sıralama uygulanır.

Amaç, iki transaction'ın tabloları farklı sırada kilitleyerek oluşturabileceği deadlock olasılığını azaltmaktır.

---

## 17. Tasarım Kararları

### Service Layer

Business logic'in View katmanına dağılmasını önlemek için `CartService` kullanılır.

### Current Price + Last Seen Price

Sepette fiyatı snapshot olarak saklamak yerine güncel `StoreProduct.price` kullanılır; `last_seen_price` ise yalnızca kullanıcıya fiyat değişikliğini göstermek için tutulur.

### StoreProduct Referansı

`CartItem` doğrudan `StoreProduct`'a bağlanarak multi-vendor siparişlerde hangi satıcının hangi teklifinin alınacağı netleştirilir.

### Database Constraints

Guest/user cart sahipliği, aynı session için tek cart ve aynı cart içinde aynı offer'ın tek satır olması database seviyesinde korunur.

### Final Checkout Lock

UI validation ile gerçek ödeme/checkout arasında zaman geçtiği için final inventory validation tekrar transaction içerisinde yapılır.

### Güvenli Cart Cleanup

Başarılı ödeme sonrası cart temizliği `OrderItem` üzerindeki checkout snapshot timestamp'i ile korunur; checkout sonrası değişen cart item silinmez.

---

## 18. Bilinçli Olarak CartService Dışında Tutulan İşlemler

CartService:

- Order oluşturmaz.
- Payment gerçekleştirmez.
- HTTP response üretmez.
- Template render etmez.
- Kullanıcıya Django messages göndermez.

Bu sorumluluk ayrımı, checkout ve payment orchestration'ının ayrı service/domain katmanlarında yönetilmesine imkân verir.
