# Multi-Vendor Order Split

```mermaid
erDiagram
    ORDER ||--o{ SUBORDER : "içerir"
    STORE ||--o{ SUBORDER : "mağazaya aittir"
    SUBORDER ||--o{ ORDERITEM : "içerir"
    STORE_PRODUCT ||--o{ ORDERITEM : "satın alınır"

    ORDER {
        bigint id PK
        string order_number UK
        bigint user_id FK
        decimal subtotal
        decimal discount_amount
        decimal shipping_amount
        decimal tax_amount
        decimal total_amount
        string currency
        string status
    }

    SUBORDER {
        bigint id PK
        bigint order_id FK
        bigint store_id FK
        string suborder_number UK
        string store_name_snapshot
        decimal subtotal
        decimal discount_amount
        decimal shipping_amount
        decimal tax_amount
        decimal total_amount
        string status
        string cargo_company
        string cargo_tracking_number
    }

    ORDERITEM {
        bigint id PK
        bigint sub_order_id FK
        bigint store_product_id FK
        bigint source_cart_item_id FK
        string product_name_snapshot
        string variant_display
        string sku_snapshot
        string barcode_snapshot
        int quantity
        decimal unit_price
        decimal discount_amount
        decimal tax_amount
        decimal total_amount
    }

    STORE {
        bigint id PK
    }

    STORE_PRODUCT {
        bigint id PK
    }
```

Örnek business sonucu:

```text
Tek checkout
    ↓
Tek Order
    ├── Store A → SubOrder A → OrderItem'lar
    └── Store B → SubOrder B → OrderItem'lar
```

Aynı `Order` içinde aynı `Store` için yalnızca bir `SubOrder` bulunmasına model seviyesinde `UniqueConstraint(order, store)` ile izin verilir.
