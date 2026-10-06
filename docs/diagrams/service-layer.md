# Service Layer Diyagramı

```mermaid
flowchart LR
    V[Views / API Views]

    AS[accounts/services]
    PS[products/services]
    CS[cart/services/CartService]
    OS[orders/services]

    M[Domain Models]
    DB["(MSSQL)"]

    IY[iyzico]
    CW[Celery]

    V --> AS
    V --> PS
    V --> CS
    V --> OS

    AS --> M
    PS --> M
    CS --> M
    OS --> M

    AS --> DB
    PS --> DB
    CS --> DB
    OS --> DB

    OS --> IY
    PS --> CW
    CW --> PS
```

## Ana service grupları

### `accounts/services`

- `SellerApprovalService`

### `products/services`

- draft/create/update
- image management
- variant management
- matching
- offer creation / publishing
- storefront
- search/indexing
- review / QA / collections ile ilgili işlemler

### `cart/services`

- `CartService`

### `orders/services`

- `OrderService`
- `PaymentService`
- `CardStorageService`
- `RefundService`
- `Cancellation` servisleri
- `ShippingService`
- `InvoiceService`
- `StockReservationService`
- `IyzicoSubmerchantService`
