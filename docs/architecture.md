# System Architecture

## 1. Genel Bakış

Bu proje Django tabanlı, MSSQL kullanan çok satıcılı (multi-vendor) bir e-ticaret uygulamasıdır. Uygulama; müşteri, satıcı, mağaza, ürün kataloğu, satıcı teklifleri, sepet, sipariş, ödeme, iade, fatura ve kargo gibi farklı domainleri tek bir Django projesi içerisinde yönetir.

Mimari yaklaşımın temel amacı, HTTP katmanındaki View sorumluluklarını domain/business logic'ten ayırmaktır. Özellikle karmaşık iş kuralları `services/` altında toplanır; View'lar ise isteği karşılar, yetki/erişim kontrollerini uygular, uygun service'i çağırır ve HTTP cevabını üretir.

> Not: Bu dokümantasyon ZIP'teki kaynak kod baz alınarak hazırlanmıştır. Canlı MSSQL sunucusunun fiziksel topolojisi veya deployment ortamı bu çalışmada doğrulanmamıştır.

---

## 2. Mimari Katmanlar

```text
Client / Browser
       │
       ▼
Django URL Routing
       │
       ▼
Views / API Views
       │
       ├──────────────► Django Forms / Validation
       │
       ▼
Domain Services
       │
       ▼
Django ORM / Models
       │
       ▼
Microsoft SQL Server
```

Bazı işlemlerde uygulama dış servis veya arka plan çalışanına da bağlanır:

```text
Domain Service
    ├──► iyzico
    ├──► Celery task
    └──► File / Media storage
```

---

## 3. Uygulama Sınırları

### `core`

Uygulamanın temel/ortak bölümü ve ana sayfa gibi genel giriş noktalarını içerir.

### `accounts`

Kullanıcı hesabı ve kimlik alanını yönetir:

- `CustomUser`
- email / telefon ile authentication
- adresler
- satıcı profili
- satıcı başvuru ve onay süreçleri
- müşteri sipariş geçmişi
- kullanıcının kayıtlı kartları ve ilgili ekranlar
- müşteri tarafındaki ürün soru-cevap işlemleri

Özel authentication backend:

```text
EmailOrPhoneBackend
        │
        ├── email ile kullanıcı arama
        └── phone_number ile kullanıcı arama
```

### `store`

Satıcı mağaza yaşam döngüsünü ve satıcı operasyon ekranlarını yönetir:

- mağaza oluşturma
- mağaza güncelleme
- mağaza arşivleme
- mağaza dashboard'u
- public mağaza vitrini
- satıcı soru-cevap ekranları
- satıcı sipariş listesi / detayı
- satıcı sipariş durum güncelleme
- satıcı iptal işlemleri

### `products`

Ürün kataloğu ve satıcı teklif sisteminin ana domainidir:

- kategori ve marka
- attribute / variant yapısı
- ürün ve ürün görselleri
- ürün taslakları ve çok adımlı Product Wizard
- satıcı offer oluşturma / güncelleme / arşivleme
- storefront ve Buy Box sorguları
- koleksiyonlar
- ürün soru-cevap
- taslak yayınlama
- ürün indeksleme için async görev

### `cart`

Sepetin domain/business logic'ini `CartService` üzerinden yönetir:

- sepete ekleme
- adet güncelleme
- ürün çıkarma
- seçim değiştirme
- guest cart
- guest → authenticated user merge
- fiyat değişikliği takibi
- checkout öncesi cart validation
- stok ile ilgili transaction/locking işlemleri için order domain'i ile koordinasyon

### `orders`

Checkout sonrası sipariş ve ticari işlem akışının merkezidir:

- Order / SubOrder / OrderItem
- stok rezervasyonu
- sipariş oluşturma
- ödeme işlemleri
- 3DS callback
- iyzico webhook
- kayıtlı kart yönetimi
- fatura
- kargo
- iptal
- iade

---

## 4. View → Service Ayrımı

Projede View katmanı ile service katmanı arasında belirgin bir sorumluluk ayrımı bulunur.

### View katmanının sorumlulukları

- HTTP request kabul etmek
- authentication / permission / ownership kontrolü yapmak
- URL parametrelerini ve form/body verisini almak
- service çağrısını yapmak
- domain exception'larını HTTP cevabına dönüştürmek
- template veya JSON response üretmek
- redirect / message üretmek

### Service katmanının sorumlulukları

- business rule uygulamak
- birden fazla modeli ilgilendiren işlemleri orkestre etmek
- transaction sınırlarını yönetmek
- database locking gerektiğinde uygulamak
- dış servislerle entegrasyon yapmak
- domain seviyesinde hata üretmek

Örnek genel akış:

```text
HTTP Request
     │
     ▼
APIView / View
     │
     │ validated input
     ▼
Domain Service
     │
     ├── business rules
     ├── transaction
     ├── locking
     └── external integration
     │
     ▼
Django ORM
     │
     ▼
MSSQL
```

> `CartService` bu ayrımın en belirgin örneklerinden biridir: servis HTTP response, template veya Django message üretmez; domain seviyesinde sonuç veya exception üretir.

---

## 5. URL ve HTTP Katmanı

Kök URL yönlendirmesi `ecommerce_project/urls.py` üzerinden yapılır.

```text
/                   → core
/accounts/          → accounts
/stores/            → store
/products/          → products
/cart/              → cart
/orders/            → orders
/payments/...       → iyzico 3DS callback
/admin/             → Django admin
```

Uygulamada iki temel HTTP sunum şekli birlikte kullanılır:

### HTML / server-rendered sayfalar

Kullanıcı ve satıcı arayüzlerinin önemli bölümü Django template/view yapısı ile sunulur.

### JSON / API endpoint'leri

Özellikle cart, QA, collections, wizard ve payment gibi dinamik işlemlerde APIView tabanlı JSON endpoint'leri bulunur.

Bu nedenle sistem tam anlamıyla ayrı bir REST backend + SPA mimarisinden ziyade **server-rendered Django + API endpoint'lerinin birlikte kullanıldığı hibrit bir web mimarisidir**.

---

## 6. Kimlik Doğrulama ve Yetkilendirme

Authentication için Django auth altyapısı ve özel `EmailOrPhoneBackend` birlikte kullanılır.

```text
User Login
    │
    ▼
Authentication Form / Login View
    │
    ▼
EmailOrPhoneBackend
    │
    ├── email
    └── phone_number
    │
    ▼
CustomUser
    │
    ▼
Django Session / Authentication Middleware
```

Satıcıya özel ekranlarda ayrıca satıcının gerçekten ilgili mağazanın sahibi olup olmadığı kontrol edilir. Bu ownership kontrolleri özellikle `store` ve `products` domainlerinde önemlidir.

---

## 7. Veri Erişimi

Uygulamanın veri erişim katmanı Django ORM'dir.

```text
Service / View
     │
     ▼
Django ORM
     │
     ├── select_related
     ├── prefetch_related
     ├── annotate / aggregate
     ├── select_for_update
     └── transaction.atomic
     │
     ▼
Microsoft SQL Server
```

`DEFAULT_AUTO_FIELD = BigAutoField` olarak tanımlıdır. Database bağlantısı `mssql-django` üzerinden SQL Server'a, `pyodbc` ve `ODBC Driver 17 for SQL Server` kullanılarak yapılır.

---

## 8. Transaction ve Concurrency Yaklaşımı

Kritik ticari işlemlerde transaction sınırları service katmanında ele alınır.

Başlıca örnekler:

- stok rezervasyonu
- sipariş oluşturma
- ödeme tamamlama ile ilişkili database işlemleri
- iptal / iade işlemleri
- cart → order geçişindeki kritik işlemler
- eş zamanlı stok değişikliklerini kontrol eden row-level locking

Genel yaklaşım:

```text
transaction.atomic()
       │
       ▼
select_for_update()
       │
       ▼
Validate current state
       │
       ▼
Write / reserve / update
       │
       ▼
Commit
```

Özellikle checkout öncesi validation ile gerçek checkout transaction'ı birbirinden ayrıdır. Ön validation kullanıcı deneyimi için faydalıdır; nihai stok ve durum garantisi, checkout sırasında yeniden lock + validation ile sağlanır.

---

## 9. Ödeme ve Dış Servis Entegrasyonu

Ödeme domain'i `orders/services/` altında ayrı service'lere bölünmüştür.

Başlıca entegrasyon noktaları:

```text
PaymentService
CardStorageService
IyzicoSubmerchantService
RefundService
        │
        ▼
     iyzico
```

Desteklenen akışlar arasında:

- ödeme başlatma
- 3DS
- 3DS callback
- payment webhook
- kayıtlı kart işlemleri
- satıcı sub-merchant işlemleri
- refund

bulunur.

Iyzico ayarları environment variable üzerinden okunur; API key, secret ve callback URL gibi hassas bilgiler kaynak koda sabitlenmemiştir.

---

## 10. Asenkron İşler

Projede Celery kullanılır. ZIP'teki mevcut async task örneği ürün yayınlandıktan sonra ürün görsellerinin kopyalanmasıdır.

```text
Product Publish
      │
      ▼
async_copy_product_images.delay(...)
      │
      ▼
Celery Worker
      │
      ▼
Image Copy Operation
```

Bu görev ağır görsel kopyalama işlemini HTTP request'in ana akışından ayırmak için kullanılır.

> Mevcut task kodunda hata loglanmaktadır; bu nedenle dokümantasyonda otomatik retry mekanizmasının aktif olduğu varsayılmamıştır.

---

## 11. Dosya / Media Katmanı

Django `MEDIA_ROOT` ve `MEDIA_URL` kullanır. Ürün ve mağaza görselleri model `FileField/ImageField` alanları üzerinden yönetilir.

Development modunda medya dosyaları Django URL yapılandırmasına eklenmektedir. Üretim ortamında ise ayrı bir static/media servisinin veya object storage çözümünün konfigüre edilmesi deployment tercihine bağlıdır; mevcut kaynak kod bunu zorunlu olarak belirlememektedir.

---

## 12. ASGI / WSGI Giriş Noktaları

Projede hem:

```text
wsgi.py
asgi.py
```

bulunmaktadır. Bu, Django uygulamasının WSGI veya ASGI tabanlı sunucularla çalıştırılabilmesine uygun giriş noktalarının mevcut olduğunu gösterir.

Ancak mevcut koddan belirli bir production server (ör. Gunicorn, Uvicorn veya IIS reverse proxy) konfigürasyonunun kullanıldığı sonucu çıkarılmamalıdır.

---

## 13. Yüksek Seviyeli Domain İletişimi

```text
                    ┌──────────────┐
                    │   accounts   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    store     │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   products   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │     cart     │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    orders    │
                    └──────┬───────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
          Payment       Refund        Shipping
             │
             ▼
           iyzico
```

Bu oklar paketler arasındaki kod bağımlılıklarının tamamını değil, **işlevsel/domain ilişkisini** temsil eder.

---

## 14. Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| Web framework | Django |
| ORM | Django ORM |
| Database | Microsoft SQL Server |
| DB adapter | mssql-django / pyodbc |
| Authentication | Django Auth + Custom backend |
| Templates | Django Templates |
| Forms | Django Forms + django-crispy-forms |
| Payment provider | iyzico |
| Async processing | Celery |
| Image processing | Pillow |
| ASGI | ASGI entrypoint mevcut |
| WSGI | WSGI entrypoint mevcut |

Kaynak bağımlılıklarındaki sürümler için repository'deki `requirements.txt` esas alınmalıdır.

---

## 15. Mimari Tasarım Kararları

### Service layer kullanımı

Karmaşık business logic'in View'larda dağılmasını önlemek için servisler kullanılır. Bu, özellikle Cart, Order, Payment, Refund, Stock Reservation ve Product Wizard gibi çok adımlı domainlerde bakım yapılabilirliğini artırır.

### Domain bazlı servis ayrımı

Orders tarafının tek bir dev servis yerine `order`, `payment`, `refund`, `shipping`, `invoice`, `stock_reservation`, `card_storage` ve `iyzico_submerchant` gibi parçalara ayrılması, ticari işlemlerin sorumluluklarını belirginleştirir.

### Database-level correctness

Unique constraint, check constraint, transaction ve row-level lock gibi mekanizmalar yalnızca uygulama koduna güvenmek yerine database seviyesinde de doğruluk sağlamaya yardımcı olur.

### Multi-vendor ayrımı

Global ürün kataloğu ile satıcının ürüne ilişkin satılabilir teklifinin ayrılması, aynı ürünün birden fazla mağaza tarafından farklı fiyat/stok ile sunulabilmesini mümkün kılar.

---

## 16. Mimari Özeti

Sistem genel olarak aşağıdaki akışı izler:

```text
                  CLIENT / BROWSER
                         │
                         ▼
                  DJANGO URL ROUTER
                         │
                         ▼
                ┌─────────────────┐
                │ Views / APIViews│
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ Domain Services │
                └────────┬────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
           ORM / DB   iyzico    Celery
              │
              ▼
             MSSQL
```

Bu yapı; Django'nun server-rendered web yeteneklerini API endpoint'leriyle birleştirirken, kritik iş kurallarını service katmanında tutan katmanlı bir multi-vendor e-ticaret mimarisi oluşturur.
